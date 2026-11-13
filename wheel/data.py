"""Market data providers. Anything with the same methods as YahooProvider can be swapped in
(a moomoo OpenD provider is the planned next one)."""

from dataclasses import dataclass
import logging
import time
from datetime import date, datetime, timezone

import pandas as pd

logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # ETFs have no fundamentals; yfinance logs a 404 for each


@dataclass
class OptionQuote:
    ticker: str
    kind: str          # 'put' or 'call'
    strike: float
    expiry: date
    bid: float
    ask: float
    last: float
    iv: float          # provider-reported IV, may be unreliable outside market hours
    open_interest: int
    volume: int

    @property
    def mid(self):
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last


def _num(x, default=0):
    try:
        if x is None or pd.isna(x):
            return default
        return x
    except TypeError:
        return x


def _retry(fn, tries=3, wait=20):
    """Yahoo rate limits in bursts; back off and try again."""
    for attempt in range(tries):
        try:
            return fn()
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(wait * (attempt + 1))


US_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "ASE"}   # Nasdaq, NYSE, NYSE American; drops OTC listings


def pick_top_companies(quotes, n):
    """From Yahoo screener rows sorted by market cap: exchange-listed common shares,
    one share class per company (the cheaper one, so BRK-B rather than BRK-A)."""
    best = {}
    for q in quotes:
        sym = q.get("symbol", "")
        if q.get("exchange") not in US_EXCHANGES or q.get("quoteType") != "EQUITY":
            continue
        if "-P" in sym or not q.get("regularMarketPrice"):   # preferred shares
            continue
        name = q.get("longName") or q.get("shortName") or sym
        row = {"symbol": sym, "name": name, "price": float(q["regularMarketPrice"]),
               "market_cap": float(q.get("marketCap") or 0)}
        if name not in best:
            best[name] = row
        elif row["price"] < best[name]["price"]:
            row["market_cap"] = max(row["market_cap"], best[name]["market_cap"])
            best[name] = row
    return sorted(best.values(), key=lambda r: r["market_cap"], reverse=True)[:n]


class YahooProvider:
    """Free, roughly 15-minute delayed data via yfinance."""

    def __init__(self):
        import yfinance as yf
        self._yf = yf
        self._tickers = {}

    def _t(self, ticker):
        if ticker not in self._tickers:
            self._tickers[ticker] = self._yf.Ticker(ticker)
        return self._tickers[ticker]

    def price(self, ticker):
        closes = self._t(ticker).history(period="5d")["Close"].dropna()  # today's row can be NaN pre-market
        if closes.empty:
            raise RuntimeError(f"No price data for {ticker}")
        return float(closes.iloc[-1])

    def expirations(self, ticker, retries=3, wait=30):
        # Yahoo answers a rate-limited request with an empty list, which would look like "no options"
        for attempt in range(retries):
            dates = self._t(ticker).options
            if dates:
                return [datetime.strptime(s, "%Y-%m-%d").date() for s in dates]
            if attempt < retries - 1:
                time.sleep(wait)
                self._tickers.pop(ticker, None)
        raise RuntimeError(f"No option expirations for {ticker} (Yahoo may be rate limiting)")

    def chain(self, ticker, expiry, kind):
        oc = _retry(lambda: self._t(ticker).option_chain(expiry.strftime("%Y-%m-%d")))
        df = oc.puts if kind == "put" else oc.calls
        return [
            OptionQuote(
                ticker=ticker, kind=kind, strike=float(r.strike), expiry=expiry,
                bid=float(_num(r.bid)), ask=float(_num(r.ask)), last=float(_num(r.lastPrice)),
                iv=float(_num(r.impliedVolatility)),
                open_interest=int(_num(r.openInterest)), volume=int(_num(r.volume)),
            )
            for r in df.itertuples()
        ]

    def next_earnings(self, ticker, retry_wait=10):
        """The earnings date Yahoo has on file, or None when it has none or the lookup failed (it
        fails quietly when rate limited, so callers must not read None as 'no earnings'). Right
        after a report the date on file is the one just past, until the next is announced."""
        for attempt in (1, 2):
            t = self._t(ticker)
            try:
                cal = t.calendar
                dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
                if dates:
                    d = dates[0]
                    return d.date() if isinstance(d, datetime) else d
            except Exception:
                pass
            try:   # the quote summary carries the same date and is served by a different endpoint
                info = t.info
                ts = info.get("earningsTimestampStart") or info.get("earningsTimestamp")
                if ts:
                    return datetime.fromtimestamp(ts, timezone.utc).date()
            except Exception:
                pass
            if attempt == 1:
                time.sleep(retry_wait)
                self._tickers.pop(ticker, None)
        return None

    def is_fund(self, ticker):
        """True for ETFs and other funds, which have no earnings."""
        try:
            return str(self._t(ticker).fast_info["quoteType"]).upper() in ("ETF", "MUTUALFUND")
        except Exception:
            return False

    def histories(self, tickers, period="1y"):
        """Daily closes for many tickers in a few requests: {ticker: Series}. Missing tickers are left out."""
        out = {}
        tickers = list(dict.fromkeys(tickers))
        for i in range(0, len(tickers), 50):
            batch = tickers[i:i + 50]
            df = _retry(lambda: self._yf.download(batch, period=period, auto_adjust=True, group_by="ticker",
                                                  progress=False, threads=True))
            for t in batch:
                try:
                    s = (df[t]["Close"] if isinstance(df.columns, pd.MultiIndex) else df["Close"]).dropna()
                except KeyError:
                    continue
                if len(s):
                    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                    out[t] = s.astype(float)
        return out

    def top_market_cap(self, n=100):
        """The n largest US-listed companies: [{symbol, name, price, market_cap}]."""
        from yfinance import EquityQuery as Q
        query = Q("and", [Q("eq", ["region", "us"]), Q("gt", ["intradaymarketcap", 20_000_000_000])])
        quotes = []
        for offset in (0, 100, 200, 300):
            page = _retry(lambda: self._yf.screen(query, sortField="intradaymarketcap", sortAsc=False,
                                                  size=100, offset=offset))
            quotes += page.get("quotes", [])
            if len(pick_top_companies(quotes, n)) >= n:
                break
        return pick_top_companies(quotes, n)

    def history(self, ticker, start, end=None):
        hist = self._t(ticker).history(start=start, end=end, auto_adjust=True)
        if hist.empty:
            raise RuntimeError(f"No history for {ticker}")
        s = hist["Close"]
        s.index = s.index.tz_localize(None).normalize()
        return s


def load_csv_history(path):
    """CSV with Date and Close (or Adj Close) columns, e.g. exported from Yahoo or moomoo."""
    df = pd.read_csv(path, parse_dates=["Date"]).set_index("Date").sort_index()
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    return df[col].astype(float)
