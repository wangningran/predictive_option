"""Find option-selling ideas that pass the entry rules.

Per ticker: a cash-secured put when the account can afford 100 shares at the strike, otherwise a
put credit spread whose worst-case loss fits cfg.max_loss_per_spread. Covered calls are offered on
shares already held.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import bs, research

CSP, PUT_SPREAD, COVERED_CALL = "csp", "put_spread", "covered_call"


@dataclass
class Idea:
    ticker: str
    strategy: str
    price: float
    strike: float          # the short strike
    long_strike: float     # 0 unless a spread
    expiry: date
    dte: int
    credit: float          # per share at mid prices (net of the long leg for spreads)
    natural: float         # per share if you sell at the bid and buy at the ask
    delta: float           # of the short leg
    iv: float
    open_interest: int
    spread_pct: float      # short leg (ask - bid) / mid
    premium: float         # dollars per contract after fees
    capital: float         # dollars tied up: strike x 100 for a CSP, the max loss for a spread
    max_loss: float        # dollars; for a CSP this is the stock going to zero
    ret: float             # premium / capital
    annualized: float
    breakeven: float
    cushion: float         # how far price can fall before breakeven, as a fraction
    earnings: date | None = None
    earnings_unknown: bool = False
    atm_iv: float | None = None
    iv_hv: float | None = None       # at-the-money IV / 20-day realized vol
    metrics: research.Metrics | None = None
    score: float = 0.0
    flags: list = field(default_factory=list)

    @property
    def width(self):
        return self.strike - self.long_strike if self.long_strike else 0.0

    @property
    def win_prob(self):
        """Rough chance the short strike is not breached at expiry."""
        return 1 - abs(self.delta)


@dataclass
class Leg:
    quote: object
    mid: float
    iv: float
    delta: float
    spread_pct: float


def _leg(q, S, dte, cfg):
    """Price a quote, or None when it has no two-sided market."""
    if q.bid <= 0 or q.ask <= 0 or dte <= 0:
        return None
    mid = (q.bid + q.ask) / 2
    T = dte / 365
    iv = bs.implied_vol(q.kind, mid, S, q.strike, T, cfg.risk_free_rate) or q.iv
    if not iv or iv <= 0:
        return None
    return Leg(q, mid, iv, bs.delta(q.kind, S, q.strike, T, cfg.risk_free_rate, iv), (q.ask - q.bid) / mid)


def _sellable(leg, cfg):
    return (cfg.delta_min <= abs(leg.delta) <= cfg.delta_max and leg.spread_pct <= cfg.max_spread_pct
            and leg.quote.open_interest >= cfg.min_open_interest)


def _csp(leg, S, dte, cfg):
    q = leg.quote
    premium = leg.mid * 100 - cfg.fee_per_contract
    capital = q.strike * 100
    breakeven = q.strike - leg.mid
    return Idea(
        ticker=q.ticker, strategy=CSP, price=S, strike=q.strike, long_strike=0.0, expiry=q.expiry, dte=dte,
        credit=leg.mid, natural=q.bid, delta=leg.delta, iv=leg.iv, open_interest=q.open_interest,
        spread_pct=leg.spread_pct, premium=premium, capital=capital, max_loss=capital - premium,
        ret=premium / capital, annualized=premium / capital * 365 / dte,
        breakeven=breakeven, cushion=(S - breakeven) / S,
    )


def _put_spread(short, long, S, dte, cfg):
    """Sell `short`, buy the lower-strike `long`. None when it breaks the spread rules."""
    qs, ql = short.quote, long.quote
    width = qs.strike - ql.strike
    credit = short.mid - long.mid
    natural = qs.bid - ql.ask
    if width <= 0 or credit <= 0 or natural < cfg.min_natural_credit_pct * credit:
        return None
    if credit / width < cfg.min_credit_pct:
        return None
    fees = 2 * cfg.fee_per_contract
    premium = credit * 100 - fees
    max_loss = (width - credit) * 100 + fees
    if premium <= 0 or max_loss > cfg.max_loss_per_spread:
        return None
    breakeven = qs.strike - credit
    return Idea(
        ticker=qs.ticker, strategy=PUT_SPREAD, price=S, strike=qs.strike, long_strike=ql.strike,
        expiry=qs.expiry, dte=dte, credit=credit, natural=natural, delta=short.delta, iv=short.iv,
        open_interest=qs.open_interest, spread_pct=short.spread_pct, premium=premium, capital=max_loss,
        max_loss=max_loss, ret=premium / max_loss, annualized=premium / max_loss * 365 / dte,
        breakeven=breakeven, cushion=(S - breakeven) / S,
    )


def _expiries(provider, ticker, today, cfg):
    exps = [e for e in provider.expirations(ticker) if cfg.dte_min <= (e - today).days <= cfg.dte_max]
    exps.sort(key=lambda e: abs((e - today).days - cfg.target_dte))
    return exps[:cfg.max_expiries]


QUARTER_DAYS = 80   # earnings come roughly every 91 days; assume the early side


def _earnings(provider, ticker, is_fund, today, cfg):
    """(next earnings date or None, unknown). Funds have none. A date just past means the company
    has reported and not yet scheduled the next one, so estimate it a quarter on. With no usable
    date we cannot tell whether an expiry spans earnings: refuse unless the user opted out."""
    if is_fund:
        return None, False
    e = provider.next_earnings(ticker)
    if e and e >= today:
        return e, False
    if e and (today - e).days < QUARTER_DAYS:   # older than that and the estimate itself is in the past
        return e + timedelta(days=QUARTER_DAYS), False
    if provider.is_fund(ticker):
        return None, False
    if cfg.avoid_earnings:
        raise LookupError("no upcoming earnings date found")
    return None, True


def screen_ticker(provider, cfg, ticker, cash_available, today=None, price=None, is_fund=False):
    """All put-selling ideas for one ticker. Raises on data errors."""
    today = today or date.today()
    S = price if price is not None else provider.price(ticker)
    earnings, unknown = _earnings(provider, ticker, is_fund, today, cfg)
    csp_limit = min(cash_available, cfg.account_size * cfg.max_ticker_pct)
    ideas, atm_iv = [], None
    for exp in _expiries(provider, ticker, today, cfg):
        if cfg.avoid_earnings and earnings and today <= earnings <= exp:
            continue
        dte = (exp - today).days
        legs = [leg for q in provider.chain(ticker, exp, "put") if (leg := _leg(q, S, dte, cfg))]
        legs.sort(key=lambda leg: leg.quote.strike)
        if atm_iv is None and legs:
            atm_iv = min(legs, key=lambda leg: abs(leg.quote.strike - S)).iv
        shorts = [leg for leg in legs if leg.quote.strike < S and _sellable(leg, cfg)]
        found = [_csp(leg, S, dte, cfg) for leg in shorts if leg.quote.strike * 100 <= csp_limit]
        if not found:   # 100 shares cost too much: cap the risk with a long put instead
            for short in shorts:
                spreads = [sp for long in legs if long.quote.strike < short.quote.strike
                           and (sp := _put_spread(short, long, S, dte, cfg)) and sp.max_loss <= cash_available]
                if spreads:   # widest spread the loss budget allows: least sensitive to the long leg's price
                    found.append(max(spreads, key=lambda sp: sp.width))
        ideas += found
    for idea in ideas:
        idea.earnings, idea.earnings_unknown, idea.atm_iv = earnings, unknown, atm_iv
    return ideas


def _score(idea):
    """CSPs: annualized return, marked up when options are rich versus realized vol and down in a
    downtrend. Spreads all pay about the same for a given delta, and what they pay is roughly the
    fair price of the risk, so they are ranked on richness and trend alone."""
    m, flags = idea.metrics, []
    score = 1.0 if idea.strategy == PUT_SPREAD else idea.annualized
    if m and idea.atm_iv and m.hv20 > 0:
        idea.iv_hv = idea.atm_iv / m.hv20
        score *= min(max(idea.iv_hv, 0.7), 1.5)
        if idea.iv_hv < 1:
            flags.append("IV below realized")
    if m and m.above_200 is False:
        score *= 0.75
        flags.append("below 200-day avg")
    if m and m.chg_1m == m.chg_1m and m.chg_1m < -0.15:
        score *= 0.75
        flags.append(f"down {-m.chg_1m:.0%} in a month")
    if idea.earnings_unknown:
        flags.append("earnings date unknown")
    idea.score, idea.flags = score, flags
    return idea


def best_per_ticker(ideas, cfg):
    """One idea per ticker: the short delta closest to cfg.target_delta."""
    best = {}
    for i in ideas:
        key = (abs(abs(i.delta) - cfg.target_delta), -i.annualized)
        if i.ticker not in best or key < best[i.ticker][0]:
            best[i.ticker] = (key, i)
    return [i for _, i in best.values()]


@dataclass
class Scan:
    ideas: list            # every passing idea, scored
    metrics: dict          # ticker -> research.Metrics
    errors: dict           # ticker -> message


def scan(provider, cfg, tickers, cash_available, today=None, funds=(), workers=4, retry_wait=0):
    """Screen many tickers. Price history comes in one batch; chains are fetched a few at a time,
    and tickers that fail (usually rate limits) get one more try, one by one, after retry_wait seconds."""
    today = today or date.today()
    histories = provider.histories(tickers)
    metrics, errors = {}, {}
    for t in tickers:
        try:
            metrics[t] = research.compute(histories[t])
        except (KeyError, ValueError):
            errors[t] = "no price history"

    def one(t):
        try:
            return t, screen_ticker(provider, cfg, t, cash_available, today, metrics[t].price, t in funds), None
        except Exception as e:
            return t, [], str(e) or type(e).__name__

    ideas, todo = [], [t for t in tickers if t in metrics]
    for attempt in (1, 2):
        if attempt == 2:
            time.sleep(retry_wait)
        with ThreadPoolExecutor(workers if attempt == 1 else 1) as pool:
            results = list(pool.map(one, todo))
        todo = []
        for t, found, err in results:
            if err and attempt == 1:
                todo.append(t)
            elif err:
                errors[t] = err
            ideas += found
        if not todo:
            break
    for i in ideas:
        i.metrics = metrics[i.ticker]
        _score(i)
    return Scan(sorted(ideas, key=lambda i: i.score, reverse=True), metrics, errors)


def screen_calls(provider, cfg, positions, today=None, log=print):
    """Covered calls for every ticker where you hold at least 100 uncovered shares.
    positions: {ticker: {"shares", "avg_cost", "uncovered"}}"""
    today = today or date.today()
    out = []
    for ticker, p in positions.items():
        if p.get("uncovered", p["shares"]) // 100 < 1:
            continue
        try:
            S = provider.price(ticker)
            earnings = provider.next_earnings(ticker)
            for exp in _expiries(provider, ticker, today, cfg):
                if cfg.avoid_earnings and earnings and today <= earnings <= exp:
                    continue
                dte = (exp - today).days
                for q in provider.chain(ticker, exp, "call"):
                    if q.strike <= S or (cfg.calls_above_cost_basis and q.strike < p["avg_cost"]):
                        continue
                    leg = _leg(q, S, dte, cfg)
                    if not leg or not _sellable(leg, cfg):
                        continue
                    premium = leg.mid * 100 - cfg.fee_per_contract
                    out.append(Idea(
                        ticker=ticker, strategy=COVERED_CALL, price=S, strike=q.strike, long_strike=0.0,
                        expiry=exp, dte=dte, credit=leg.mid, natural=q.bid, delta=leg.delta, iv=leg.iv,
                        open_interest=q.open_interest, spread_pct=leg.spread_pct, premium=premium,
                        capital=S * 100, max_loss=S * 100 - premium, ret=premium / (S * 100),
                        annualized=premium / (S * 100) * 365 / dte, breakeven=q.strike + leg.mid,
                        cushion=(q.strike - S) / S, earnings=earnings,
                    ))
        except Exception as e:
            log(f"  {ticker}: fetch failed ({e})")
    return sorted(out, key=lambda c: c.annualized, reverse=True)
