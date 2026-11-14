# Principles of option selling

This document explains the trading ideas behind the tool: selling puts, selling calls, the wheel and put credit spreads. It is general education, not investment advice.

## 1. Selling options is selling insurance

Buying an option buys a right. Selling one is the opposite: you collect money up front (the **premium**) and take on an obligation.

| | Sell a put | Sell a call |
|---|---|---|
| Your promise | Buy 100 shares at the strike if the price falls below it | Sell 100 shares at the strike if the price rises above it |
| Your view | No big drop | No big rally |
| Best outcome | Expires unexercised; you keep the whole premium | Expires unexercised; you keep the whole premium |
| Maximum gain | The premium (capped) | The premium (capped) |
| Maximum loss | Strike × 100 − premium (stock goes to zero) | With shares: the upside you give up. **Naked: unlimited in theory** |

The money comes from two places:

1. **Time decay.** An option loses value as expiry approaches, and the seller collects that decay every day.
2. **The volatility premium.** Over long periods the volatility priced into options (implied volatility, IV) tends to exceed the volatility that actually follows (historical volatility, HV). Sellers collect that gap on average.

The defining trait is **a high win rate with lopsided payoffs**: many small gains and the occasional large loss. The usual image is picking up pennies in front of a steamroller.

## 2. Four ways to do it

### Cash-secured put

Sell a put and keep strike × 100 in cash in the account.

Example: IBIT trades at $38.94. Sell the $35 put expiring in 35 days for $0.65.

- Cash tied up: $3,500. Premium: $65 (about 1.8%).
- IBIT above $35 at expiry: keep the $65.
- IBIT below $35 at expiry: buy 100 shares at $35, an effective cost of $34.35.
- If it falls to $28: a paper loss of ($34.35 − $28) × 100 = $635, roughly ten months of premium.

Only sell puts on something you **would be glad to own at the strike**.

### Covered call

Own 100 shares and sell a call with a strike above the current price. You collect the premium, and in return your upside is capped: above the strike, the shares are called away at the strike. The downside risk of holding the shares is unchanged.

### The wheel

Chain the two into a loop:

```
Sell a put ──(assigned)──▶ Own the shares ──▶ Sell a call ──(assigned)──▶ Back to cash ──▶ Repeat
```

It has two typical failure modes:

- **It lags in a bull market.** When the stock rallies the shares get called away and the rest of the rise is not yours.
- **It gets stuck in a bear market.** When the stock falls you buy at a high price. If you insist on selling calls only above your cost, the premiums get too small to be worth selling. The option leg can show a 100% win rate while the account loses money, because all the loss sits in the shares.

### Put credit spread

Sell a put and buy a put with a **lower strike** at the same time. The bought put is insurance that caps the maximum loss.

Example: MSFT trades at $524. Sell the 495 put and buy the 490 put for a net credit of $1.08.

- Above $495 at expiry: gain $1.08 × 100 = $108 (about $106 after fees).
- Below $490 at expiry: lose (5 − 1.08) × 100 = $392 (about $394 with fees).
- In between: the result moves proportionally; $493.92 is breakeven.

Spreads suit stocks that are too expensive to buy 100 shares of. Be clear about what they are:

- **They win roughly 70–80% of the time, and one loss costs about three wins.** Win rate × gain ≈ loss rate × loss. Traded at fair prices, the long-run expectation is close to zero.
- The edge comes only from options being overpriced, that is, when IV is clearly above HV.
- So do not judge a spread by its annualized return. $106 ÷ $394 is 27% a month, about 300% a year, and that number means nothing.
- If the price sits between the two strikes near expiry, close the spread. Otherwise the short leg is assigned and you suddenly own 100 shares.

## 3. Key concepts

| Concept | Meaning | How the tool uses it |
|---|---|---|
| **Delta** | How much the option price moves for a $1 move in the stock. Its absolute value is also roughly the chance of finishing in the money | Sell only \|delta\| between 0.15 and 0.30, preferring about 0.20 |
| **DTE** | Days to expiry | 25–45 days: decay is fast, and there is still time to adjust |
| **IV** (implied volatility) | The future volatility implied by the option price | Higher IV means more premium and more risk |
| **HV** (historical volatility) | The volatility that actually occurred recently | Compared with IV to judge whether options are expensive |
| **IV/HV** | The ratio of the two | Above 1 means options are pricier than actual movement; sellers have the edge |
| **Bid-ask spread** | The gap between the bid and the ask | Contracts with wide spreads are skipped; trading them gives away too much |

## 4. Rules for opening and managing

Opening:

- Skip any contract whose expiry falls after an earnings report. Earnings can gap the price before you can react.
- Keep the capital in any one name to half the account at most, and always hold 20% in cash.
- Use limit orders near the mid price.

Managing:

- **Take profit**: once 50% of the premium is captured, buy the option back and hand off the remaining risk.
- **Stop loss**: when the loss reaches twice the premium, close or roll. Do not hold and hope.
- **Time**: with about 21 days left and the target not reached, consider closing or rolling.

**Rolling** means buying back the current contract and selling one with a later expiry or a lower strike.

## 5. Risks

1. **Tail risk.** A year of steady gains can be given back in one crash. March 2020 and all of 2022 are examples.
2. **"Steady monthly income" is an illusion.** In a bull market selling puts looks like printing money; the market provided that, not the strategy.
3. **Margin and leverage.** Selling puts on margin can create exposure of three to five times your capital. A sharp drop brings margin calls and forced liquidation, locking in losses that can exceed the account. This tool always assumes full cash collateral.
4. **Naked calls have unlimited loss.** The trade journal refuses to record a call that is not covered by shares.
5. **Gaps.** Earnings, news and weekend events can open the price straight through the strike, before any stop rule can act.
6. **Early assignment.** US equity options can be exercised on any day before expiry; in-the-money calls just before an ex-dividend date are the common case.
7. **Liquidity.** Thinly traded contracts have wide spreads, and a round trip costs a lot.
8. **Taxes.** They depend on where you live. Ask a professional.

## 6. What is different about BTC

The tool gets BTC exposure through options on **IBIT**, BlackRock's spot bitcoin ETF.

- **Trading hours do not match.** BTC trades around the clock; IBIT trades only during US market hours. If BTC falls overnight or on a weekend, IBIT gaps down at the next open.
- **High volatility.** BTC is usually about twice as volatile as a large-cap stock. The premium is high because the risk is high.
- **No earnings**, but it has its own events: macro data, regulation, exchange failures.
- **Strong trends.** Once below its 200-day average, BTC often keeps falling for a long time. Selling puts into a downtrend is catching a falling knife.

## 7. Realistic expectations

Long-run backtests and index data (for example the CBOE PUT index) show that systematic put selling earns roughly what holding the S&P 500 earns, with somewhat lower volatility, and it still suffers deep drawdowns in crashes.

It trades part of the future upside for cash flow today. It is not a risk-free cash machine. Fully collateralized, 1–2% a month in premium is a realistic figure: about $100–200 a month on a $10,000 account.
