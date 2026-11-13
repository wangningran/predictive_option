# predictive_option：BTC 和大盘股期权的开单研究工具

扫描两类标的，找出值得卖出的期权，并跟踪持仓：

- **BTC**：通过 IBIT（贝莱德比特币现货 ETF）的期权。它在 moomoo 可以交易，期权流动性好。
- **市值前 100 的美股**：在美国上市、市值最大的 100 家公司（含 TSM、ASML 等 ADR），名单每 7 天自动更新。

| 命令 | 作用 |
|---|---|
| `screen` | 扫描全部标的，分三块列出候选：BTC、现金担保 Put、Put 信用价差；持有正股时再加 Covered Call |
| `research NVDA` | 单只股票的研究卡片：走势、均线、波动率、财报日和全部候选合约 |
| `monitor` | 检查持仓：止盈、止损、临近或跌破行权价、财报、仓位超限 |
| `log` / `report` | 交易日志：记录开仓、平仓、到期、被行权，统计盈亏和胜率 |
| `backtest` | 用历史价格回测轮子策略（只支持现金担保 Put 和 Covered Call，不支持价差） |

> 本工具只做筛选、提醒和记录，**不会自动下单**。所有数字仅供学习参考，不构成投资建议。

**文档**：[期权卖方策略的原理](docs/principles.md) · [模型和计算方法](docs/models.md)

## 两种开仓方式

**现金担保 Put**：卖一张 Put，账户里留足"行权价 × 100"的现金。被行权就按行权价买入 100 股。只有股价较低的标的才做得起（1 万美元账户大约是 $50 以下，如 IBIT、BAC、PFE、VZ）。

**Put 信用价差**：大盘股大多每股几百美元，买不起 100 股，所以改成卖一张 Put、同时买一张行权价更低的 Put。

- 最大亏损固定 =（两个行权价之差 − 净收入）× 100 + 手续费，默认不超过 $500。
- 例子：MSFT 现价 $524，卖 495 Put、买 490 Put，净收入 $1.08。到期时股价高于 $495，赚 $106；低于 $490，亏 $394。
- **务必理解**：价差的胜率约 70%–80%，但亏一次大约等于赚三次。按中间价成交时，长期期望接近零。只有期权定价明显偏贵（IV/HV 大于 1）时才有优势。所以表格里不显示价差的"年化收益"，那个数字会严重误导。
- 需要券商开通价差权限。到期前如果股价落在两个行权价之间，要主动平仓，否则卖出腿会被行权。

## 表格里的研究指标

| 列 | 含义 |
|---|---|
| Delta / 胜率 | 卖出腿的 Delta；胜率 ≈ 1 − \|Delta\|，即到期时股价没跌破行权价的大致概率 |
| 资金 | 占用的资金。现金担保 Put 是行权价 × 100；价差是最大亏损 |
| 安全垫 | 股价跌多少才到盈亏平衡点 |
| IV/HV | 平值隐含波动率 ÷ 过去 20 天实际波动率。大于 1 说明期权比实际波动贵，卖方占优 |
| 提示 | 200 日线下方、1 个月跌幅超过 15%、IV 低于实际波动 |

每只股票只列一条：Delta 最接近 0.20 的合约。排序规则：现金担保 Put 按"年化收益 × IV/HV"，价差只按 IV/HV；处于 200 日线下方或 1 个月跌超 15% 的打 75 折。

**财报**：到期日之前有财报的合约一律不列。查不到财报日的股票会被排除，并在输出末尾列出原因。刚发完财报、下次日期还没公布的，按"上次 + 80 天"估算。

## 安装

需要 Python 3.11 或更高版本。

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q                                   # 应该全部通过
```

然后打开 `config.toml`，把 `fee_per_contract` 改成你在券商查到的实际费用。

## 日常用法

```bash
# 1. 开盘 45 分钟后扫描（约 1 分钟）
python -m wheel screen

# 2. 对感兴趣的标的看详情
python -m wheel research IBIT

# 3. 成交后记日志
python -m wheel log open IBIT put 35 2026-12-18 --premium 0.65                     # 现金担保 Put
python -m wheel log open MSFT put 495 2026-12-18 --long-strike 490 --premium 1.08  # 价差，premium 填净收入

# 4. 每天检查持仓
python -m wheel monitor

# 5. 按提醒操作后记录结果
python -m wheel log close 1 --price 0.30     # 买回平仓（价差填净支出）
python -m wheel log expire 1                 # 到期作废
python -m wheel log assign 2                 # 被行权（只适用于单腿 Put / Call）

# 6. 复盘
python -m wheel report
```

日志保存在本机的 `data/journal.csv` 和 `data/stock_ledger.csv`，可以用 Excel 打开。这个仓库是公开的，`data/` 已加入 `.gitignore`，不会被提交。

## 配置（`config.toml`）

- `[account]`：账户大小、现金储备比例、单只标的上限、手续费。
- `[rules]`：到期天数、Delta 范围、流动性门槛、止盈止损规则。
- `[spreads]`：单笔价差的最大亏损（默认 $500）和净收入门槛。
- `[universe]`：`universe_mode = "top100"` 扫市值前 100；改成 `"watchlist"` 只扫 `[watchlist]` 里的股票。`btc_tickers` 是 BTC 相关标的，始终扫描。

## BTC 部分的注意事项

- IBIT 只在美股交易时段交易，BTC 全天 24 小时交易。周末或夜间 BTC 大跌，IBIT 会在下一个开盘直接跳空，来不及调整。
- BTC 的波动率通常是大盘股的两倍，权利金高是因为风险高。
- 扫描结果会先显示 BTC 现价、近期涨跌、是否在 200 日线上方、实际波动率处于一年中的什么位置。

## 数据来源和限制

- 行情来自 Yahoo Finance（yfinance），免费，延迟约 15 分钟。**请在美股交易时段运行**，盘后的买卖报价经常是 0。
- Yahoo 会限流。工具会自动重试；市值榜拉不到时，改用缓存或内置的名单（`wheel/universe_snapshot.json`）。
- "参考价"是买卖报价的中间价。大盘股的期权价差有时很宽，实际成交价会更差，请用限价单。
- 回测里的期权价格由 Black-Scholes 模型估算，只能用来比较规则，不能当成收益预测。

## 推送提醒（可选）

设置 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID` 两个环境变量后，运行 `python -m wheel monitor --notify`。

## 代码结构

```
wheel/
  universe.py   扫描范围：BTC 标的 + 市值前 100（带缓存和内置名单）
  research.py   单只标的的走势和波动率指标
  screener.py   候选合约：现金担保 Put、Put 信用价差、Covered Call，以及打分
  data.py       行情数据源（Yahoo）
  bs.py         Black-Scholes 定价、Delta、隐含波动率
  journal.py    交易日志、持仓和盈亏
  monitor.py    持仓监控和提醒
  backtest.py   回测
  config.py     读取 config.toml
  cli.py        命令行
tests/          单元测试（用模拟数据，不需要联网）
docs/           原理和模型说明
```
