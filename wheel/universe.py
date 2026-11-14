"""Which tickers to scan: BTC proxies first, then the largest US-listed companies or a fixed watchlist."""

import json
from datetime import date
from pathlib import Path

from .config import ROOT

CACHE = ROOT / ".cache" / "universe.json"
SNAPSHOT = Path(__file__).with_name("universe_snapshot.json")   # shipped fallback, refreshed by hand


def _read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def top_companies(provider, cfg, today=None, log=print, cache=CACHE):
    """[{symbol, name, price, market_cap}], cached for cfg.universe_refresh_days."""
    today = today or date.today()
    cached = _read(cache)
    if cached and (today - date.fromisoformat(cached["date"])).days < cfg.universe_refresh_days \
            and len(cached["companies"]) >= cfg.top_n:
        return cached["companies"][:cfg.top_n]
    try:
        companies = provider.top_market_cap(cfg.top_n)
        if len(companies) < cfg.top_n * 0.8:
            raise RuntimeError(f"only {len(companies)} companies returned")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"date": today.isoformat(), "companies": companies}, ensure_ascii=False))
        return companies
    except Exception as e:
        stale = cached or _read(SNAPSHOT)
        if not stale:
            raise
        log(f"  market-cap ranking unavailable ({e}); using the list from {stale['date']}")
        return stale["companies"][:cfg.top_n]


def load(provider, cfg, today=None, log=print):
    """Returns (btc_tickers, stock_tickers, names). names maps ticker -> company name where known."""
    btc = list(cfg.btc_tickers)
    if cfg.universe_mode == "watchlist":
        stocks, names = list(cfg.watchlist), {}
    elif cfg.universe_mode == "top100":
        companies = top_companies(provider, cfg, today, log)
        stocks = [c["symbol"] for c in companies]
        names = {c["symbol"]: c["name"] for c in companies}
    else:
        raise ValueError(f"universe_mode must be top100 or watchlist, not {cfg.universe_mode!r}")
    return btc, [t for t in stocks if t not in btc], names
