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
    """One table for any mix of strategies. 资金 is the cash tied up, which for a spread is the max loss."""
    if not ideas:
        print("  没有符合规则的合约")
        return
    print(f"  {'代码':<6}{'现价':>8} {'行权价':>10}{'到期日':>12}{'天数':>4}{'参考价':>7}{'Delta':>7}{'胜率':>5}"
          f"{'收入':>6}{'资金':>7}{'收益':>7}{'年化':>6}{'安全垫':>6}{'IV':>5}{'IV/HV':>6}{'1月':>6}  提示")
    for i in ideas[:top]:
        m = i.metrics
        iv_hv = f"{i.iv_hv:.2f}" if i.iv_hv else "-"
        # annualizing a spread's return on risk gives absurd numbers for what is close to a fair bet
        annual = "-" if i.long_strike else f"{i.annualized:.0%}"
        print(f"  {i.ticker:<6}{i.price:>8.2f} {_strikes(i):>10}{i.expiry.isoformat():>12}{i.dte:>4}"
              f"{i.credit:>7.2f}{i.delta:>7.2f}{i.win_prob:>5.0%}{i.premium:>6.0f}{i.capital:>7.0f}{i.ret:>7.1%}"
              f"{annual:>6}{i.cushion:>6.1%}{i.iv:>5.0%}{iv_hv:>6}{_pct(m.chg_1m if m else None):>6}"
              f"  {'，'.join(i.flags)}")


def _print_btc_context(provider, metrics, btc_tickers):
    from . import research
    try:
        btc = research.compute(provider.histories(["BTC-USD"])["BTC-USD"], periods_per_year=365)
    except Exception as e:
        print(f"  BTC 行情获取失败（{e}）")
        return
    trend = "200日线上方" if btc.above_200 else "200日线下方"
    print(f"  BTC ${btc.price:,.0f}  1日 {_pct(btc.chg_1d, 1)}  5日 {_pct(btc.chg_5d, 1)}  1月 {_pct(btc.chg_1m, 1)}  "
          f"距一年高点 {_pct(btc.off_high)}  {trend}")
    print(f"  BTC 20日实际波动率 {_pct(btc.hv20)}（处于一年区间的 {_pct(btc.hv_rank)} 位置）")
    for t in btc_tickers:
        if t in metrics:
            m = metrics[t]
            print(f"  {t} ${m.price:.2f}  1日 {_pct(m.chg_1d, 1)}  20日实际波动率 {_pct(m.hv20)}")


def cmd_screen(args, cfg):
    from . import universe
    from .screener import CSP, PUT_SPREAD, best_per_ticker, scan, screen_calls
    j = _journal(cfg)
    p = _provider()
    cash = args.cash if args.cash is not None else _free_cash(cfg, j)
    btc, stocks, _ = universe.load(p, cfg)
    scope = f"市值前 {cfg.top_n} 的美股" if cfg.universe_mode == "top100" else "观察列表"
    print(f"可用资金：${cash:,.0f}（账户 ${cfg.account_size:,.0f}，保留 {cfg.cash_reserve_pct:.0%} 现金）")
    print(f"范围：{'、'.join(btc)} + {scope}（{len(stocks)} 只）")
    print(f"规则：{cfg.dte_min}-{cfg.dte_max} 天到期，|Delta| {cfg.delta_min}-{cfg.delta_max}（每只取最接近 "
          f"{cfg.target_delta} 的），未平仓量 ≥ {cfg.min_open_interest}，价差 ≤ {cfg.max_spread_pct:.0%}，"
          f"{'避开财报' if cfg.avoid_earnings else '不避开财报'}；单笔价差最大亏损 ≤ ${cfg.max_loss_per_spread:,.0f}\n")
    result = scan(p, cfg, btc + stocks, cash, funds=set(btc), retry_wait=30)
    picks = sorted(best_per_ticker(result.ideas, cfg), key=lambda i: i.score, reverse=True)

    print("== BTC ==")
    _print_btc_context(p, result.metrics, btc)
    _print_ideas(sorted((i for i in result.ideas if i.ticker in btc), key=lambda i: (i.expiry, -i.strike)), args.top)

    print(f"\n== 现金担保 Put（买得起 100 股的大盘股）==")
    _print_ideas([i for i in picks if i.strategy == CSP and i.ticker not in btc], args.top)

    print(f"\n== Put 信用价差（股价太高，用价差把最大亏损锁在 ${cfg.max_loss_per_spread:,.0f} 以内；“资金”即最大亏损）==")
    _print_ideas([i for i in picks if i.strategy == PUT_SPREAD and i.ticker not in btc], args.top)

    calls = screen_calls(p, cfg, _uncovered(j))
    if calls:
        print("\n== Covered Call（基于你持有的正股）==")
        _print_ideas(calls, args.top)
    if result.errors:
        by_reason = {}
        for t, why in sorted(result.errors.items()):
            by_reason.setdefault(why, []).append(t)
        print(f"\n未纳入的 {len(result.errors)} 只：" + "；".join(f"{why}：{'、'.join(ts)}" for why, ts in by_reason.items()))
    print("\n排序：现金担保 Put 按 年化收益 × IV/HV；价差只按 IV/HV（期权是否比实际波动贵）。"
          "200 日线下方或 1 个月跌超 15% 的打 75 折。")
    print("价差提醒：胜率约 70%–80%，但亏一次大约等于赚三次，按中间价成交时长期期望接近零；"
          "只有 IV/HV 明显大于 1 时才有优势。参考价是中间价，用限价单。")


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
    print(f"  现价 ${m.price:,.2f}   1日 {_pct(m.chg_1d, 1)}  5日 {_pct(m.chg_5d, 1)}  1月 {_pct(m.chg_1m, 1)}")
    print(f"  50日均线 ${m.sma50:,.2f}，200日均线 ${m.sma200:,.2f}（{'上方' if m.above_200 else '下方'}）")
    print(f"  距一年高点 {_pct(m.off_high)}，一年内最大回撤 {_pct(m.max_dd)}")
    print(f"  20日实际波动率 {_pct(m.hv20)}，处于一年区间的 {_pct(m.hv_rank)} 位置")
    if result.ideas:
        i = result.ideas[0]
        earn = "未知" if i.earnings_unknown else (i.earnings.isoformat() if i.earnings else "无")
        print(f"  平值隐含波动率 {_pct(i.atm_iv)}，IV/HV {i.iv_hv:.2f}；下次财报 {earn}" if i.iv_hv else
              f"  下次财报 {earn}")
    print()
    _print_ideas(sorted(result.ideas, key=lambda i: (i.expiry, -i.strike)), args.top)


def cmd_monitor(args, cfg):
    from .monitor import check, notify_telegram
    j = _journal(cfg)
    if not j.open_trades() and not any(p["shares"] for p in j.positions().values()):
        print("没有持仓。")
        return
    alerts, rows = check(_provider(), j, cfg)
    if rows:
        print(f"  {'#':<4}{'代码':<6}{'合约':<26}{'天数':>5}{'现价':>8}{'卖出价':>7}{'现值':>7}{'已赚':>7}")
        for r in rows:
            mid = f"{r['mid']:.2f}" if r["mid"] is not None else "-"
            cap = f"{r['captured']:.0%}" if r["captured"] is not None else "-"
            print(f"  {r['id']:<4}{r['ticker']:<6}{r['label']:<26}{r['dte']:>5}"
                  f"{r['price']:>8.2f}{r['premium']:>7.2f}{mid:>7}{cap:>7}")
    print()
    if not alerts:
        print("一切正常，无需操作。")
    for a in alerts:
        print(a)
    if args.notify and alerts:
        text = "期权持仓提醒\n" + "\n".join(str(a) for a in alerts)
        print("\n已推送到 Telegram" if notify_telegram(text) else "\n未设置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，跳过推送")


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
    print(f"回测：{name}  {m['start']} → {m['end']}  初始资金 ${params.initial_cash:,.0f}")
    print(f"参数：Put delta {params.put_delta}，Call delta {params.call_delta}，{params.dte} 天，"
          f"止盈 {params.take_profit:.0%}，IV = 已实现波动率 × {params.iv_multiplier}\n")
    print(f"  {'':<14}{'轮子策略':>12}{'买入持有':>12}")
    print(f"  {'总收益':<14}{m['total_return']:>12.1%}{m['bh_total_return']:>12.1%}")
    print(f"  {'年化收益':<14}{m['cagr']:>12.1%}{m['bh_cagr']:>12.1%}")
    print(f"  {'最大回撤':<14}{m['max_drawdown']:>12.1%}{m['bh_max_drawdown']:>12.1%}")
    print(f"  {'最差单月':<14}{m['worst_month']:>12.1%}")
    if m["option_trades"]:
        print(f"\n  期权交易 {m['option_trades']} 笔，期权腿胜率 {m['option_win_rate']:.0%}（不含正股盈亏，别被它骗了），"
              f"Put 被行权 {m['put_assignments']} 次，正股被叫走 {m['calls_called_away']} 次")
        print(f"  净权利金 ${m['net_premium']:,.0f}，平均每月 ${m['avg_monthly_premium']:,.0f}")
    else:
        print("\n  没有成交任何期权（资金可能不够买 100 股）")
    print("\n注意：期权价格由模型估算，忽略了偏斜和事件前的 IV 飙升，实盘通常更差。")
    if args.out:
        res.equity.to_frame().assign(buy_hold=closes / closes.iloc[0] * params.initial_cash).to_csv(args.out)
        res.trades.to_csv(args.out.replace(".csv", "_trades.csv"), index=False)
        print(f"净值曲线已保存到 {args.out}")
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
        print(f"图表已保存到 {args.plot}")


def cmd_log(args, cfg):
    j = _journal(cfg)
    on = date.fromisoformat(args.date) if getattr(args, "date", None) else None
    if args.action == "open":
        legs = 2 if args.long_strike else 1
        fees = args.fees if args.fees is not None else cfg.fee_per_contract * args.contracts * legs
        t = j.open(args.ticker, args.kind, args.strike, args.expiry, args.contracts, args.premium, fees, on,
                   args.notes, args.long_strike)
        print(f"已记录 #{t.id}：卖出 {t.contracts} 张 {t.ticker} {t.label}，"
              f"收入 ${t.premium * 100 * t.contracts - fees:,.2f}（扣费后）")
    elif args.action == "close":
        open_trade = j.get(args.id)
        legs = 2 if open_trade.is_spread else 1
        fees = args.fees if args.fees is not None else cfg.fee_per_contract * open_trade.contracts * legs
        t = j.close(args.id, args.price, fees, on)
        print(f"#{t.id} 已平仓，盈亏 ${t.realized_pnl:,.2f}")
    elif args.action == "expire":
        t = j.expire(args.id, on)
        print(f"#{t.id} 作废到期，盈亏 ${t.realized_pnl:,.2f}")
    elif args.action == "assign":
        t = j.assign(args.id, args.fees, on)
        verb = "买入" if t.kind == "put" else "卖出"
        print(f"#{t.id} 被行权：以 ${t.strike:g} {verb} {t.contracts * 100} 股 {t.ticker}，期权盈亏 ${t.realized_pnl:,.2f}")
    elif args.action == "stock":
        f = j.stock(args.ticker, args.shares, args.price, args.fees, on)
        print(f"已记录股票成交：{f.ticker} {f.shares:+d} 股 @ ${f.price:g}")
    j.save()


def cmd_report(args, cfg):
    j = _journal(cfg)
    s = j.stats()
    print("== 交易日志汇总 ==")
    print(f"  已结束交易 {s['closed_trades']} 笔，进行中 {s['open_trades']} 笔，胜率 {s['win_rate']:.0%}，被行权 {s['assignments']} 次")
    print(f"  期权已实现盈亏 ${s['option_pnl']:,.2f}，股票已实现盈亏 ${s['stock_pnl']:,.2f}")
    print(f"  当前 Put 占用现金 ${s['collateral']:,.0f}")
    if s["monthly"]:
        print("\n  月份      期权盈亏")
        for month, pnl in s["monthly"].items():
            print(f"  {month}  {pnl:>10,.2f}")
    pos = {k: v for k, v in j.positions().items() if v["shares"]}
    if pos:
        print("\n  正股持仓：")
        for ticker, p in pos.items():
            print(f"  {ticker:<6}{p['shares']:>6} 股  均价 ${p['avg_cost']:.2f}  扣除已收权利金后成本 ${p['net_basis']:.2f}")
    if j.open_trades():
        print("\n  进行中的期权：")
        for t in j.open_trades():
            print(f"  #{t.id:<3} {t.ticker:<6}{t.label}  x{t.contracts}  卖出价 {t.premium:.2f}")


def build_parser():
    ap = argparse.ArgumentParser(prog="wheel", description="Sell Put / Covered Call 轮子策略工具")
    ap.add_argument("--config", help="config.toml 路径（默认使用项目根目录下的）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("screen", help="扫描 BTC（IBIT）和大盘股，列出可卖的 Put、Put 价差和 Covered Call")
    s.add_argument("--cash", type=float, help="覆盖可用现金")
    s.add_argument("--top", type=int, default=12, help="每个板块最多列几条")
    s.set_defaults(func=cmd_screen)

    rs = sub.add_parser("research", help="单只股票的研究卡片：走势、波动率、财报和全部候选合约")
    rs.add_argument("ticker")
    rs.add_argument("--cash", type=float, help="覆盖可用现金")
    rs.add_argument("--top", type=int, default=20)
    rs.set_defaults(func=cmd_research)

    m = sub.add_parser("monitor", help="检查持仓并给出提醒")
    m.add_argument("--notify", action="store_true", help="通过 Telegram 推送")
    m.set_defaults(func=cmd_monitor)

    b = sub.add_parser("backtest", help="回测轮子策略")
    b.add_argument("ticker", nargs="?", default="SPY")
    b.add_argument("--start", default="2018-01-01")
    b.add_argument("--end")
    b.add_argument("--csv", help="用本地 CSV（Date, Close 列）代替在线数据")
    b.add_argument("--synthetic", type=int, metavar="DAYS", help="用模拟价格（离线演示）")
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--cash", type=float)
    b.add_argument("--delta", type=float, default=0.25)
    b.add_argument("--call-delta", type=float)
    b.add_argument("--dte", type=int, default=35)
    b.add_argument("--take-profit", type=float)
    b.add_argument("--iv-mult", type=float, default=1.10)
    b.add_argument("--out", help="保存净值曲线 CSV")
    b.add_argument("--plot", help="保存图表 PNG（需要 matplotlib）")
    b.set_defaults(func=cmd_backtest)

    lg = sub.add_parser("log", help="记录交易")
    lsub = lg.add_subparsers(dest="action", required=True)
    o = lsub.add_parser("open", help="卖出开仓")
    o.add_argument("ticker")
    o.add_argument("kind", choices=["put", "call"])
    o.add_argument("strike", type=float)
    o.add_argument("expiry", help="YYYY-MM-DD")
    o.add_argument("--premium", type=float, required=True, help="每股成交价，如 0.45；价差填净收入")
    o.add_argument("--long-strike", type=float, default=0.0, help="Put 价差里买入腿的行权价")
    o.add_argument("--contracts", type=int, default=1)
    o.add_argument("--fees", type=float, help="默认 fee_per_contract × 张数")
    o.add_argument("--notes", default="")
    c = lsub.add_parser("close", help="买回平仓")
    c.add_argument("id", type=int)
    c.add_argument("--price", type=float, required=True, help="每股买回价；价差填净支出")
    c.add_argument("--fees", type=float)
    e = lsub.add_parser("expire", help="到期作废")
    e.add_argument("id", type=int)
    a = lsub.add_parser("assign", help="被行权")
    a.add_argument("id", type=int)
    a.add_argument("--fees", type=float, default=0.0)
    st = lsub.add_parser("stock", help="手动记录股票买卖（卖出用负数）")
    st.add_argument("ticker")
    st.add_argument("shares", type=int)
    st.add_argument("price", type=float)
    st.add_argument("--fees", type=float, default=0.0)
    for p in (o, c, e, a, st):
        p.add_argument("--date", help="成交日期 YYYY-MM-DD，默认今天")
    lg.set_defaults(func=cmd_log)

    r = sub.add_parser("report", help="交易日志汇总")
    r.set_defaults(func=cmd_report)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = config_mod.load(args.config)
    try:
        args.func(args, cfg)
    except (ValueError, KeyError) as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
