"""Check open positions against the management rules and produce alerts."""

import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import date


@dataclass
class Alert:
    level: str        # ACTION / WARN / INFO
    trade_id: int | None
    ticker: str
    message: str

    def __str__(self):
        ref = f"#{self.trade_id} " if self.trade_id else ""
        return f"[{self.level}] {ref}{self.ticker}: {self.message}"


def _mid(provider, trade):
    """Current cost per share to close: the option's mid, or short minus long for a spread."""
    mids = {q.strike: q.mid for q in provider.chain(trade.ticker, trade.expiry_date, trade.kind)}

    def at(strike):
        return next((m for k, m in mids.items() if abs(k - strike) < 1e-6), None)

    short = at(trade.strike)
    if short is None or not trade.is_spread:
        return short
    long = at(trade.long_strike)
    return None if long is None else max(short - long, 0.0)


def check(provider, journal, cfg, today=None):
    """Returns (alerts, rows). rows hold per-position numbers for display."""
    today = today or date.today()
    alerts, rows = [], []
    prices = {}

    def price(t):
        if t not in prices:
            prices[t] = provider.price(t)
        return prices[t]

    for t in journal.open_trades():
        dte = (t.expiry_date - today).days
        if dte < 0:
            other = (f"`log close {t.id} --price NET_DEBIT` (in the money)" if t.is_spread
                     else f"`log assign {t.id}` (assigned)")
            alerts.append(Alert("ACTION", t.id, t.ticker,
                                f"expired {t.expiry}; record the outcome: `log expire {t.id}` (worthless) or {other}"))
            continue
        try:
            S = price(t.ticker)
            mid = _mid(provider, t)
        except Exception as e:
            alerts.append(Alert("WARN", t.id, t.ticker, f"quote fetch failed ({e})"))
            continue
        captured = (t.premium - mid) / t.premium if mid is not None else None
        itm = S < t.strike if t.kind == "put" else S > t.strike
        dist = (S - t.strike) / S if t.kind == "put" else (t.strike - S) / S
        rows.append({"id": t.id, "ticker": t.ticker, "kind": t.kind, "strike": t.strike, "expiry": t.expiry,
                     "label": t.label,
                     "dte": dte, "price": S, "premium": t.premium, "mid": mid, "captured": captured, "itm": itm})
        label = t.label

        if captured is not None and captured >= cfg.take_profit:
            alerts.append(Alert("ACTION", t.id, t.ticker,
                                f"{label} has captured {captured:.0%} of the premium; buy to close at about ${mid:.2f}"))
        elif mid is not None and (mid - t.premium) >= cfg.loss_multiple * t.premium:
            alerts.append(Alert("ACTION", t.id, t.ticker,
                                f"{label} loss is {(mid - t.premium) / t.premium:.1f}x the premium; close or roll per your stop rule"))
        elif dte <= cfg.roll_dte:
            alerts.append(Alert("WARN", t.id, t.ticker,
                                f"{label} has {dte} days left and has captured only "
                                f"{(captured or 0):.0%}; consider closing or rolling"))
        if t.is_spread and S < t.long_strike:
            alerts.append(Alert("ACTION", t.id, t.ticker,
                                f"{label} both legs in the money (price ${S:.2f}), near maximum loss; close before expiry to avoid assignment"))
        elif itm:
            what = ("close or roll before expiry, or the short leg will be assigned" if t.is_spread else
                    "assignment is likely, be ready to own the shares" if t.kind == "put"
                    else "the shares are likely to be called away")
            alerts.append(Alert("WARN", t.id, t.ticker, f"{label} is in the money (price ${S:.2f}); {what}"))
        elif dist < cfg.near_strike_pct:
            alerts.append(Alert("WARN", t.id, t.ticker, f"{label}: price ${S:.2f} is only {dist:.1%} from the strike"))
        try:
            e = provider.next_earnings(t.ticker)
        except Exception:
            e = None
        if e and today <= e <= t.expiry_date:
            alerts.append(Alert("WARN", t.id, t.ticker, f"earnings on {e} fall before expiry {t.expiry}; watch for a gap"))

    # portfolio-level checks
    exposure = {}
    for t in journal.open_trades():
        exposure[t.ticker] = exposure.get(t.ticker, 0.0) + t.collateral
    for ticker, p in journal.positions().items():
        if p["shares"]:
            try:
                exposure[ticker] = exposure.get(ticker, 0.0) + p["shares"] * price(ticker)
            except Exception:
                exposure[ticker] = exposure.get(ticker, 0.0) + p["shares"] * p["avg_cost"]
    committed = sum(exposure.values())
    limit = cfg.account_size * (1 - cfg.cash_reserve_pct)
    if committed > limit:
        alerts.append(Alert("WARN", None, "PORTFOLIO",
                            f"${committed:,.0f} committed, above the ${limit:,.0f} limit (cash reserve {cfg.cash_reserve_pct:.0%})"))
    for ticker, amount in exposure.items():
        if amount > cfg.account_size * cfg.max_ticker_pct:
            alerts.append(Alert("WARN", None, ticker,
                                f"${amount:,.0f} exposure is {amount / cfg.account_size:.0%} of the account (limit {cfg.max_ticker_pct:.0%})"))
    return alerts, rows


def notify_telegram(text):
    """Send text to Telegram if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set. Returns True when sent."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=15)
    return True
