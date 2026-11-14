"""Command line entry point: python -m wheel <command>"""

import argparse
import sys
from datetime import date

from . import config as config_mod
from .journal import Journal


def _journal(cfg):
    return Journal(cfg.path("journal_path"), cfg.path("stock_ledger_path"))


def _provider():
    from .data import YahooProvider
    return YahooProvider()


def _uncovered(j):
    pos = j.positions()
    for ticker, p in pos.items():
        covered = sum(t.contracts * 100 for t in j.open_trades() if t.ticker == ticker and t.kind == "call")
        p["uncovered"] = max(p["shares"] - covered, 0)
    return pos


def _free_cash(cfg, j):
    committed = j.stats()["collateral"] + sum(p["shares"] * p["avg_cost"] for p in j.positions().values())
    return cfg.account_size * (1 - cfg.cash_reserve_pct) - committed


def _pct(x, digits=0):
    return "-" if x is None or x != x else f"{x:.{digits}%}"


def _strikes(i):
    return f"{i.strike:g}/{i.long_strike:g}" if i.long_strike else f"{i.strike:g}"


def _print_ideas(ideas, top):
    """One table for any mix of strategies. Capital is the cash tied up, which for a spread is the max loss."""
    if not ideas:
        print("  no contracts pass the rules")
        return
    print(f"  {'Ticker':<6}{'Price':>8} {'Strike':>10}{'Expiry':>12}{'DTE':>4}{'Mid':>7}{'Delta':>7}{'Win':>5}"
          f"{'Prem':>6}{'Capital':>8}{'Return':>7}{'Annual':>7}{'Cushion':>8}{'IV':>5}{'IV/HV':>6}{'1M':>6}  Notes")
    for i in ideas[:top]:
        m = i.metrics
        iv_hv = f"{i.iv_hv:.2f}" if i.iv_hv else "-"
        # annualizing a spread's return on risk gives absurd numbers for what is close to a fair bet
        annual = "-" if i.long_strike else f"{i.annualized:.0%}"
        print(f"  {i.ticker:<6}{i.price:>8.2f} {_strikes(i):>10}{i.expiry.isoformat():>12}{i.dte:>4}"
              f"{i.credit:>7.2f}{i.delta:>7.2f}{i.win_prob:>5.0%}{i.premium:>6.0f}{i.capital:>8.0f}{i.ret:>7.1%}"
              f"{annual:>7}{i.cushion:>8.1%}{i.iv:>5.0%}{iv_hv:>6}{_pct(m.chg_1m if m else None):>6}"
              f"  {', '.join(i.flags)}")


def _print_btc_context(provider, metrics, btc_tickers):
    from . import research
    try:
        btc = research.compute(provider.histories(["BTC-USD"])["BTC-USD"], periods_per_year=365)
    except Exception as e:
        print(f"  BTC price fetch failed ({e})")
        return
    trend = "above 200-day avg" if btc.above_200 else "below 200-day avg"
    print(f"  BTC ${btc.price:,.0f}  1D {_pct(btc.chg_1d, 1)}  5D {_pct(btc.chg_5d, 1)}  1M {_pct(btc.chg_1m, 1)}  "
          f"off 1-year high {_pct(btc.off_high)}  {trend}")
    print(f"  BTC 20-day realized vol {_pct(btc.hv20)} ({_pct(btc.hv_rank)} of its 1-year range)")
    for t in btc_tickers:
        if t in metrics:
            m = metrics[t]
            print(f"  {t} ${m.price:.2f}  1D {_pct(m.chg_1d, 1)}  20-day realized vol {_pct(m.hv20)}")


def cmd_screen(args, cfg):
    from . import universe
    from .screener import CSP, PUT_SPREAD, best_per_ticker, scan, screen_calls
    j = _journal(cfg)
    p = _provider()
    cash = args.cash if args.cash is not None else _free_cash(cfg, j)
    btc, stocks, _ = universe.load(p, cfg)
    scope = f"top {cfg.top_n} US stocks by market cap" if cfg.universe_mode == "top100" else "watchlist"
    print(f"Available cash: ${cash:,.0f} (account ${cfg.account_size:,.0f}, {cfg.cash_reserve_pct:.0%} kept in reserve)")
    print(f"Universe: {', '.join(btc)} + {scope} ({len(stocks)} tickers)")
    print(f"Rules: {cfg.dte_min}-{cfg.dte_max} DTE, |delta| {cfg.delta_min}-{cfg.delta_max} (closest to "
          f"{cfg.target_delta} per ticker), open interest >= {cfg.min_open_interest}, spread <= {cfg.max_spread_pct:.0%}, "
          f"{'avoid earnings' if cfg.avoid_earnings else 'earnings allowed'}; max loss per spread <= "
          f"${cfg.max_loss_per_spread:,.0f}\n")
    result = scan(p, cfg, btc + stocks, cash, funds=set(btc), retry_wait=30)
    picks = sorted(best_per_ticker(result.ideas, cfg), key=lambda i: i.score, reverse=True)

    print("== BTC ==")
    _print_btc_context(p, result.metrics, btc)
    _print_ideas(sorted((i for i in result.ideas if i.ticker in btc), key=lambda i: (i.expiry, -i.strike)), args.top)

    print("\n== Cash-secured puts (large caps you can afford 100 shares of) ==")
    _print_ideas([i for i in picks if i.strategy == CSP and i.ticker not in btc], args.top)

    print(f"\n== Put credit spreads (shares too expensive; loss capped at ${cfg.max_loss_per_spread:,.0f}, Capital = max loss) ==")
    _print_ideas([i for i in picks if i.strategy == PUT_SPREAD and i.ticker not in btc], args.top)

    calls = screen_calls(p, cfg, _uncovered(j))
    if calls:
        print("\n== Covered calls (on shares you hold) ==")
        _print_ideas(calls, args.top)
    if result.errors:
        by_reason = {}
        for t, why in sorted(result.errors.items()):
            by_reason.setdefault(why, []).append(t)
        print(f"\nLeft out ({len(result.errors)}): " + "; ".join(f"{why}: {', '.join(ts)}" for why, ts in by_reason.items()))
    print("\nRanking: cash-secured puts by annual return x IV/HV; spreads by IV/HV alone (are options pricier "
          "than realized moves). Below the 200-day average or down more than 15% in a month: x0.75.")
    print("Spreads win about 70-80% of the time, but one loss costs about three wins; at mid prices the long-run "
          "expectation is near zero, with an edge only when IV/HV is clearly above 1. Mid is a reference: use limit orders.")


def cmd_research(args, cfg):
    """Everything the screener knows about one ticker."""
    from .screener import scan
    p = _provider()
    t = args.ticker.upper()
    cash = args.cash if args.cash is not None else _free_cash(cfg, _journal(cfg))
    result = scan(p, cfg, [t], cash, funds={t} if p.is_fund(t) else set(), workers=1)
    if t in result.errors:
        print(f"{t}: {result.errors[t]}")
        return
    m = result.metrics[t]
    print(f"== {t} ==")
    print(f"  Price ${m.price:,.2f}   1D {_pct(m.chg_1d, 1)}  5D {_pct(m.chg_5d, 1)}  1M {_pct(m.chg_1m, 1)}")
    print(f"  50-day avg ${m.sma50:,.2f}, 200-day avg ${m.sma200:,.2f} ({'above' if m.above_200 else 'below'})")
    print(f"  Off 1-year high {_pct(m.off_high)}, max drawdown in the year {_pct(m.max_dd)}")
    print(f"  20-day realized vol {_pct(m.hv20)}, {_pct(m.hv_rank)} of its 1-year range")
    if result.ideas:
        i = result.ideas[0]
        earn = "unknown" if i.earnings_unknown else (i.earnings.isoformat() if i.earnings else "none")
        print(f"  At-the-money IV {_pct(i.atm_iv)}, IV/HV {i.iv_hv:.2f}; next earnings {earn}" if i.iv_hv else
              f"  Next earnings {earn}")
    print()
    _print_ideas(sorted(result.ideas, key=lambda i: (i.expiry, -i.strike)), args.top)


def cmd_monitor(args, cfg):
    from .monitor import check, notify_telegram
    j = _journal(cfg)
    if not j.open_trades() and not any(p["shares"] for p in j.positions().values()):
        print("No positions.")
        return
    alerts, rows = check(_provider(), j, cfg)
    if rows:
        print(f"  {'#':<4}{'Ticker':<7}{'Contract':<26}{'DTE':>5}{'Price':>8}{'Sold':>7}{'Now':>7}{'Kept':>7}")
        for r in rows:
            mid = f"{r['mid']:.2f}" if r["mid"] is not None else "-"
            cap = f"{r['captured']:.0%}" if r["captured"] is not None else "-"
            print(f"  {r['id']:<4}{r['ticker']:<7}{r['label']:<26}{r['dte']:>5}"
                  f"{r['price']:>8.2f}{r['premium']:>7.2f}{mid:>7}{cap:>7}")
    print()
    if not alerts:
        print("All clear, nothing to do.")
    for a in alerts:
        print(a)
    if args.notify and alerts:
        text = "Option position alerts\n" + "\n".join(str(a) for a in alerts)
        print("\nSent to Telegram" if notify_telegram(text) else "\nTELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set, not sent")


def cmd_backtest(args, cfg):
    from . import backtest as bt
    from .data import load_csv_history
    if args.csv:
        closes = load_csv_history(args.csv)
        name = args.csv
    elif args.synthetic:
        closes = bt.synthetic_prices(days=args.synthetic, seed=args.seed, start=40.0)
        name = "synthetic"
    else:
        closes = _provider().history(args.ticker, args.start, args.end)
        name = args.ticker
    if args.csv and (args.start or args.end):
        closes = closes.loc[args.start:args.end]
    params = bt.Params(
        initial_cash=args.cash or cfg.account_size, put_delta=args.delta, call_delta=args.call_delta or args.delta,
        dte=args.dte, take_profit=args.take_profit if args.take_profit is not None else cfg.take_profit,
        iv_multiplier=args.iv_mult, fee_per_contract=cfg.fee_per_contract,
        cash_reserve_pct=cfg.cash_reserve_pct, risk_free_rate=cfg.risk_free_rate,
        calls_above_cost_basis=cfg.calls_above_cost_basis,
    )
    res = bt.run(closes, params)
    m = res.metrics
    print(f"Backtest: {name}  {m['start']} -> {m['end']}  starting cash ${params.initial_cash:,.0f}")
    print(f"Params: put delta {params.put_delta}, call delta {params.call_delta}, {params.dte} days, "
          f"take profit {params.take_profit:.0%}, IV = realized vol x {params.iv_multiplier}\n")
    print(f"  {'':<14}{'Wheel':>12}{'Buy & hold':>12}")
    print(f"  {'Total return':<14}{m['total_return']:>12.1%}{m['bh_total_return']:>12.1%}")
    print(f"  {'CAGR':<14}{m['cagr']:>12.1%}{m['bh_cagr']:>12.1%}")
    print(f"  {'Max drawdown':<14}{m['max_drawdown']:>12.1%}{m['bh_max_drawdown']:>12.1%}")
    print(f"  {'Worst month':<14}{m['worst_month']:>12.1%}")
    if m["option_trades"]:
        print(f"\n  {m['option_trades']} option trades, option-leg win rate {m['option_win_rate']:.0%} "
              f"(excludes share P&L, do not be fooled by it), puts assigned {m['put_assignments']} times, "
              f"shares called away {m['calls_called_away']} times")
        print(f"  Net premium ${m['net_premium']:,.0f}, ${m['avg_monthly_premium']:,.0f} a month on average")
    else:
        print("\n  No options traded (cash may not cover 100 shares)")
    print("\nNote: option prices are modelled and ignore skew and pre-event IV spikes; live results are usually worse.")
    if args.out:
        res.equity.to_frame().assign(buy_hold=closes / closes.iloc[0] * params.initial_cash).to_csv(args.out)
        res.trades.to_csv(args.out.replace(".csv", "_trades.csv"), index=False)
        print(f"Equity curve saved to {args.out}")
    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        ax = res.equity.plot(label="Wheel", figsize=(10, 5))
        (closes / closes.iloc[0] * params.initial_cash).plot(ax=ax, label="Buy & hold", alpha=0.7)
        ax.set_title(f"Wheel backtest: {name}")
        ax.legend()
        plt.tight_layout()
        plt.savefig(args.plot, dpi=120)
        print(f"Chart saved to {args.plot}")


def cmd_log(args, cfg):
    j = _journal(cfg)
    on = date.fromisoformat(args.date) if getattr(args, "date", None) else None
    if args.action == "open":
        legs = 2 if args.long_strike else 1
        fees = args.fees if args.fees is not None else cfg.fee_per_contract * args.contracts * legs
        t = j.open(args.ticker, args.kind, args.strike, args.expiry, args.contracts, args.premium, fees, on,
                   args.notes, args.long_strike)
        print(f"Logged #{t.id}: sold {t.contracts} x {t.ticker} {t.label}, "
              f"received ${t.premium * 100 * t.contracts - fees:,.2f} after fees")
    elif args.action == "close":
        open_trade = j.get(args.id)
        legs = 2 if open_trade.is_spread else 1
        fees = args.fees if args.fees is not None else cfg.fee_per_contract * open_trade.contracts * legs
        t = j.close(args.id, args.price, fees, on)
        print(f"#{t.id} closed, P&L ${t.realized_pnl:,.2f}")
    elif args.action == "expire":
        t = j.expire(args.id, on)
        print(f"#{t.id} expired worthless, P&L ${t.realized_pnl:,.2f}")
    elif args.action == "assign":
        t = j.assign(args.id, args.fees, on)
        verb = "bought" if t.kind == "put" else "sold"
        print(f"#{t.id} assigned: {verb} {t.contracts * 100} {t.ticker} shares at ${t.strike:g}, option P&L ${t.realized_pnl:,.2f}")
    elif args.action == "stock":
        f = j.stock(args.ticker, args.shares, args.price, args.fees, on)
        print(f"Logged stock fill: {f.ticker} {f.shares:+d} shares @ ${f.price:g}")
    j.save()


def cmd_report(args, cfg):
    j = _journal(cfg)
    s = j.stats()
    print("== Trade journal ==")
    print(f"  {s['closed_trades']} closed, {s['open_trades']} open, win rate {s['win_rate']:.0%}, {s['assignments']} assignments")
    print(f"  Realized option P&L ${s['option_pnl']:,.2f}, realized stock P&L ${s['stock_pnl']:,.2f}")
    print(f"  Cash tied up by puts ${s['collateral']:,.0f}")
    if s["monthly"]:
        print("\n  Month     Option P&L")
        for month, pnl in s["monthly"].items():
            print(f"  {month}  {pnl:>10,.2f}")
    pos = {k: v for k, v in j.positions().items() if v["shares"]}
    if pos:
        print("\n  Shares held:")
        for ticker, p in pos.items():
            print(f"  {ticker:<6}{p['shares']:>6} shares  avg cost ${p['avg_cost']:.2f}  net of premiums ${p['net_basis']:.2f}")
    if j.open_trades():
        print("\n  Open options:")
        for t in j.open_trades():
            print(f"  #{t.id:<3} {t.ticker:<6}{t.label}  x{t.contracts}  sold at {t.premium:.2f}")


def build_parser():
    ap = argparse.ArgumentParser(prog="wheel", description="Option-selling research: cash-secured puts, put spreads, covered calls")
    ap.add_argument("--config", help="path to config.toml (default: the one in the project root)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("screen", help="scan BTC (IBIT) and large caps for puts, put spreads and covered calls to sell")
    s.add_argument("--cash", type=float, help="override the available cash")
    s.add_argument("--top", type=int, default=12, help="rows per section")
    s.set_defaults(func=cmd_screen)

    rs = sub.add_parser("research", help="research card for one ticker: trend, volatility, earnings and every candidate")
    rs.add_argument("ticker")
    rs.add_argument("--cash", type=float, help="override the available cash")
    rs.add_argument("--top", type=int, default=20)
    rs.set_defaults(func=cmd_research)

    m = sub.add_parser("monitor", help="check open positions and print alerts")
    m.add_argument("--notify", action="store_true", help="send alerts to Telegram")
    m.set_defaults(func=cmd_monitor)

    b = sub.add_parser("backtest", help="backtest the wheel")
    b.add_argument("ticker", nargs="?", default="SPY")
    b.add_argument("--start", default="2018-01-01")
    b.add_argument("--end")
    b.add_argument("--csv", help="local CSV with Date and Close columns instead of online data")
    b.add_argument("--synthetic", type=int, metavar="DAYS", help="simulated prices (offline demo)")
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--cash", type=float)
    b.add_argument("--delta", type=float, default=0.25)
    b.add_argument("--call-delta", type=float)
    b.add_argument("--dte", type=int, default=35)
    b.add_argument("--take-profit", type=float)
    b.add_argument("--iv-mult", type=float, default=1.10)
    b.add_argument("--out", help="save the equity curve as CSV")
    b.add_argument("--plot", help="save a PNG chart (needs matplotlib)")
    b.set_defaults(func=cmd_backtest)

    lg = sub.add_parser("log", help="record trades")
    lsub = lg.add_subparsers(dest="action", required=True)
    o = lsub.add_parser("open", help="sell to open")
    o.add_argument("ticker")
    o.add_argument("kind", choices=["put", "call"])
    o.add_argument("strike", type=float)
    o.add_argument("expiry", help="YYYY-MM-DD")
    o.add_argument("--premium", type=float, required=True, help="fill price per share, e.g. 0.45; the net credit for a spread")
    o.add_argument("--long-strike", type=float, default=0.0, help="strike of the bought leg of a put spread")
    o.add_argument("--contracts", type=int, default=1)
    o.add_argument("--fees", type=float, help="default: fee_per_contract x contracts x legs")
    o.add_argument("--notes", default="")
    c = lsub.add_parser("close", help="buy to close")
    c.add_argument("id", type=int)
    c.add_argument("--price", type=float, required=True, help="price paid per share; the net debit for a spread")
    c.add_argument("--fees", type=float)
    e = lsub.add_parser("expire", help="expired worthless")
    e.add_argument("id", type=int)
    a = lsub.add_parser("assign", help="assigned")
    a.add_argument("id", type=int)
    a.add_argument("--fees", type=float, default=0.0)
    st = lsub.add_parser("stock", help="record a stock trade by hand (negative shares to sell)")
    st.add_argument("ticker")
    st.add_argument("shares", type=int)
    st.add_argument("price", type=float)
    st.add_argument("--fees", type=float, default=0.0)
    for p in (o, c, e, a, st):
        p.add_argument("--date", help="trade date YYYY-MM-DD, default today")
    lg.set_defaults(func=cmd_log)

    r = sub.add_parser("report", help="trade journal summary")
    r.set_defaults(func=cmd_report)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = config_mod.load(args.config)
    try:
        args.func(args, cfg)
    except (ValueError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
