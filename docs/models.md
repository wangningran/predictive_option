# Models and calculations

This document explains how every number the tool shows is computed, and where the models fall short. The code lives in `wheel/`.

## 1. Pipeline

```
Universe ──▶ Price history ──▶ Trend and volatility ──▶ Option chains ──▶ Candidate trades ──▶ Score and rank
universe.py   data.py           research.py              data.py           screener.py          screener.py
```

1. **Universe** (`universe.py`): the BTC tickers (IBIT by default) plus the 100 largest US-listed companies by market cap. The ranking refreshes every 7 days; when it cannot be fetched, the cache or a shipped snapshot is used. Where a company has several share classes only the cheaper one is kept (BRK-B, not BRK-A); preferred shares and over-the-counter listings are dropped.
2. **Price history**: one year of daily closes for every ticker, fetched in batches.
3. **Option chains**: for each ticker, up to two expiries between 25 and 45 days out, nearest to 35 days first.

## 2. Option pricing: Black-Scholes (`bs.py`)

Theoretical put and call prices:

```
d1 = [ln(S/K) + (r + σ²/2)·T] / (σ·√T)
d2 = d1 − σ·√T

Call = S·N(d1) − K·e^(−rT)·N(d2)
Put  = K·e^(−rT)·N(−d2) − S·N(−d1)
```

S is the price, K the strike, T the time to expiry in years, r the risk-free rate (4% by default), σ the volatility and N the standard normal cumulative distribution.

- **Delta**: N(d1) for a call, N(d1) − 1 for a put.
- **Implied volatility (IV)**: the σ that reproduces the option's market price, found by bisection. The market price used is the mid of bid and ask. The data source's own IV is not used because it is unreliable outside market hours.

Limits: the model assumes constant volatility, continuous prices and exercise only at expiry. US equity options can be exercised early and prices gap, so delta is only an approximate probability.

## 3. Trend and volatility (`research.py`)

From one year of daily closes:

| Metric | Calculation |
|---|---|
| **HV20** (20-day historical volatility) | Standard deviation of the last 20 daily log returns × √252 (√365 for spot BTC, which trades every day) |
| **HV rank** | Where today's HV20 sits in its own one-year range: (now − low) ÷ (high − low), from 0 to 1 |
| **50- and 200-day averages** | Mean of the last 50 and 200 closes |
| **Above the 200-day average** | Price compared with the 200-day average; not judged with fewer than 200 days of history |
| **1-day, 5-day, 1-month change** | Against the close 1, 5 and 21 trading days ago |
| **Off the 1-year high** | Price ÷ highest close of the year − 1 |
| **Maximum drawdown** | The largest fall from any peak to a later trough within the year |

## 4. Candidate trades (`screener.py`)

### Rules for the short leg

A put must meet all of these:

- strike below the price (out of the money);
- \|delta\| between 0.15 and 0.30;
- bid-ask spread ÷ mid ≤ 15%;
- open interest ≥ 100;
- no earnings report before expiry.

### Cash-secured puts

If strike × 100 fits within the available cash and within half the account, the tool lists a cash-secured put:

```
Premium   = mid × 100 − fees
Capital   = strike × 100
Return    = premium ÷ capital
Annual    = return × 365 ÷ days to expiry
Breakeven = strike − mid
Cushion   = (price − breakeven) ÷ price
```

### Put credit spreads

If no put on the ticker is affordable, the tool builds spreads instead. For each qualifying short leg it looks for a long leg among the lower strikes:

```
Width           = short strike − long strike
Credit          = short mid − long mid
Natural credit  = short bid − long ask
Maximum loss    = (width − credit) × 100 + fees on both legs
Return          = (credit × 100 − fees on both legs) ÷ maximum loss
```

A spread must satisfy:

- maximum loss ≤ $500 (`max_loss_per_spread`) and within the available cash;
- credit ÷ width ≥ 20%;
- natural credit ≥ 50% of the mid credit (otherwise the quotes are too wide for the mid to be trusted).

When several long legs fit one short leg, the **widest** spread is kept. A wider spread depends less on the long leg's quote.

### Win rate

```
Win rate ≈ 1 − |delta of the short leg|
```

This approximates the chance the price stays above the short strike at expiry. It comes from the option price itself: it is the probability the market implies, not a forecast.

### Earnings dates

- The data source has a future earnings date: contracts expiring after it are skipped.
- It only has a date just past (the company reported and has not yet announced the next one): the next is estimated at the last date + 80 days.
- No date, or one more than 80 days old: the whole ticker is excluded and listed at the end of the output. Under rate limiting the lookup fails silently, so "not found" must not be read as "no earnings".
- ETFs, IBIT included, have no earnings and skip this check.

## 5. Scoring and ranking

Each ticker keeps one trade: the one whose \|delta\| is closest to 0.20. Those are then sorted by score.

**IV/HV** = implied volatility of the at-the-money put (the strike closest to the price) ÷ HV20.

```
Cash-secured put score = annual return × clamp(IV/HV, 0.7, 1.5) × trend discount
Put spread score       =                 clamp(IV/HV, 0.7, 1.5) × trend discount
```

- `clamp` keeps IV/HV between 0.7 and 1.5 so extreme values do not dominate.
- **Trend discount**: × 0.75 when the price is below its 200-day average, and another × 0.75 when it fell more than 15% in a month.
- Spread scores leave out the return. Spreads at the same delta pay almost the same, and roughly the fair price of their risk, so only richness and trend are ranked.

The score is a transparent ranking rule, not a predictive model. It has not been fitted to historical data, and there is no evidence yet that higher-scoring trades do better afterwards.

## 6. Position monitoring (`monitor.py`)

For each open trade the current cost to close is taken: the option's mid for a single leg, short minus long for a spread.

| Condition | Alert |
|---|---|
| (sold for − now) ÷ sold for ≥ 50% | Take profit |
| (now − sold for) ≥ 2 × sold for | Stop loss |
| ≤ 21 days left | Consider closing or rolling |
| Price through the short strike | In the money |
| Spread: price below the long strike | Near maximum loss |
| Price within 2% of the strike | Near the strike |
| Earnings before expiry | Earnings risk |
| Total capital over 80% of the account, or one name over 50% | Over the position limit |

## 7. Backtest (`backtest.py`)

It simulates only the wheel (cash-secured puts and covered calls), **not spreads**.

On each trading day:

1. With an option open: settle it at expiry (an assigned put buys shares, an assigned call sells them); otherwise estimate its value and close it once 50% of the premium is captured.
2. With no option open: sell a put if holding fewer than 100 shares, otherwise sell a call. The strike is derived from the target delta and rounded to a common strike increment. Call strikes are kept at or above the cost basis.

Key assumption: free data has no historical option prices, so **option prices are estimated with Black-Scholes** using 20-day historical volatility × 1.1, with a 5% bid-ask haircut on every fill.

This ignores:

- the higher IV of out-of-the-money puts (skew);
- the IV spikes before earnings and crashes, which make real losses larger than the model's;
- fills that do not happen, or happen only at worse prices.

So the backtest is for comparing rules (delta, days, profit target), not for forecasting returns. Live results are usually worse.

## 8. Data and the errors it introduces

- Prices come from Yahoo Finance and are delayed about 15 minutes.
- The reference price is the mid of bid and ask; real fills are often worse, and large-cap option quotes are sometimes wide.
- Outside market hours bids and asks are often zero, and those contracts are skipped. Run the tool during US market hours.
- Yahoo rate limits. The tool retries, gives failed tickers one more try at the end of a scan, and lists any that still fail.

## 9. What the model does not do

- It does not predict direction. It only answers which contracts are relatively well priced under the rules.
- IV/HV above 1 does not guarantee profit. IV is sometimes high because the market knows something is coming.
- It does not handle correlation across positions. Puts sold on several tech stocks will lose together in a sell-off.
- It does not place orders.
