"""Per-ticker context from daily closes: realized volatility, trend and drawdown."""

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class Metrics:
    price: float
    hv20: float            # 20-day realized volatility, annualized
    hv_rank: float         # where today's hv20 sits in its own 1-year range, 0..1
    sma50: float
    sma200: float
    above_200: bool | None  # None when there is not enough history
    chg_1d: float
    chg_5d: float
    chg_1m: float
    off_high: float        # distance below the 1-year high, <= 0
    max_dd: float          # worst peak-to-trough fall in the period, <= 0


def _chg(closes, n):
    return float(closes.iloc[-1] / closes.iloc[-1 - n] - 1) if len(closes) > n else float("nan")


def compute(closes, periods_per_year=252):
    """closes: pandas Series of daily closes, oldest first. Use periods_per_year=365 for crypto."""
    closes = closes.dropna()
    if len(closes) < 30:
        raise ValueError("need at least 30 closes")
    hv = np.log(closes).diff().rolling(20).std() * math.sqrt(periods_per_year)
    hv_now = float(hv.iloc[-1])
    hist = hv.dropna()
    lo, hi = float(hist.min()), float(hist.max())
    price = float(closes.iloc[-1])
    sma200 = float(closes.tail(200).mean()) if len(closes) >= 200 else float("nan")
    return Metrics(
        price=price, hv20=hv_now, hv_rank=(hv_now - lo) / (hi - lo) if hi > lo else 0.5,
        sma50=float(closes.tail(50).mean()), sma200=sma200,
        above_200=None if math.isnan(sma200) else price > sma200,
        chg_1d=_chg(closes, 1), chg_5d=_chg(closes, 5), chg_1m=_chg(closes, 21),
        off_high=float(price / closes.max() - 1), max_dd=float((closes / closes.cummax() - 1).min()),
    )
