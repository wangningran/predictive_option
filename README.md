# predictive_option: option-selling research for BTC and large-cap stocks

Scans two groups of underlyings for options worth selling, and tracks the positions you open:

- **BTC**, through options on IBIT (BlackRock's spot bitcoin ETF). It trades at mainstream brokers and its options are liquid.
- **The 100 largest US-listed companies by market cap**, ADRs such as TSM and ASML included. The list refreshes every 7 days.

| Command | What it does |
|---|---|
| `screen` | Scans everything and lists candidates in three groups: BTC, cash-secured puts, put credit spreads; plus covered calls on shares you hold |
| `research NVDA` | A research card for one ticker: trend, averages, volatility, earnings date and every candidate contract |
| `monitor` | Checks open positions: take profit, stop loss, near or through the strike, earnings, position limits |
| `log` / `report` | Trade journal: records opens, closes, expiries and assignments, and reports P&L and win rate |
| `backtest` | Backtests the wheel on historical prices (cash-secured puts and covered calls only, no spreads) |

> The tool screens, alerts and records. **It never places orders.** Every number is for learning and is not investment advice.

**Docs**: [Principles of option selling](docs/principles.md) · [Models and calculations](docs/models.md)

## Two ways to open a trade

**Cash-secured put**: sell a put and keep strike × 100 in cash. If assigned, you buy 100 shares at the strike. Only low-priced underlyings are affordable this way (roughly under $50 on a $10,000 account: IBIT, BAC, PFE, VZ and the like).

**Put credit spread**: most large caps cost hundreds of dollars a share, so buying 100 shares is out of reach. Instead, sell a put and buy a put with a lower strike.

- The maximum loss is fixed: (strike difference − net credit) × 100 + fees, $500 at most by default.
- Example: MSFT at $524, sell the 495 put and buy the 490 put for a net $1.08. Above $495 at expiry you make $106; below $490 you lose $394.
- **Understand this**: spreads win about 70–80% of the time, but one loss costs about three wins. Traded at mid prices the long-run expectation is close to zero; there is an edge only when options are clearly overpriced (IV/HV above 1). That is why the table shows no annualized return for spreads: that number would be badly misleading.
- Your broker must approve you for spreads. If the price sits between the two strikes near expiry, close the spread, or the short leg will be assigned.

## The research columns

| Column | Meaning |
|---|---|
| Delta / Win | Delta of the short leg; win ≈ 1 − \|delta\|, the rough chance the price stays above the strike at expiry |
| Capital | Money tied up: strike × 100 for a cash-secured put, the maximum loss for a spread |
| Cushion | How far the price can fall before breakeven |
| IV/HV | At-the-money implied volatility ÷ 20-day historical volatility. Above 1 means options are pricier than actual movement, which favours the seller |
| Notes | Below the 200-day average, down more than 15% in a month, IV below realized |

Each ticker gets one line: the contract with delta closest to 0.20. Cash-secured puts are ranked by annualized return × IV/HV, spreads by IV/HV alone; anything below its 200-day average or down more than 15% in a month is discounted by 25%.

**Earnings**: contracts that expire after an earnings report are never listed. Stocks whose earnings date cannot be found are excluded and listed, with the reason, at the end of the output. For a company that has just reported and not yet announced its next date, the next one is estimated at the last date + 80 days.

## Install

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q                                   # everything should pass
```

Then open `config.toml` and set `fee_per_contract` to what your broker actually charges.

## Daily use

```bash
# 1. Scan about 45 minutes after the open (takes about a minute)
python -m wheel screen

# 2. Look closer at anything interesting
python -m wheel research IBIT

# 3. Log the fill
python -m wheel log open IBIT put 35 2026-12-18 --premium 0.65                     # cash-secured put
python -m wheel log open MSFT put 495 2026-12-18 --long-strike 490 --premium 1.08  # spread; premium is the net credit

# 4. Check positions every day
python -m wheel monitor

# 5. Record what you did about an alert
python -m wheel log close 1 --price 0.30     # bought back (net debit for a spread)
python -m wheel log expire 1                 # expired worthless
python -m wheel log assign 2                 # assigned (single-leg puts and calls only)

# 6. Review
python -m wheel report
```

The journal lives in `data/journal.csv` and `data/stock_ledger.csv` on your own machine and opens in Excel. This repository is public, so `data/` is in `.gitignore` and never committed.

## Configuration (`config.toml`)

- `[account]`: account size, cash reserve, per-ticker limit, fees.
- `[rules]`: days to expiry, delta range, liquidity thresholds, profit-taking and stop-loss rules.
- `[spreads]`: the maximum loss per spread ($500 by default) and the credit thresholds.
- `[universe]`: `universe_mode = "top100"` scans the 100 largest companies; `"watchlist"` scans only the tickers under `[watchlist]`. `btc_tickers` are always scanned.

## BTC notes

- IBIT trades only during US market hours while BTC trades around the clock. If BTC drops at night or over a weekend, IBIT gaps at the next open and there is no chance to adjust.
- BTC is usually about twice as volatile as a large-cap stock. The premium is high because the risk is high.
- The scan first shows BTC's price, recent moves, whether it is above its 200-day average, and where its realized volatility sits in its one-year range.

## Data and limits

- Prices come from Yahoo Finance, free and delayed about 15 minutes. **Run it during US market hours**: after the close bids and asks are often zero.
- Yahoo rate limits. The tool retries; if the market-cap ranking cannot be fetched it falls back to the cache or the shipped list (`wheel/universe_snapshot.json`).
- The reference price is the mid between bid and ask. Large-cap option quotes can be wide and real fills worse, so use limit orders.
- Backtest option prices are estimated with Black-Scholes. Use the backtest to compare rules, not to forecast returns.

## Push alerts (optional)

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, then run `python -m wheel monitor --notify`.

## Code layout

```
wheel/
  universe.py   what to scan: BTC tickers + top 100 by market cap (cached, with a shipped list)
  research.py   trend and volatility metrics per ticker
  screener.py   candidates: cash-secured puts, put credit spreads, covered calls, and scoring
  data.py       market data (Yahoo)
  bs.py         Black-Scholes pricing, delta, implied volatility
  journal.py    trade journal, positions and P&L
  monitor.py    position checks and alerts
  backtest.py   backtest
  config.py     reads config.toml
  cli.py        command line
tests/          unit tests (simulated data, no network)
docs/           principles and model notes
```
