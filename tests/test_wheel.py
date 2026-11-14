import math
from datetime import date, timedelta

import pytest

from wheel import bs, backtest as bt
from wheel.config import Config, load
from wheel.data import OptionQuote
from wheel.journal import Journal
from wheel.monitor import check
from wheel import research, universe
from wheel.data import pick_top_companies
from wheel.screener import CSP, PUT_SPREAD, best_per_ticker, scan, screen_calls, screen_ticker

TODAY = date(2026, 10, 13)
R = 0.04


class FakeProvider:
    """Chain priced with Black-Scholes at a flat IV, spread of 4% around the model price."""

    def __init__(self, prices, iv=0.35, earnings=None):
        self.prices, self.iv, self.earnings = prices, iv, earnings or {}

    def price(self, t):
        return self.prices[t]

    def expirations(self, t):
        return [TODAY + timedelta(days=d) for d in (7, 30, 37, 60)]

    def chain(self, t, expiry, kind):
        S = self.prices[t]
        T = (expiry - TODAY).days / 365
        out = []
        k = math.floor(S * 0.7)
        while k <= S * 1.3:
            mid = bs.price(kind, S, k, T, R, self.iv)
            if mid > 0.02:
                out.append(OptionQuote(t, kind, float(k), expiry, mid * 0.98, mid * 1.02, mid, self.iv, 500, 50))
            k += 1
        return out

    def next_earnings(self, t):
        return self.earnings.get(t, TODAY + timedelta(days=200))

    def is_fund(self, t):
        return False

    def histories(self, tickers, period="1y"):
        base = bt.synthetic_prices(days=260, seed=5)
        return {t: base / base.iloc[-1] * self.prices[t] for t in tickers if t in self.prices}


def test_put_call_parity_and_iv_roundtrip():
    S, K, T, sig = 50, 48, 0.1, 0.3
    c, p = bs.price("call", S, K, T, R, sig), bs.price("put", S, K, T, R, sig)
    assert c - p == pytest.approx(S - K * math.exp(-R * T), abs=1e-9)
    assert bs.implied_vol("put", p, S, K, T, R) == pytest.approx(sig, abs=1e-4)
    assert -1 < bs.delta("put", S, K, T, R, sig) < 0 < bs.delta("call", S, K, T, R, sig) < 1


def test_strike_for_delta_is_otm_and_close_to_target():
    S, T, sig = 40, 35 / 365, 0.4
    kp = bs.strike_for_delta("put", S, T, R, sig, 0.25)
    kc = bs.strike_for_delta("call", S, T, R, sig, 0.25)
    assert kp < S < kc
    assert abs(bs.delta("put", S, kp, T, R, sig)) <= 0.25 + 1e-9
    assert abs(bs.delta("put", S, kp + 0.5, T, R, sig)) > 0.2


def test_journal_wheel_cycle(tmp_path):
    j = Journal(tmp_path / "j.csv", tmp_path / "l.csv")
    put = j.open("sofi", "put", 20, "2026-11-20", 2, 0.50, 1.30, on=TODAY)
    with pytest.raises(ValueError):
        j.open("SOFI", "call", 22, "2026-12-18", 1, 0.40, 0.65)  # no shares yet: naked
    j.assign(put.id)
    assert put.realized_pnl == pytest.approx(0.5 * 200 - 1.30)
    assert j.positions()["SOFI"]["shares"] == 200
    call = j.open("SOFI", "call", 21, "2026-12-18", 2, 0.40, 1.30)
    j.assign(call.id)
    j.save()

    j2 = Journal(tmp_path / "j.csv", tmp_path / "l.csv")  # round-trips through CSV
    pos = j2.positions()["SOFI"]
    assert pos["shares"] == 0
    assert pos["realized"] == pytest.approx(200 * (21 - 20))
    s = j2.stats()
    assert s["closed_trades"] == 2 and s["assignments"] == 2 and s["win_rate"] == 1.0
    assert s["option_pnl"] == pytest.approx(100 - 1.3 + 80 - 1.3)


def test_journal_close_and_expire(tmp_path):
    j = Journal(tmp_path / "j.csv", tmp_path / "l.csv")
    a = j.open("F", "put", 11, "2026-11-20", 1, 0.30, 0.65, on=TODAY)
    b = j.open("F", "put", 10, "2026-11-20", 1, 0.20, 0.65, on=TODAY)
    j.close(a.id, 0.15, 0.65)
    j.expire(b.id)
    assert a.realized_pnl == pytest.approx(15 - 1.30)
    assert b.realized_pnl == pytest.approx(20 - 0.65)
    with pytest.raises(ValueError):
        j.expire(b.id)





def test_monitor_alerts(tmp_path):
    cfg = Config(account_size=10_000)
    j = Journal(tmp_path / "j.csv", tmp_path / "l.csv")
    exp = (TODAY + timedelta(days=30)).isoformat()
    j.open("ABC", "put", 25, exp, 1, 2.00, 0.65, on=TODAY)       # far OTM now, mostly decayed -> take profit
    j.open("XYZ", "put", 30, exp, 1, 0.50, 0.65, on=TODAY)       # deep ITM -> loss + ITM
    j.open("ABC", "put", 20, "2026-10-01", 1, 0.40, 0.65)       # already expired
    prov = FakeProvider({"ABC": 30.0, "XYZ": 24.0})
    alerts, rows = check(prov, j, cfg, today=TODAY)
    assert any(a.trade_id == 1 and "has captured" in a.message for a in alerts)
    assert any(a.trade_id == 2 and "in the money" in a.message for a in alerts)
    assert any(a.trade_id == 2 and "x the premium" in a.message for a in alerts)
    assert any(a.trade_id == 3 and "expired" in a.message for a in alerts)
    assert len(rows) == 2


def test_backtest_runs_and_stays_cash_secured():
    closes = bt.synthetic_prices(days=800, start=40, seed=1, crash_at=400, crash=-0.25)
    res = bt.run(closes, bt.Params(initial_cash=10_000))
    m = res.metrics
    assert m["option_trades"] > 10
    assert (res.equity > 0).all()
    assert m["max_drawdown"] < 0
    assert set(res.trades["outcome"]) <= {"closed", "expired", "assigned"}
    # never more contracts than cash (puts) or shares (calls) can cover
    puts = res.trades[res.trades["kind"] == "put"]
    assert (puts["strike"] * 100 * puts["n"] <= 10_000 * 1.5).all()


def test_backtest_unaffordable_underlying_does_nothing():
    closes = bt.synthetic_prices(days=300, start=500, seed=2)
    res = bt.run(closes, bt.Params(initial_cash=10_000))
    assert res.metrics["option_trades"] == 0
    assert res.equity.iloc[-1] == pytest.approx(10_000)


def test_shipped_config_loads():
    cfg = load()
    assert cfg.account_size == 10_000 and len(cfg.watchlist) > 3


def test_csp_ideas_respect_rules():
    cfg = Config()
    ideas = screen_ticker(FakeProvider({"ABC": 30.0}), cfg, "ABC", 8000, today=TODAY)
    assert ideas
    for i in ideas:
        assert i.strategy == CSP and i.long_strike == 0
        assert cfg.dte_min <= i.dte <= cfg.dte_max            # 7 and 60 DTE excluded
        assert cfg.delta_min <= abs(i.delta) <= cfg.delta_max
        assert i.strike < i.price and i.capital == i.strike * 100 <= 5000
        assert i.breakeven == pytest.approx(i.strike - i.credit)


def test_expensive_stock_gets_spreads_within_loss_budget():
    cfg = Config()
    ideas = screen_ticker(FakeProvider({"BIG": 400.0}), cfg, "BIG", 8000, today=TODAY)
    assert ideas
    for i in ideas:
        assert i.strategy == PUT_SPREAD and 0 < i.long_strike < i.strike < i.price
        assert i.max_loss <= cfg.max_loss_per_spread and i.capital == i.max_loss
        assert i.max_loss == pytest.approx((i.width - i.credit) * 100 + 2 * cfg.fee_per_contract)
        assert i.premium == pytest.approx(i.credit * 100 - 2 * cfg.fee_per_contract)
        assert i.credit / i.width >= cfg.min_credit_pct
        assert 0 < i.natural <= i.credit
        assert cfg.delta_min <= abs(i.delta) <= cfg.delta_max
    assert screen_ticker(FakeProvider({"BIG": 400.0}), cfg, "BIG", 100, today=TODAY) == []  # no cash, no ideas


def test_expiries_through_earnings_are_skipped():
    prov = FakeProvider({"ABC": 30.0}, earnings={"ABC": TODAY + timedelta(days=33)})
    ideas = screen_ticker(prov, Config(), "ABC", 8000, today=TODAY)
    assert ideas and all(i.dte < 33 for i in ideas)


def test_stock_without_an_earnings_date_is_refused():
    for missing in (None, TODAY - timedelta(days=120)):          # nothing on file, or a date too old to project
        prov = FakeProvider({"ABC": 30.0}, earnings={"ABC": missing})
        with pytest.raises(LookupError):
            screen_ticker(prov, Config(), "ABC", 8000, today=TODAY)
        assert scan(prov, Config(), ["ABC"], 8000, today=TODAY).errors == {"ABC": "no upcoming earnings date found"}
        flagged = screen_ticker(prov, Config(avoid_earnings=False), "ABC", 8000, today=TODAY)
        assert flagged and all(i.earnings_unknown for i in flagged)
        fund = screen_ticker(prov, Config(), "ABC", 8000, today=TODAY, is_fund=True)
        assert fund and not any(i.earnings_unknown for i in fund)


def test_just_reported_stock_gets_an_estimated_next_date():
    prov = FakeProvider({"ABC": 30.0}, earnings={"ABC": TODAY - timedelta(days=10)})
    ideas = screen_ticker(prov, Config(), "ABC", 8000, today=TODAY)
    assert ideas and all(i.earnings == TODAY + timedelta(days=70) and not i.earnings_unknown for i in ideas)


def test_scan_scores_and_picks_one_per_ticker():
    cfg = Config()
    result = scan(FakeProvider({"ABC": 30.0, "BIG": 400.0}), cfg, ["ABC", "BIG", "NOPE"], 8000, today=TODAY)
    assert result.errors == {"NOPE": "no price history"}
    assert all(i.metrics is result.metrics[i.ticker] and i.iv_hv and i.score > 0 for i in result.ideas)
    picks = best_per_ticker(result.ideas, cfg)
    assert sorted(i.ticker for i in picks) == ["ABC", "BIG"]
    for pick in picks:
        gaps = [abs(abs(i.delta) - cfg.target_delta) for i in result.ideas if i.ticker == pick.ticker]
        assert abs(abs(pick.delta) - cfg.target_delta) == pytest.approx(min(gaps))


def test_scan_retries_a_failed_ticker_once():
    class Flaky(FakeProvider):
        calls = 0

        def expirations(self, t):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RuntimeError("rate limited")
            return super().expirations(t)

    class Dead(FakeProvider):
        def expirations(self, t):
            raise RuntimeError("rate limited")

    ok = scan(Flaky({"ABC": 30.0}), Config(), ["ABC"], 8000, today=TODAY)
    assert ok.ideas and not ok.errors
    bad = scan(Dead({"ABC": 30.0}), Config(), ["ABC"], 8000, today=TODAY)
    assert not bad.ideas and bad.errors == {"ABC": "rate limited"}


def test_screen_calls_above_cost_basis():
    cfg = Config()
    prov = FakeProvider({"ABC": 30.0})
    cands = screen_calls(prov, cfg, {"ABC": {"shares": 100, "avg_cost": 32.0, "uncovered": 100}}, today=TODAY,
                         log=lambda *_: None)
    assert cands and all(c.strike >= 32 for c in cands)
    assert screen_calls(prov, cfg, {"ABC": {"shares": 100, "avg_cost": 25, "uncovered": 0}}, today=TODAY) == []


def test_research_metrics():
    up = bt.synthetic_prices(days=300, mu=0.6, sigma=0.2, seed=3)
    m = research.compute(up)
    assert m.price == pytest.approx(up.iloc[-1]) and m.above_200 and 0 <= m.hv_rank <= 1
    assert m.off_high <= 0 and m.max_dd <= m.off_high
    assert 0.1 < m.hv20 < 0.35
    assert research.compute(up, periods_per_year=365).hv20 == pytest.approx(m.hv20 * math.sqrt(365 / 252))
    assert research.compute(up.tail(60)).above_200 is None
    with pytest.raises(ValueError):
        research.compute(up.tail(10))


def test_pick_top_companies_filters_and_dedupes():
    def row(sym, price, cap, exch="NMS", name=None, kind="EQUITY"):
        return {"symbol": sym, "regularMarketPrice": price, "marketCap": cap, "exchange": exch,
                "longName": name or sym, "quoteType": kind}
    rows = [row("BRK-A", 800000, 900, "NYQ", "Berkshire"), row("BRK-B", 590, 900, "NYQ", "Berkshire"),
            row("AAA", 10, 800), row("OTCXF", 5, 700, "PNK"), row("JPM-PC", 22, 600, "NYQ"),
            row("FUND", 50, 500, kind="ETF"), row("BBB", 20, 400), row("CCC", 30, 300)]
    assert [c["symbol"] for c in pick_top_companies(rows, 3)] == ["BRK-B", "AAA", "BBB"]


def test_universe_falls_back_when_the_ranking_fails(tmp_path):
    class Down:
        def top_market_cap(self, n):
            raise RuntimeError("401")

    class Up:
        def top_market_cap(self, n):
            return [{"symbol": f"T{i}", "name": f"Co {i}", "price": 10.0, "market_cap": 1e9} for i in range(n)]

    cfg, logged, cache = Config(top_n=5), [], tmp_path / "u.json"
    assert len(universe.top_companies(Down(), cfg, TODAY, logged.append, cache)) == 5   # shipped snapshot
    assert logged
    fresh = universe.top_companies(Up(), cfg, TODAY, logged.append, cache)
    assert [c["symbol"] for c in fresh] == ["T0", "T1", "T2", "T3", "T4"]
    assert universe.top_companies(Down(), cfg, TODAY + timedelta(days=3), logged.append, cache) == fresh  # cache
    assert universe.top_companies(Down(), cfg, TODAY + timedelta(days=30), logged.append, cache) == fresh  # stale


def test_journal_put_spread(tmp_path):
    j = Journal(tmp_path / "j.csv", tmp_path / "l.csv")
    with pytest.raises(ValueError):
        j.open("MSFT", "put", 495, "2026-12-18", 1, 1.08, 1.30, long_strike=500)   # long leg must be lower
    t = j.open("MSFT", "put", 495, "2026-12-18", 1, 1.08, 1.30, on=TODAY, long_strike=490)
    assert t.is_spread and t.collateral == 500 and t.label == "put 495/490 2026-12-18"
    assert j.stats()["collateral"] == 500
    with pytest.raises(ValueError):
        j.assign(t.id)
    j.save()
    j2 = Journal(tmp_path / "j.csv", tmp_path / "l.csv")
    assert j2.get(t.id).long_strike == 490
    j2.close(t.id, 5.0, 1.30)                                  # both legs in the money: pay the full width
    assert j2.get(t.id).realized_pnl == pytest.approx((1.08 - 5.0) * 100 - 2.60)


def test_journal_reads_files_written_before_spreads(tmp_path):
    (tmp_path / "j.csv").write_text(
        "id,open_date,ticker,kind,strike,expiry,contracts,premium,open_fees,close_date,close_price,close_fees,"
        "status,notes\n1,2026-10-13,SOFI,put,20.0,2026-11-20,1,0.55,0.65,,0.0,0.0,OPEN,\n")
    t = Journal(tmp_path / "j.csv", tmp_path / "l.csv").get(1)
    assert t.long_strike == 0 and not t.is_spread and t.collateral == 2000


def test_monitor_values_a_spread_as_short_minus_long(tmp_path):
    cfg = Config()
    j = Journal(tmp_path / "j.csv", tmp_path / "l.csv")
    exp = (TODAY + timedelta(days=30)).isoformat()
    j.open("BIG", "put", 380, exp, 1, 3.00, 1.30, on=TODAY, long_strike=375)   # far OTM now: mostly decayed
    j.open("LOW", "put", 110, exp, 1, 1.00, 1.30, on=TODAY, long_strike=105)   # price fell through both legs
    prov = FakeProvider({"BIG": 430.0, "LOW": 100.0})
    alerts, rows = check(prov, j, cfg, today=TODAY)
    chain = {q.strike: q.mid for q in prov.chain("BIG", date.fromisoformat(exp), "put")}
    assert rows[0]["mid"] == pytest.approx(chain[380.0] - chain[375.0])
    assert any(a.trade_id == 1 and "has captured" in a.message for a in alerts)
    assert any(a.trade_id == 2 and "both legs in the money" in a.message for a in alerts)
    assert any(a.trade_id == 2 and "x the premium" in a.message for a in alerts)

