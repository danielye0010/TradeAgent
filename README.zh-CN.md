# TradeAgent

[English](README.md) | **简体中文**

**AI 分析师挑交易，经过测试的普通 Python 代码负责下单。**

TradeAgent 扫描美股和 ETF，让编程智能体 Codex 复核当天最异常的几只股票，再通过 Robinhood
官方的智能体交易接口，对其中最好的标的下单。仓位有上限，到时间自动卖出。每一个预测都在市场
给出结果*之前*记录下来，并和随机选股、SPY 做对比打分，AI 带来了多少价值一目了然。

[![CI](https://github.com/danielye0010/TradeAgent/actions/workflows/ci.yml/badge.svg)](https://github.com/danielye0010/TradeAgent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12%E2%80%933.14-blue)
![Status](https://img.shields.io/badge/status-beta-orange)
[![License](https://img.shields.io/badge/License-Apache--2.0%20AND%20MIT-blue)](LICENSE)

[两分钟试用](#两分钟试用) · [为什么选 TradeAgent](#为什么选-tradeagent) · [连接 Robinhood](#连接-robinhood) · [日常使用](#日常使用) · [内置安全机制](#内置安全机制) · [研究实验室](#研究实验室)

## 工作原理

```mermaid
flowchart LR
    A[行情数据<br/>31 只股票和 ETF] --> B[扫描器]
    B -->|异动前 3 名| C[Codex 复核]
    B --> D[量化信号]
    C --> E{交易计划}
    D --> E
    E -->|不交易| F[记录，事后打分]
    E -->|买入| G[风控检查]
    G --> H[Robinhood 下单]
    H --> I[定时退出并计算盈亏]
```

关键在于这种分工：**模型只决定哪些标的值得看，它永远碰不到券商接口。** 仓位大小、限额、
下单、退出和对账，都是每次行为完全一致的普通 Python 代码，并由 1000 多个离线测试覆盖。

## 为什么选 TradeAgent

TradeAgent 建立在已经好用的基础之上：数据和下单用 Robinhood
[官方智能体交易 MCP](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)，
分析用 [Codex](https://github.com/openai/codex)（走你的 ChatGPT 订阅，不按 token 计费），
交易日历用标准库。在此之上，它补上了下面这些方案都没有的部分：一条从 AI 建议到真实下单、再安全
退出的受保护通道，以及 AI 每一次判断的打分记录。

| 方案 | 擅长 | TradeAgent 带来什么 |
| --- | --- | --- |
| 把 ChatGPT、Claude 或 Codex 直接接到 Robinhood MCP，让它自己交易 | 用大白话下一次性订单 | 下单工具不在模型手里。限额由代码强制执行，每笔订单都先写入日志，超时也不会重复下单；退出提前排好，每次判断事后都会打分。 |
| “AI 对冲基金”类项目，比如 [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) 和 [TradingAgents](https://github.com/TauricResearch/TradingAgents) | 探索多智能体的市场分析 | 在真实账户里真正下单，分析走你的 ChatGPT 订阅，不需要按 token 付费的 API key。 |
| 非官方 Robinhood 库，比如 [robin_stocks](https://pypi.org/project/robin-stocks/) | 用脚本操作自己的账户 | 使用 Robinhood 官方接口和浏览器登录，TradeAgent 永远看不到你的密码。 |
| 回测框架（backtrader、vectorbt、LEAN 等） | 在多年历史数据上测试想法 | 实时证据：在结果出来之前就把预测冻结，结果不可能事后拟合。 |

## 两分钟试用

需要 Linux 或 Windows 上的 WSL2，以及 Python 3.12 及以上版本。不需要任何账户。

```bash
pipx install tradeagent         # Ubuntu/WSL 上先运行 sudo apt install pipx

tradeagent                      # 按用途分组列出所有命令
tradeagent demo --demo-dir ~/tradeagent-demo
tradeagent run-once --paper --state-dir ~/tradeagent-paper
```

这样就能用演示、Robinhood 登录和单笔实盘交易。Codex 分析师和每日自动驾驶要用到仓库里的
技能和安装脚本，所以需要克隆仓库：

```bash
git clone https://github.com/danielye0010/TradeAgent.git
cd TradeAgent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

每条命令都会输出完整的 JSON 记录；在终端里运行时，还会附一段简短的总结（目前是英文）：

```text
Demo passed: synthetic data only, 0 broker calls.
  150 forecasts recorded and scored, 15 learning runs, 5 challenger strategies created.

Paper round trip: COMPLETED (simulated broker, no real orders)
  buy  2 IWM @ 10.01 (filled)
  sell 2 IWM @ 9.99 (filled)
```

`tradeagent inspect --state-dir ~/tradeagent-demo` 可以查看演示实验学到了什么。状态目录要放在本地
Linux 磁盘上（ext4、btrfs 或 xfs）。网络共享盘和 `/mnt/c` 这类 Windows 挂载目录不受支持，
因为那里的 SQLite 文件锁不可靠。

## 连接 Robinhood

你需要一个状态正常的 Robinhood 个人投资账户。授权 TradeAgent 时，Robinhood 会引导你开一个
单独的**智能体账户**。TradeAgent 只在这个账户里交易，你的主账户完全不受影响。

```bash
tradeagent login        # 在浏览器里打开 Robinhood，授权 TradeAgent
tradeagent doctor       # 离线自检：哪些已配好，下一步该做什么
tradeagent live-check   # 只读检查：账户、权限、报价。永远不会下单。
```

`login` 使用 Robinhood 为 AI 应用提供的标准 OAuth 登录。密码只留在 Robinhood，TradeAgent
在 `~/.config/tradeagent/robinhood-oauth.json` 里保存一个短期 access token 和一个 refresh
token，文件权限为 600，仅你本人可读。它还会生成
`~/.config/tradeagent/tradeagent.toml`，这是控制真实交易的唯一配置文件：

```toml
[live]
symbols = ["QQQ", "IWM"]   # TradeAgent 唯一允许买入的标的
max_notional = "25"        # 单笔买入的硬性金额上限（美元）

[entry]
dollar_amount = "5"        # 每次实盘买入恰好花这么多

[exit]
hold_seconds = 3600        # 一小时后自动卖出
```

在没有浏览器的机器上，用 `tradeagent login --paste`，然后把 Robinhood 跳转后的地址粘贴回来。
`tradeagent logout` 会删除保存的登录状态。

## 日常使用

TradeAgent 有三种用法，从研究到实盘交易。只有你自己启动实盘运行时，才会动用真钱。

### 1. 让分析师（Codex）来分析

安装 [Codex CLI](https://github.com/openai/codex)，在 TradeAgent 文件夹里打开它，然后使用
`trade-opportunity-analyst` 技能。下面的指令原样输入即可，技能会识别其中的 `LIVE` 等关键词：

| 模式 | 输入 | 会发生什么 |
| --- | --- | --- |
| **研究** | `$trade-opportunity-analyst Analyze today's market and generate a TradePlan.` | 扫描市场，阅读候选标的的新闻，解释理由。不下单。 |
| **实盘** | `$trade-opportunity-analyst LIVE: analyze today's market, show the purchase plan, execute if eligible, and report P&L.` | 当某个策略在该情形下通过证据门槛时买入；否则给出附带理由的 `NO_TRADE`。 |
| **实验性实盘** | `$trade-opportunity-analyst EXPERIMENTAL LIVE: trade today's strongest quant signal with my $5 setting.` | 每天按最强的量化信号开最多一笔 5 美元的仓位，然后定时退出。真实成交单独记录，用于学习。 |

同样的步骤也可以手动执行：`tradeagent opportunity scan`、`decide`、`show` 和 `compare`。
详见[机会研究流程](docs/opportunity-workflow.md)。

### 2. 让每日自动驾驶做模拟交易

一个 systemd 定时器会在每个交易日自动运行研究流程，不需要打开聊天窗口，也不会真实下单。
它需要先运行 `tradeagent login`、用 ChatGPT 账户登录 Codex CLI；在 WSL2 上还需要
[启用 systemd](https://learn.microsoft.com/windows/wsl/systemd)：

```bash
python scripts/install_shadow.py --daily \
  --python "$PWD/.venv/bin/python" \
  --state-dir "$HOME/tradeagent-research/daily"
```

美东时间 10:00，它扫描市场并让 Codex 给前三名候选排序，然后记录四种互相竞争的选择。四者的
入场、退出时间和成本完全相同：

| 组别 | 怎么选 |
| --- | --- |
| **纯量化** | 量化信号最强的标的 |
| **纯 Codex** | Codex 排名第一的*看多*标的 |
| **量化 + Codex** | 两者都认可的最佳标的 |
| **随机** | 在同一批候选中用固定种子随机抽取，或持有现金 |

每次运行后，状态目录里的 `daily-report.md` 会列出每组扣除成本后的盈亏，以及同一小时内相对
SPY 的超额收益，Codex 比随机选股多带来多少，一看便知。另一个协议 `--protocol daily-news-v2`
让 Codex 分别在联网搜索和不联网的情况下回答同一个问题，从而准确衡量“读新闻”到底值多少。
详见[每日自动驾驶](docs/prospective-shadow.md)。

### 3. 自己下一笔单

```bash
tradeagent run-once --live   # 按配置买入一笔，然后自动退出
tradeagent status            # 只读查看当前这笔交易的状态
tradeagent recover           # 恢复被中断的交易；只会卖出
```

这是单独的执行路径，最快能在你自己的账户上看到下单、成交和退出跑通。完整说明见
[实盘执行](docs/one-shot.md)。

## 内置安全机制

AI 负责建议，确定性的代码负责决定。每次下单前，引擎都会检查：

- **硬性上限：** 你设定的金额、单笔上限、持仓和敞口限额、现金保留比例，以及每日亏损上限。
  任何金额都不会被自动调大；没有杠杆、没有做空、没有融资。
- **白名单：** 只交易配置里列出的标的，只在常规交易时段交易。
- **数据新鲜：** 账户、报价、买卖价差和盘口深度都是几秒内的数据，且彼此一致，订单才会发出。
- **不会重复下单：** 每笔订单在发出*之前*就带着唯一 ID 写入日志，并且绝不盲目重试，所以一次
  超时不会把一笔买单变成两笔。
- **内置退出路径：** 退出是同一次运行的一部分，在持有时间到期或临近收盘前执行。如果电脑休眠或
  断网，`tradeagent recover` 会接着这次运行完成退出。在状态目录
  （`~/.local/state/tradeagent/live`）里创建一个名为 `KILL` 的文件，就会停止买入并立即退出。

Robinhood 自己的审批设置同样生效。TradeAgent 还会自动跟进 Robinhood 的接口更新：逐个检查
它用到的工具，新增字段之类的变化照常工作，并在 `live-check` 里提示；如果某个变化可能改变
订单的含义，就暂停交易并给出明确原因。

## 研究实验室

在主流程之外，TradeAgent 还运行一组研究协议，各自使用独立的状态目录：

| 协议 | 研究问题 | 状态 |
| --- | --- | --- |
| `daily-four-arm-v1` | Codex 能否胜过量化信号和随机选股？ | 每日收集中 |
| `daily-news-v2` | 联网搜索新闻能否改进 Codex 的选择？ | 每日收集中 |
| `continuous-quant-v5` | 日内动量信号在 1–10 分钟的决策延迟后还有效吗？ | [文档](docs/continuous-v5.md) |
| `event-driven-v6` | Codex 阅读 SEC 公告能否胜过机械规则基线？ | [文档](docs/event-driven-v6.md) |
| `daily-bidirectional-v3`、`daily-call-put-v4` | 看空预测用看跌期权表达是否有效？ | 已就绪，Robinhood 开放期权报价后启用 |

## 项目结构

```text
.agents/skills/trade-opportunity-analyst/   Codex 技能
src/tradeagent/
  research/          扫描器、信号、交易计划、结果打分
  prospective/       每日自动驾驶和行情数据源
  oneshot.py         实盘买入、退出和恢复引擎
  risk.py            持仓、敞口和现金限额
  robinhood_login.py 浏览器登录和 token helper
scripts/             systemd 安装脚本和发布检查
docs/                设计说明和操作指南（英文）
```

延伸阅读：[架构](docs/architecture.md)、[研究协议](docs/research-protocol.md)、
[更新日志](CHANGELOG.md)、[发布流程](docs/releasing.md)。

## 开发

```bash
pip install -e ".[dev]"
pytest -q
ruff check src tests scripts && ruff format --check src tests scripts
```

测试使用合成行情、模拟券商和模拟 OAuth 服务器，可以离线运行，大约两分钟。CI 覆盖 Python 3.12
到 3.14，并对构建出的 wheel 做冒烟测试。参见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[Apache 2.0](LICENSE)，部分内置组件为 MIT 许可（[详情](THIRD_PARTY.md)）。TradeAgent 是独立
项目，与 Robinhood Markets, Inc. 和 OpenAI 均无关联。不构成投资建议；交易存在风险。
