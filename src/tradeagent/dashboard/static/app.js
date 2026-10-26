/* TradeAgent dashboard: read-only views over local state. No build step, no dependencies. */
(() => {
  "use strict";

  const REFRESH_MS = 15000;
  const MIN_DAYS = 20;
  const OPEN_MIN = 570; // 9:30 ET
  const CLOSE_MIN = 960; // 16:00 ET

  // ------------------------------------------------------------------ copy

  const I18N = {
    en: {
      tabs: { home: "Account", overview: "Decisions", compare: "GPT vs Claude", activity: "Activity", research: "Research", trades: "Trades" },
      models: { codex: "Codex (GPT)", claude: "Claude" },
      updown: { us: "Green up", cn: "Red up" },
      home: {
        account: "Live account",
        noTrades: "Your account value and returns appear here after the first live trade.",
        asof: "Account value as Robinhood reported it, {when}.",
        chartLabel: "Account value over time",
        ranges: { "1w": "1W", "1m": "1M", "3m": "3M", all: "All", "1wLabel": "Past week", "1mLabel": "Past month", "3mLabel": "Past 3 months", allLabel: "All time" },
        history: "Recent trades",
        allTrades: "All trades",
        historyEmpty: "No live trades yet.",
        row: { closed: "Bought {buy} → sold {sell}", open: "Bought {cost} at {buy}", none: "{status}", holding: "Holding" },
        today: {
          label: "Today, {day}",
          holding: "Holding {sym}",
          bought: "Bought {cost} at {price}, {when}",
          buying: "Placing the order",
          sellsAt: "Sells automatically at {when}",
          done: "{sym} bought and sold",
          result: "{pnl} ({ret})",
          noTrade: "No trade today",
          problem: "Today's trade did not run",
          picked: "{model} picked {sym}",
          soon: "Buys at the research entry time, about 10:36 ET",
          waiting: "Waiting for today's pick",
          noPick: "No pick today",
          waitingLine: "{model} reads the news and decides at 10:30 ET",
          closedNow: "Market closed",
          holiday: "Market closed today",
          next: "Next trading day: {day}",
        },
        reasons: {
          cash: "{model} chose to stay in cash.",
          unavailable: "{model} did not answer in time, so nothing was bought.",
          noDecision: "The research did not run today (computer asleep or offline?).",
          notAllowed: "{model} picked {sym}, which is not on your list of allowed symbols.",
          already: "Today's trade was already attempted.",
          late: "The entry window had passed, so nothing was bought.",
        },
        auto: {
          title: "Autotrade",
          rule: "Follows",
          ruleValue: "{model} reading the news",
          next: "Next run",
          off: "Off",
          amount: "Per trade",
          hold: "Holds for",
          notInstalled: "Daily live trading is not set up.",
        },
        ai: { title: "{model} + news, paper results", perDay: "per day on average", days: "{n} of {need} trading days scored" },
        duel: { title: "GPT vs Claude", line: "{n} scored sessions, same picks on {same}", early: "Claude is running next to Codex; scores start after the first session." },
      },
      stop: ". ",
      lang: "中文",
      theme: { auto: "Auto", light: "Light", dark: "Dark" },
      skip: "Skip to content",
      close: "Close",
      pulse: {
        busy: "Running now: {name}",
        trade: "In a trade: {sym}, exits {when}",
        idle: "Waiting. Next run {when} ({rel})",
        idleNoTime: "Waiting for the next scheduled run",
        disabled: "Autopilot installed but switched off",
        none: "Nothing scheduled yet",
        offline: "Can't reach the dashboard server. Is `tradeagent dashboard` still running?",
      },
      banner: { errors: "Some state could not be read:" },
      today: {
        open: "Today's session",
        closed: "Market closed today",
        nextOpen: "Next session {day}, 9:30 ET",
        afterClose: "After the close",
        now: "now {time}",
        example: "With the autopilot on, every trading day runs on this schedule.",
        noteDone: "Today's decisions are frozen. Results land after the holding window.",
        noteWaiting: "Next step: {what} at {time} ET.",
        noteClosed: "Nothing runs on market holidays and weekends.",
        noteAllDone: "Everything scheduled for today has run.",
        roles: { decide: "Decide", resolve: "Score", report: "Daily report", run: "Run" },
      },
      decisions: {
        title: "Latest decisions",
        all: "Full activity",
        empty: "No research sessions yet.",
        topPick: "{model}'s top pick",
        cash: "cash",
        pending: "result pending",
        unavailable: "no answer",
      },
      arms: {
        codex_only: "Codex",
        codex_price: "Codex, price only",
        codex_news: "Codex + news",
        quant_codex: "Quant + Codex",
        quant_codex_news: "Quant + Codex + news",
        claude_news: "Claude + news",
        claude_price: "Claude, price only",
        quant_claude_news: "Quant + Claude + news",
        quant_only: "Quant",
        seeded_random: "Random",
      },
      protocols: {
        "daily-four-arm-v1": "Daily four-arm",
        "daily-news-v2": "Daily news test",
        "daily-news-claude-v1": "Claude vs Codex",
        "daily-bidirectional-v3": "Long/short test",
        "daily-call-put-v4": "Call/Put test",
        "continuous-quant-v5": "Intraday momentum",
        "event-driven-v6": "SEC filings",
        "prospective-shadow": "Market collector",
        "live-mirror": "Daily live mirror",
      },
      questions: {
        "daily-four-arm-v1": "Does Codex pick better than quant signals or a random pick from the same shortlist?",
        "daily-news-v2": "Does letting Codex read the news improve its picks?",
        "daily-news-claude-v1": "Same shortlist, same question, same hour: does Claude pick better than Codex (GPT)?",
      },
      evidence: { synthetic: "Test data", prospective: "Paper", replay: "Replay" },
      stance: { long: "long", avoid: "avoid", short: "short" },
      failures: {
        MISSED_DECISION_WINDOW: "Missed the decision window. Was the computer asleep or offline?",
        CODEX_ASSESSMENT_UNAVAILABLE: "Codex didn't answer in time; the other arms still count.",
        MARKET_EVIDENCE_UNAVAILABLE: "Market data was unavailable, so no decision was frozen.",
        INTERRUPTED: "The run was interrupted before it finished.",
        CLAUDE_ASSESSMENT_UNAVAILABLE: "Claude didn't answer in time (or isn't signed in); the other arms still count.",
        V2_SCAN_UNAVAILABLE: "The Codex research saved no shortlist this session, so Claude was not asked.",
        V2_DECISION_UNAVAILABLE: "The Codex research froze no decision this session, so nothing could be scored.",
        LATE_START: "Claude started too long after the shortlist was frozen, so the session was skipped to keep it fair.",
      },
      jobs: {
        title: "Autopilot",
        running: "Running now",
        next: "Next run {when}",
        off: "Timer is off",
        unknown: "Timer state unknown (systemd not reachable)",
        last: "Last session {day}: {status}",
        none: "No autopilot yet. Once installed it paper-trades every session and scores Codex against Quant and Random.",
      },
      trading: {
        title: "Live trading",
        notConnected: "Not connected to Robinhood yet.",
        symbols: "Allowed symbols",
        perEntry: "Per entry",
        cap: "Hard cap",
        hold: "Holds for",
        kill: "KILL switch",
        killOn: "On: buying is stopped",
        killOff: "Off",
        inTrade: "In a trade",
        lastRun: "Last run",
        noRuns: "No live runs yet.",
        exitsAt: "exits {when}",
      },
      setup: {
        title: "Getting started",
        steps: {
          install: "Install TradeAgent",
          login: "Sign in to Robinhood",
          live_check: "Run the read-only live check",
          autopilot: "Turn on the daily autopilot",
          first_result: "Get the first result",
        },
        ready: "Setup checks pass.",
        check: "Setup checks",
      },
      snapshot: { title: "Where the comparison stands", more: "Open research" },
      research: {
        title: "Research",
        lede: "Every pick is frozen before the market answers, then scored after costs against SPY over the same hour. One trading session is one observation.",
        progress: "{n} of {need} paired sessions",
        progressDone: "{n} paired sessions",
        firstRead: "Verdicts appear at {need} paired sessions.",
        enough: "Enough sessions for a first read; more sessions narrow the range.",
        arm: "Arm",
        took: "Took a position",
        avgNet: "Avg net per session",
        vsSpy: "Vs SPY, per session",
        vsBase: "Vs {base}",
        vsPaired: "Vs its pair",
        vsArm: "vs {arm}",
        baseline: "baseline",
        chartTitle: "Cumulative net return, per group",
        chartNote: "Each session's return after costs, added up day by day.",
        chartGate: "The chart appears after the second scored session ({n} so far).",
        chartEarly: "Only {n} of {need} sessions so far: the line can still swing a lot.",
        recent: "Recent sessions",
        date: "Session",
        pick: "{model} pick",
        none: "No research state found.",
        noneHint: "Turn on the autopilot and the daily comparison shows up here:",
        lab: "Strategy lab",
        labLede: "The learning loop behind the quant signals: forecasts, scored outcomes and the weights it currently gives each strategy.",
        forecasts: "Forecasts",
        scored: "Scored",
        learning: "Learning runs",
        challengers: "Challengers",
        weights: "Current strategy weights",
        verdict: {
          collecting: "collecting {d}/{n}",
          ahead: "ahead",
          behind: "behind",
          unclear: "no clear difference",
        },
        ci: "95% range {lo} to {hi}",
        star: "★ marks a challenger: a mutated version the lab is testing against its parent.",
        ownReport: "This protocol scores long and short sleeves with its own method; the dashboard lists its sessions, and the full scoring is in its report:",
      },
      activity: {
        title: "Activity",
        filters: { all: "All", session: "AI decisions", trade: "Trades", problem: "Problems" },
        session: "{proto}: {status}",
        picked: "{model} ranked {sym} first ({stance}).",
        armsPicked: "Positions: {list}.",
        scored: "Scored: {list}.",
        lab: "Strategy lab learned from {n} forecasts ({runs} learning runs).",
        trade: "{mode} trade {sym}: {status}",
        pnl: "P&L {pnl}",
        empty: "Nothing here yet.",
        details: "Details",
      },
      trades: {
        title: "Trades",
        lede: "Live trades, newest first; the totals cover every trade. Select one for its fills and timeline.",
        total: "Total profit",
        count: "Closed trades",
        winRate: "Win rate",
        paper: "Paper and test runs",
        detail: { bought: "Bought", sold: "Sold", amount: "Amount", shares: "Shares", rule: "Chosen by", manual: "Manual run", status: "Status", reconciled: "Account reconciled" },
        started: "Started",
        mode: "Mode",
        status: "Status",
        symbol: "Symbol",
        buy: "Buy",
        sell: "Sell",
        pnl: "P&L",
        reconciled: "Reconciled",
        yes: "yes",
        no: "no",
        empty: "No trades yet. Once signed in and checked, one command places a trade:",
        timeline: "What happened",
        orders: "Orders",
        events: {
          proposal: "Order proposed",
          risk_pass: "Risk checks passed",
          human_approval: "Order approved",
          submission_started: "Order sent to the broker",
          fill_reconciliation: "Fill confirmed against the account",
          exit_due: "Exit scheduled",
        },
      },
      drawer: {
        frozen: "Frozen at {time}, {delay} before the entry window opened and {hold} before the exit. Nothing here can change afterwards.",
        outcome: "How each arm did",
        candidates: "The shortlist",
        price: "{model}, price data",
        news: "{model}, with news",
        thesis: "Thesis",
        catalyst: "Catalyst",
        pricedIn: "Priced in?",
        invalidation: "Would be wrong if",
        risks: "Against it",
        sources: "Sources",
        windowReturn: "1h move",
        rel: "vs SPY",
        volume: "volume",
        noAnalysis: "No {model} note for this symbol.",
        noNote: "No note.",
        net: "net {v}",
        excess: "over SPY {v}",
        loading: "Loading…",
        missing: "This session could not be loaded.",
      },
      duel: {
        title: "GPT vs Claude",
        lede: "Every trading day both models get the same three stocks, the same market evidence and the same question at 10:30 ET. Each pick is scored over the same hour with the same costs. Research only: no money is traded on this page.",
        modes: { news: "With news", price: "Price only" },
        codex: "GPT",
        claude: "Claude",
        via: { codex: "Codex CLI · {model}", claude: "Claude Code · {model}" },
        total: "added up over {n} scored sessions",
        wins: "better on {n}",
        ties: "{n} even",
        verdict: {
          collecting: "Too early to call: {d} of {n} sessions",
          ahead: "Claude is ahead of GPT",
          behind: "GPT is ahead of Claude",
          unclear: "No clear difference so far",
        },
        ci: "Claude minus GPT, per session: 95% range {lo} to {hi}",
        same: "Same pick on {same} of {n} sessions where both answered.",
        chart: "Cumulative net return",
        chartNote: "Each session's modeled return after costs, added up. A gap over a few sessions is mostly luck; the verdict waits for {need} sessions.",
        sessions: "Session by session",
        better: "better",
        even: "even",
        trading: "Your live autotrade follows {model}.",
        none: "Claude isn't running next to Codex yet.",
        setupTitle: "Set up Claude",
        setup: [
          "Install Claude Code, then run `claude` once and sign in with your Claude subscription (no API key).",
          "Check it: `tradeagent claude doctor`",
          "Start the daily comparison: `tradeagent claude install --enable`",
          "Optional, real money: trade Claude's pick instead of Codex's: `tradeagent autotrade install --model claude --enable`",
        ],
      },
      foot: { updated: "Updated {rel}" },
      rel: { now: "just now", s: "{n} s ago", m: "{n} min ago", h: "{n} h ago", inM: "in {n} min", inH: "in {h} h {m} min", inD: "in {n} days" },
      status: { COMPLETED: "completed", INCOMPLETE: "incomplete", STARTED: "running", HALTED: "halted", CLOSED_PARTIAL: "partly closed", NO_TRADE: "no trade", UNKNOWN: "unknown" },
      empty: {
        title: "Welcome to TradeAgent",
        body: "Sign in to Robinhood and turn on the autopilot, and status, AI decisions and trades show up here. A few steps to get going:",
      },
    },
    zh: {
      tabs: { home: "账户", overview: "AI 决策", compare: "GPT vs Claude", activity: "动态", research: "研究", trades: "交易记录" },
      models: { codex: "Codex（GPT）", claude: "Claude" },
      updown: { us: "绿涨红跌", cn: "红涨绿跌" },
      home: {
        account: "实盘账户",
        noTrades: "第一笔实盘交易完成后，这里显示账户价值和收益。",
        asof: "账户价值来自 Robinhood，截至 {when}。",
        chartLabel: "账户价值走势",
        ranges: { "1w": "1周", "1m": "1月", "3m": "3月", all: "全部", "1wLabel": "近一周", "1mLabel": "近一月", "3mLabel": "近三月", allLabel: "全部" },
        history: "最近交易",
        allTrades: "全部交易",
        historyEmpty: "还没有实盘交易。",
        row: { closed: "买 {buy} → 卖 {sell}", open: "{buy} 买入 {cost}", none: "{status}", holding: "持有中" },
        today: {
          label: "今天 {day}",
          holding: "持有 {sym}",
          bought: "{when} 以 {price} 买入 {cost}",
          buying: "正在下单",
          sellsAt: "{when} 自动卖出",
          done: "{sym} 已买入并卖出",
          result: "{pnl}（{ret}）",
          noTrade: "今天不交易",
          problem: "今天的交易没有执行",
          picked: "{model} 选了 {sym}",
          soon: "按研究的入场时间买入，约美东 10:36",
          waiting: "等待今天的选择",
          noPick: "今天没有选择",
          waitingLine: "{model} 美东 10:30 读新闻后做决定",
          closedNow: "已收盘",
          holiday: "今天休市",
          next: "下个交易日：{day}",
        },
        reasons: {
          cash: "{model} 选择空仓。",
          unavailable: "{model} 没有按时给出答案，所以没有买入。",
          noDecision: "今天研究没有运行（电脑是否休眠或断网？）。",
          notAllowed: "{model} 选了 {sym}，不在你允许交易的标的里。",
          already: "今天已经尝试过下单。",
          late: "错过了入场时间，所以没有买入。",
        },
        auto: {
          title: "自动交易",
          rule: "跟随",
          ruleValue: "读新闻的 {model}",
          next: "下次运行",
          off: "已关闭",
          amount: "每笔金额",
          hold: "持有时间",
          notInstalled: "还没有开启每日实盘。",
        },
        ai: { title: "{model} 读新闻 · 模拟盘表现", perDay: "日均", days: "已计分 {n}/{need} 个交易日" },
        duel: { title: "GPT vs Claude", line: "已计分 {n} 个交易日，其中 {same} 天选得一样", early: "Claude 已在 Codex 旁边运行，第一个交易日后开始计分。" },
      },
      stop: "。",
      checks: {
        System: "系统",
        Storage: "数据目录",
        "Robinhood sign-in": "Robinhood 登录",
        "Live settings": "实盘配置",
        "Codex CLI": "Codex CLI",
        "Claude Code": "Claude Code",
        "Autopilot timer": "Autopilot 定时器",
      },
      fixes: {
        System: "请在 Linux 或 WSL2 中运行，Python 版本需 3.12 或以上。",
        Storage: "把数据目录放在 Linux 本地磁盘上（ext4、btrfs 或 xfs），不要放在 `/mnt/c` 这类 Windows 目录。",
        "Codex CLI": "从 https://github.com/openai/codex 安装后运行 `codex login`。",
        "Claude Code": "安装 Claude Code 后运行一次 `claude`，用 Claude 订阅登录，再运行 `tradeagent claude doctor`。",
        "Autopilot timer": "在 WSL 中启用 systemd：https://learn.microsoft.com/windows/wsl/systemd",
      },
      lang: "EN",
      theme: { auto: "跟随系统", light: "浅色", dark: "深色" },
      skip: "跳到正文",
      close: "关闭",
      pulse: {
        busy: "运行中：{name}",
        trade: "持仓中：{sym}，{when} 平仓",
        idle: "下次运行 {when}（{rel}）",
        idleNoTime: "等待下一次定时运行",
        disabled: "Autopilot 已安装，定时器未开启",
        none: "尚未安装定时任务",
        offline: "连接不到 dashboard 服务，请确认 `tradeagent dashboard` 仍在运行",
      },
      banner: { errors: "部分数据读取失败：" },
      today: {
        open: "今日交易时段",
        closed: "今日休市",
        nextOpen: "下一交易日 {day}，美东 9:30 开盘",
        afterClose: "收盘后",
        now: "当前 {time}",
        example: "开启 Autopilot 后，每个交易日按这个日程运行。",
        noteDone: "今日决策已锁定，持有期结束后结算。",
        noteWaiting: "下一步：美东 {time} {what}。",
        noteClosed: "周末和休市日不运行。",
        noteAllDone: "今日任务已全部完成。",
        roles: { decide: "决策", resolve: "结算", report: "日报", run: "运行" },
      },
      decisions: {
        title: "最新决策",
        all: "全部动态",
        empty: "还没有决策记录。",
        topPick: "{model} 首选",
        cash: "空仓",
        pending: "待结算",
        unavailable: "无结果",
      },
      arms: {
        codex_only: "Codex",
        codex_price: "Codex（仅价格）",
        codex_news: "Codex + 新闻",
        quant_codex: "量化 + Codex",
        quant_codex_news: "量化 + Codex + 新闻",
        claude_news: "Claude + 新闻",
        claude_price: "Claude（仅价格）",
        quant_claude_news: "量化 + Claude + 新闻",
        quant_only: "量化",
        seeded_random: "随机",
      },
      protocols: {
        "daily-four-arm-v1": "每日四组对比",
        "daily-news-v2": "新闻对比",
        "daily-news-claude-v1": "Claude 对比 Codex",
        "daily-bidirectional-v3": "多空实验",
        "daily-call-put-v4": "期权实验",
        "continuous-quant-v5": "日内动量",
        "event-driven-v6": "SEC 公告",
        "prospective-shadow": "行情采集",
        "live-mirror": "每日实盘跟单",
      },
      questions: {
        "daily-four-arm-v1": "同一批候选，Codex 的选择能否胜过量化信号和随机选股？",
        "daily-news-v2": "让 Codex 读新闻，选股会更准吗？",
        "daily-news-claude-v1": "同一批候选、同一个问题、同一个小时：Claude 选得比 Codex（GPT）好吗？",
      },
      evidence: { synthetic: "测试数据", prospective: "模拟盘", replay: "回放" },
      stance: { long: "看多", avoid: "回避", short: "看空" },
      failures: {
        MISSED_DECISION_WINDOW: "错过决策窗口，可能是电脑休眠或断网。",
        CODEX_ASSESSMENT_UNAVAILABLE: "Codex 未按时返回，其他组照常计分。",
        MARKET_EVIDENCE_UNAVAILABLE: "行情数据缺失，当天未生成决策。",
        INTERRUPTED: "运行中途被中断。",
        CLAUDE_ASSESSMENT_UNAVAILABLE: "Claude 未按时返回（或未登录），其他组照常计分。",
        V2_SCAN_UNAVAILABLE: "Codex 研究当天没有保存候选名单，所以没有问 Claude。",
        V2_DECISION_UNAVAILABLE: "Codex 研究当天没有锁定决策，无法计分。",
        LATE_START: "Claude 在候选名单锁定后启动太晚，为保证公平跳过当天。",
      },
      jobs: {
        title: "Autopilot",
        running: "运行中",
        next: "下次运行 {when}",
        off: "定时器未开启",
        unknown: "无法读取定时器状态（systemd 不可用）",
        last: "上次 {day}：{status}",
        none: "尚未安装 Autopilot。安装后每个交易日自动跑一轮模拟盘，给 Codex、量化和随机选股打分。",
      },
      trading: {
        title: "实盘",
        notConnected: "尚未连接 Robinhood。",
        symbols: "交易标的",
        perEntry: "单笔金额",
        cap: "金额上限",
        hold: "持有时长",
        kill: "KILL 开关",
        killOn: "已开启，暂停买入",
        killOff: "关闭",
        inTrade: "持仓中",
        lastRun: "最近一笔",
        noRuns: "还没有实盘交易。",
        exitsAt: "{when} 平仓",
      },
      setup: {
        title: "开始使用",
        steps: {
          install: "安装 TradeAgent",
          login: "登录 Robinhood",
          live_check: "运行只读检查",
          autopilot: "开启 Autopilot",
          first_result: "拿到第一笔结果",
        },
        ready: "环境检查全部通过。",
        check: "环境检查",
      },
      snapshot: { title: "对比进展", more: "查看研究" },
      research: {
        title: "研究",
        lede: "每个选择都在结果揭晓前锁定，扣除成本后与同一时段的 SPY 对比，每个交易日计为一个样本。",
        progress: "已配对 {n}/{need} 个交易日",
        progressDone: "已配对 {n} 个交易日",
        firstRead: "满 {need} 个交易日后给出结论。",
        enough: "样本已足够初步判断，区间会随样本增加继续收窄。",
        arm: "组别",
        took: "建仓天数",
        avgNet: "日均净收益",
        vsSpy: "相对 SPY（日均）",
        vsBase: "对比{base}",
        vsPaired: "对比组",
        vsArm: "对比 {arm}",
        baseline: "基准",
        chartTitle: "各组累计净收益",
        chartNote: "每天扣除成本后的收益，逐日累加。",
        chartGate: "第二个计分交易日后显示曲线（目前 {n} 个）。",
        chartEarly: "目前只有 {n}/{need} 个交易日，曲线还会大幅波动。",
        recent: "最近交易日",
        date: "交易日",
        pick: "{model} 首选",
        none: "还没有研究数据。",
        noneHint: "开启 Autopilot 后，每天的对比结果会显示在这里：",
        lab: "策略实验室",
        labLede: "量化信号背后的学习循环：预测数量、已结算数量，以及当前各策略的权重。",
        forecasts: "预测",
        scored: "已结算",
        learning: "学习轮次",
        challengers: "挑战者",
        weights: "当前策略权重",
        verdict: {
          collecting: "样本 {d}/{n}",
          ahead: "领先",
          behind: "落后",
          unclear: "暂无明显差异",
        },
        ci: "95% 区间 {lo} 至 {hi}",
        star: "★ 为挑战者：从原策略变异而来、正在与原版对比的新版本。",
        ownReport: "该实验按多空两侧分别计分，这里列出每个交易日，完整成绩见报告：",
      },
      activity: {
        title: "动态",
        filters: { all: "全部", session: "AI 决策", trade: "交易", problem: "异常" },
        session: "{proto}：{status}",
        picked: "{model} 首选 {sym}（{stance}）。",
        armsPicked: "各组仓位：{list}。",
        scored: "结算：{list}。",
        lab: "策略实验室基于 {n} 条预测完成 {runs} 轮学习。",
        trade: "{mode} {sym}：{status}",
        pnl: "盈亏 {pnl}",
        empty: "暂无记录。",
        details: "详情",
      },
      trades: {
        title: "交易记录",
        lede: "实盘交易，最新的在前；累计数字覆盖全部交易。点开查看成交明细和时间线。",
        total: "累计盈亏",
        count: "已平仓",
        winRate: "胜率",
        paper: "模拟和测试运行",
        detail: { bought: "买入", sold: "卖出", amount: "金额", shares: "股数", rule: "选股来源", manual: "手动运行", status: "状态", reconciled: "账户已对账" },
        started: "时间",
        mode: "模式",
        status: "状态",
        symbol: "标的",
        buy: "买入",
        sell: "卖出",
        pnl: "盈亏",
        reconciled: "已对账",
        yes: "是",
        no: "否",
        empty: "还没有交易。完成登录和只读检查后，一条命令即可下单：",
        timeline: "执行过程",
        orders: "订单",
        events: {
          proposal: "生成订单",
          risk_pass: "风控检查通过",
          human_approval: "订单确认",
          submission_started: "已提交券商",
          fill_reconciliation: "成交与账户核对一致",
          exit_due: "已安排平仓",
        },
      },
      drawer: {
        frozen: "{time} 锁定，距入场窗口 {delay}，距平仓 {hold}。锁定后不可更改。",
        outcome: "各组表现",
        candidates: "候选名单",
        price: "{model}（仅价格）",
        news: "{model}（含新闻）",
        thesis: "核心逻辑",
        catalyst: "催化剂",
        pricedIn: "是否已计入价格",
        invalidation: "失效条件",
        risks: "反方因素",
        sources: "来源",
        windowReturn: "近 1 小时",
        rel: "相对 SPY",
        volume: "量比",
        noAnalysis: "{model} 未分析该标的。",
        noNote: "没有分析。",
        net: "净收益 {v}",
        excess: "超额 {v}",
        loading: "加载中…",
        missing: "无法加载该交易日。",
      },
      duel: {
        title: "GPT vs Claude",
        lede: "每个交易日美东 10:30，两个模型拿到同样的三只股票、同样的行情证据和同一个问题；选出的股票按同一个小时、同样的成本计分。这里只是研究，不下单。",
        modes: { news: "读新闻", price: "仅价格" },
        codex: "GPT",
        claude: "Claude",
        via: { codex: "Codex CLI · {model}", claude: "Claude Code · {model}" },
        total: "{n} 个计分交易日累计",
        wins: "{n} 天更好",
        ties: "{n} 天持平",
        verdict: {
          collecting: "样本还太少：{d}/{n} 个交易日",
          ahead: "Claude 领先 GPT",
          behind: "GPT 领先 Claude",
          unclear: "目前没有明显差异",
        },
        ci: "Claude 减 GPT（每个交易日）：95% 区间 {lo} 至 {hi}",
        same: "两边都给出答案的 {n} 个交易日里，有 {same} 天选得一样。",
        chart: "累计净收益",
        chartNote: "每个交易日扣除成本后的模拟收益逐日累加。几天的差距多半是运气，满 {need} 个交易日才给结论。",
        sessions: "逐日对比",
        better: "更好",
        even: "持平",
        trading: "你的实盘自动交易跟随 {model}。",
        none: "Claude 还没有在 Codex 旁边运行。",
        setupTitle: "开启 Claude",
        setup: [
          "安装 Claude Code，运行一次 `claude`，用 Claude 订阅登录（不需要 API key）。",
          "检查：`tradeagent claude doctor`",
          "开启每日对比：`tradeagent claude install --enable`",
          "可选（真钱）：改为跟随 Claude 的选择下单：`tradeagent autotrade install --model claude --enable`",
        ],
      },
      foot: { updated: "{rel}更新" },
      rel: { now: "刚刚", s: "{n} 秒前", m: "{n} 分钟前", h: "{n} 小时前", inM: "{n} 分钟后", inH: "{h} 小时 {m} 分后", inD: "{n} 天后" },
      status: { COMPLETED: "完成", INCOMPLETE: "未完成", STARTED: "进行中", HALTED: "已停止", CLOSED_PARTIAL: "部分平仓", NO_TRADE: "未交易", UNKNOWN: "未知" },
      empty: {
        title: "欢迎使用 TradeAgent",
        body: "登录 Robinhood、开启 Autopilot 后，运行状态、AI 决策和交易记录都会显示在这里。按下面几步开始：",
      },
    },
  };

  const store = {
    get(key, fallback) {
      try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(key, value); } catch (e) { /* storage blocked */ }
    },
  };

  let lang = store.get("ta.lang", (navigator.language || "en").toLowerCase().startsWith("zh") ? "zh" : "en");
  let theme = store.get("ta.theme", "auto");
  let updown = store.get("ta.updown", lang === "zh" ? "cn" : "us");
  const TABS = ["home", "overview", "compare", "research", "trades"];
  const ROUTES = [...TABS, "activity"];
  const state = { data: null, etag: null, fetchedAt: 0, offline: false, route: "home", filter: "all", range: "all", duel: store.get("ta.duel", "news") };
  const CLAUDE_P = "daily-news-claude-v1";
  // Which research record and arm each autotrade model follows.
  const FOLLOW = { codex: { protocol: "daily-news-v2", arm: "codex_news" }, claude: { protocol: CLAUDE_P, arm: "claude_news" } };
  const followedModel = () => (state.data?.autotrade?.model in FOLLOW ? state.data.autotrade.model : "codex");
  const modelName = (m) => t("models." + m);
  // Whose notes a protocol's price/news analyses are.
  const analystOf = (protocol) => (protocol === CLAUDE_P ? "Claude" : "Codex");

  function t(path, vars) {
    const pick = (table) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), table);
    let text = pick(I18N[lang]);
    if (text == null) text = pick(I18N.en);
    if (text == null) return path;
    if (typeof text !== "string") return text;
    return text.replace(/\{(\w+)\}/g, (_, k) => (vars && vars[k] != null ? String(vars[k]) : ""));
  }

  // ------------------------------------------------------------------ DOM helpers

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "style") {
        // CSSOM writes are allowed under the strict style-src policy; style attributes are not.
        for (const decl of String(v).split(";")) {
          const at = decl.indexOf(":");
          if (at > 0) el.style.setProperty(decl.slice(0, at).trim(), decl.slice(at + 1).trim());
        }
      }
      else el.setAttribute(k, v === true ? "" : v);
    }
    append(el, children);
    return el;
  }

  function append(el, children) {
    for (const child of children.flat(Infinity)) {
      if (child == null || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  const SVG = "http://www.w3.org/2000/svg";
  function s(tag, attrs, ...children) {
    const el = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
    for (const child of children.flat()) {
      if (child == null) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  // ------------------------------------------------------------------ formatting

  const locale = () => (lang === "zh" ? "zh-CN" : "en-US");
  function pct(value, digits = 2) {
    if (value == null || !isFinite(value)) return "—";
    const v = value * 100;
    const sign = v > 0 ? "+" : v < 0 ? "−" : "";
    return sign + Math.abs(v).toFixed(digits) + "%";
  }
  function money(value) {
    if (value == null || !isFinite(value)) return "—";
    const sign = value > 0 ? "+" : value < 0 ? "−" : "";
    return sign + "$" + Math.abs(value).toFixed(2);
  }
  function plainMoney(value) {
    return value == null ? "—" : "$" + Number(value).toFixed(value % 1 ? 2 : 0);
  }
  function tone(value) {
    return value == null || value === 0 ? "" : value > 0 ? "pos" : "neg";
  }
  function fmtTime(ts, opts = {}) {
    if (!ts) return "—";
    return new Intl.DateTimeFormat(locale(), { hour: "numeric", minute: "2-digit", ...opts }).format(new Date(ts * 1000));
  }
  function fmtNY(ts) {
    return fmtTime(ts, { timeZone: "America/New_York" });
  }
  function fmtDateTime(ts) {
    if (!ts) return "—";
    return new Intl.DateTimeFormat(locale(), { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(ts * 1000));
  }
  function fmtDay(iso) {
    if (!iso) return "—";
    const [y, m, d] = iso.split("-").map(Number);
    return new Intl.DateTimeFormat(locale(), { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(y, m - 1, d)));
  }
  function minutesLabel(min) {
    const hh = Math.floor(min / 60), mm = min % 60;
    const d = new Date(Date.UTC(2000, 0, 1, hh, mm));
    return new Intl.DateTimeFormat(locale(), { hour: "numeric", minute: "2-digit", timeZone: "UTC" }).format(d);
  }
  function duration(seconds) {
    const m = Math.round(Math.abs(seconds) / 60);
    if (m < 60) return lang === "zh" ? `${m} 分钟` : `${m} min`;
    const hh = Math.floor(m / 60), mm = m % 60;
    if (lang === "zh") return mm ? `${hh} 小时 ${mm} 分` : `${hh} 小时`;
    return mm ? `${hh} h ${mm} min` : `${hh} h`;
  }
  function rel(ts) {
    const diff = ts - Date.now() / 1000;
    const a = Math.abs(diff);
    if (diff >= 0) {
      if (a < 3600) return t("rel.inM", { n: Math.max(1, Math.round(a / 60)) });
      if (a < 86400 * 2) return t("rel.inH", { h: Math.floor(a / 3600), m: Math.round((a % 3600) / 60) });
      return t("rel.inD", { n: Math.round(a / 86400) });
    }
    if (a < 10) return t("rel.now");
    if (a < 60) return t("rel.s", { n: Math.round(a) });
    if (a < 3600) return t("rel.m", { n: Math.round(a / 60) });
    return t("rel.h", { n: Math.round(a / 3600) });
  }
  const protoName = (p) => t("protocols." + p) === "protocols." + p ? p : t("protocols." + p);
  const armName = (a) => t("arms." + a) === "arms." + a ? a : t("arms." + a);
  const statusName = (s) => (t("status." + s) === "status." + s ? String(s || "").toLowerCase() : t("status." + s));

  const ARM_COLOR = {
    codex_only: "var(--codex)",
    codex_price: "var(--codex)",
    codex_news: "var(--codex-news)",
    quant_codex: "var(--combo)",
    quant_codex_news: "var(--combo)",
    claude_news: "var(--claude-news)",
    claude_price: "var(--claude)",
    quant_claude_news: "var(--combo)",
    quant_only: "var(--quant)",
    seeded_random: "var(--random)",
  };
  const armColor = (a) => ARM_COLOR[a] || "var(--muted)";

  function evidenceBadge(kind) {
    if (!kind) return null;
    const cls = kind === "synthetic" ? "synthetic" : "shadow";
    return h("span", { class: "badge " + cls, text: t("evidence." + kind) });
  }
  function modeBadge(mode, synthetic) {
    const list = [h("span", { class: "badge " + (mode === "LIVE" ? "live" : "paper"), text: mode })];
    if (synthetic) list.push(h("span", { class: "badge synthetic", text: t("evidence.synthetic") }));
    return list;
  }

  // ------------------------------------------------------------------ data

  async function load() {
    try {
      const headers = state.etag ? { "If-None-Match": state.etag } : {};
      const res = await fetch("/api/overview", { headers, cache: "no-store" });
      state.offline = false;
      if (res.status === 304) {
        state.fetchedAt = Date.now();
        // Same data, but the clock moved: keep the session ribbon current.
        if ((state.route === "overview" || state.route === "home") && document.getElementById("drawer").hidden) render();
        else { renderPulse(); renderFoot(); }
        return;
      }
      if (!res.ok) throw new Error("HTTP " + res.status);
      state.etag = res.headers.get("ETag");
      state.data = await res.json();
      state.fetchedAt = Date.now();
      render();
    } catch (err) {
      state.offline = true;
      renderPulse();
    }
  }

  function sessions() {
    return (state.data?.activity || []).filter((i) => i.kind === "session");
  }

  // ------------------------------------------------------------------ chrome

  function renderChrome() {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.querySelector(".skip").textContent = t("skip");
    const tabs = document.getElementById("tabs");
    tabs.replaceChildren(
      ...TABS.map((r) =>
        h("a", { href: "#/" + r, "aria-current": state.route === r ? "page" : null, text: t("tabs." + r) }),
      ),
    );
    document.getElementById("lang").textContent = t("lang");
    const weeklyLink = document.getElementById("weekly-link");
    if (weeklyLink) weeklyLink.textContent = lang === "zh" ? "周度持仓" : "Weekly portfolios";
    document.getElementById("theme").textContent = "◐ " + t("theme." + theme);
    document.getElementById("updown").textContent = t("updown." + updown);
    document.documentElement.setAttribute("data-updown", updown);
    document.getElementById("drawer-close").textContent = t("close");
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
  }

  function checkName(name) {
    const key = "checks." + name;
    return t(key) === key ? name : t(key);
  }

  function checkFix(c) {
    const key = "fixes." + c.check;
    return lang === "zh" && t(key) !== key ? t(key) : c.fix;
  }

  function withCode(text) {
    return String(text).split("`").map((part, i) => (i % 2 ? h("code", { text: part }) : part));
  }

  function nextRun(jobs) {
    const times = jobs.map((j) => j.timer?.next_run).filter(Boolean).sort((a, b) => a - b);
    return times[0] || null;
  }

  function renderPulse() {
    const el = document.getElementById("pulse");
    const d = state.data;
    let cls = "", text = "";
    if (state.offline) {
      cls = "trouble"; text = t("pulse.offline");
    } else if (!d) {
      text = "…";
    } else {
      const running = d.jobs.find((j) => j.service?.running);
      const trade = d.executions.find((r) => r.in_progress && r.mode === "LIVE");
      const next = nextRun(d.jobs);
      if (running) { cls = "busy"; text = t("pulse.busy", { name: protoName(running.protocol) }); }
      else if (trade) {
        cls = "busy";
        text = t("pulse.trade", { sym: trade.orders[0]?.symbol || "", when: trade.exit_due ? fmtTime(trade.exit_due) : "" });
      } else if (next) { cls = "idle"; text = t("pulse.idle", { when: fmtDateTime(next), rel: rel(next) }); }
      else if (d.jobs.length && d.jobs.every((j) => j.timer && !j.timer.enabled)) { text = t("pulse.disabled"); }
      else if (d.jobs.length) { cls = "idle"; text = t("pulse.idleNoTime"); }
      else { text = t("pulse.none"); }
    }
    if (el.dataset.text === text && el.dataset.cls === cls) return;
    el.dataset.text = text;
    el.dataset.cls = cls;
    el.replaceChildren(h("span", { class: "dot " + cls, "aria-hidden": "true" }), h("span", { class: "text", text }));
  }

  function renderBanner() {
    const el = document.getElementById("banner");
    const d = state.data;
    const parts = [];
    if (d?.errors?.length) {
      parts.push(h("div", {}, h("strong", { text: t("banner.errors") }), ...d.errors.map((e) => h("code", { text: e.path + ": " + e.message }))));
    }
    el.hidden = !parts.length;
    el.replaceChildren(...parts);
  }

  function renderFoot() {
    const d = state.data;
    document.getElementById("foot").replaceChildren(
      state.fetchedAt ? h("span", { text: t("foot.updated", { rel: rel(state.fetchedAt / 1000) }) }) : null,
      d ? h("span", { class: "muted", text: "   TradeAgent " + d.version }) : null,
    );
  }

  // ------------------------------------------------------------------ ribbon (hero)

  function nyMinutes(ts) {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "numeric", hourCycle: "h23" }).formatToParts(new Date(ts * 1000));
    const get = (type) => Number(parts.find((p) => p.type === type)?.value || 0);
    return get("hour") * 60 + get("minute");
  }

  function roleFor(mark, index, list) {
    if (mark.role) return mark.role;
    const sameJob = list.filter((m) => m.unit === mark.unit).sort((a, b) => a.start - b.start);
    const pos = sameJob.indexOf(mark);
    if (mark.start >= CLOSE_MIN) return "report";
    return pos === 0 ? "decide" : "resolve";
  }

  function ribbon() {
    const d = state.data;
    const market = d.market;
    const open = !!market.session;
    const now = Date.now() / 1000;
    const nowMin = nyMinutes(now);
    const marks = d.schedule.marks.map((m, i, list) => ({ ...m, role: roleFor(m, i, list) }));
    const todayRuns = {};
    for (const r of d.research) {
      const run = r.runs.find((x) => x.session_date === market.date);
      if (run) todayRuns[r.protocol] = run;
    }
    const span = CLOSE_MIN - OPEN_MIN;
    const x = (min) => ((Math.min(Math.max(min, OPEN_MIN), CLOSE_MIN) - OPEN_MIN) / span) * 100;

    const head = h("div", { class: "today-head" },
      h("h1", { text: open ? t("today.open") : t("today.closed") }),
      h("span", { class: "clock", text: open ? fmtDay(market.date) : market.next ? t("today.nextOpen", { day: fmtDay(market.next.date) }) : "" }),
    );

    const track = h("div", { class: "ribbon" + (open ? "" : " closed"), role: "img", "aria-label": open ? t("today.open") : t("today.closed") });
    if (open && nowMin > OPEN_MIN) track.append(h("div", { class: "past", style: `width:${x(nowMin)}%` }));
    for (let hh = 10; hh <= 15; hh++) {
      const tick = lang === "zh" ? `${hh}:00` : `${hh > 12 ? hh - 12 : hh} ${hh >= 12 ? "PM" : "AM"}`;
      track.append(h("div", { class: "hour", style: `left:${x(hh * 60)}%` }, h("span", { text: tick })));
    }
    const within = marks.filter((m) => m.start < CLOSE_MIN);
    const after = marks.filter((m) => m.start >= CLOSE_MIN);
    let nextStep = null;
    within.sort((a, b) => a.start - b.start).forEach((m) => {
      const run = todayRuns[m.protocol];
      let st = "";
      if (open) {
        if (m.role === "decide" && run) st = run.status === "COMPLETED" ? "done" : run.status === "INCOMPLETE" ? "failed" : "waiting";
        else if (m.role === "resolve" && run?.outcome) st = "done";
        else if (nowMin < m.start) { st = "waiting"; if (!nextStep) nextStep = m; }
        else if (nowMin <= m.end) st = "waiting";
        else if (m.role !== "decide") st = run?.pending ? "waiting" : "done";
      }
      const label = h("span", { class: "tag" }, h("b", { text: t("today.roles." + m.role) }), h("span", { class: "tm", text: " " + minutesLabel(m.start) }));
      if (m.end > m.start) track.append(h("span", { class: "band", style: `left:${x(m.start)}%;width:${x(m.end) - x(m.start)}%` }));
      track.append(h("div", { class: "mark " + st, style: `left:${x(m.start)}%`, title: protoName(m.protocol) }, h("span", { class: "stem" }), label));
    });
    // a live run in progress today: entry and exit pins
    for (const r of d.executions) {
      if (!r.in_progress || r.mode !== "LIVE" || !r.started) continue;
      for (const [ts, key] of [[r.started, "inTrade"], [r.exit_due, "exitsAt"]]) {
        if (!ts) continue;
        track.append(h("div", { class: "mark trade", style: `left:${x(nyMinutes(ts))}%; top:52px` }, h("span", { class: "stem", style: "height:22px" }),
          h("span", { class: "tag", text: key === "inTrade" ? t("trading.inTrade") : t("trading.exitsAt", { when: fmtNY(ts) }) })));
      }
    }
    if (open && nowMin >= OPEN_MIN && nowMin <= CLOSE_MIN) {
      track.append(h("div", { class: "now" + (x(nowMin) > 82 ? " flip" : ""), style: `left:${x(nowMin)}%` }, h("span", { text: t("today.now", { time: fmtNY(now) + " ET" }) })));
    }
    const afterBox = h("div", { class: "after" }, h("b", { text: t("today.afterClose") }),
      ...after.map((m) => h("span", { text: t("today.roles." + m.role) + " " + minutesLabel(m.start) })));

    let note;
    if (d.schedule.example) note = t("today.example");
    else if (!open) note = t("today.noteClosed");
    else if (nextStep) note = t("today.noteWaiting", { what: t("today.roles." + nextStep.role), time: minutesLabel(nextStep.start) });
    else if (Object.keys(todayRuns).length) note = t("today.noteDone");
    else note = t("today.noteAllDone");

    return h("section", { class: "section today" }, head, h("div", { class: "ribbon-wrap" }, track, afterBox), h("p", { class: "today-note", text: note }));
  }

  // ------------------------------------------------------------------ decision card

  function armChips(picks, outcome, protocol) {
    const order = Object.keys(picks || outcome || {});
    return h("div", { class: "arms" }, ...order.map((arm) => {
      const pick = picks?.[arm];
      const res = outcome?.[arm];
      let val;
      if (outcome) val = res == null ? t("decisions.unavailable") : pick && !pick.symbol ? "" : pct(res.net);
      else if (pick?.status === "UNAVAILABLE") val = t("decisions.unavailable");
      else val = pick?.symbol || t("decisions.cash");
      const pickText = outcome && pick ? (pick.symbol || t("decisions.cash")) : null;
      return h("span", { class: "arm" },
        h("i", { class: "key", style: `background:${armColor(arm)}`, "aria-hidden": "true" }),
        h("span", { class: "who", text: armName(arm) }),
        pickText ? h("span", { text: pickText }) : null,
        h("span", { class: "val " + (outcome ? tone(res?.net) : ""), text: val }),
      );
    }));
  }

  function decisionCard(item) {
    const card = h("button", { class: "decision", type: "button", onclick: () => openRun(item.source_id, item.decision_id), disabled: item.decision_id ? null : true });
    card.append(h("div", { class: "decision-head" },
      h("span", { class: "when", text: fmtDay(item.session_date) }),
      h("span", { class: "what", text: protoName(item.protocol) }),
      h("span", { class: "badges" }, evidenceBadge(item.evidence_kind),
        item.status !== "COMPLETED" ? h("span", { class: "badge", text: statusName(item.status) }) : null,
        item.pending ? h("span", { class: "badge", text: t("decisions.pending") }) : null),
    ));
    if (item.top) {
      card.append(h("div", { class: "pick" },
        h("span", { class: "sym" }, item.top.symbol, h("span", { class: "stance " + (item.top.stance || ""), text: t("stance." + item.top.stance) })),
        h("span", { class: "thesis", text: item.top.thesis || "" })));
    }
    if (item.failure_kind) card.append(h("p", { class: "failure", text: t("failures." + item.failure_kind) }));
    if (item.picks) card.append(armChips(item.picks, item.outcome, item.protocol));
    return card;
  }

  // ------------------------------------------------------------------ side panels

  function jobsPanel() {
    const d = state.data;
    const panel = h("section", { class: "panel" }, h("h3", { text: t("jobs.title") }));
    if (!d.jobs.length) {
      panel.append(h("p", { class: "small muted", text: t("jobs.none") }));
      return panel;
    }
    for (const j of d.jobs) {
      let st, cls;
      if (j.service?.running) { st = t("jobs.running"); cls = "run"; }
      else if (!j.timer) { st = t("jobs.unknown"); cls = "warn"; }
      else if (!j.timer.enabled) { st = t("jobs.off"); cls = "warn"; }
      else { st = t("jobs.next", { when: j.timer.next_run ? fmtDateTime(j.timer.next_run) : "—" }); cls = "ok"; }
      panel.append(h("div", { class: "job" },
        h("div", { class: "job-name", text: protoName(j.protocol) }),
        h("div", { class: "status " + cls, text: st }),
        j.last_session ? h("div", { class: "small muted", text: t("jobs.last", { day: fmtDay(j.last_session.session_date), status: statusName(j.last_session.status) }) }) : null,
      ));
    }
    return panel;
  }

  function runSummary(run) {
    const buy = run.orders.find((o) => o.side === "buy");
    const sell = run.orders.find((o) => o.side === "sell");
    const bits = [];
    if (buy) bits.push(`${buy.symbol} ${buy.quantity ?? ""} @ ${buy.price ?? "—"}`);
    if (sell) bits.push(`→ ${sell.price ?? "—"}`);
    return bits.join(" ");
  }

  function tradingPanel() {
    const d = state.data;
    const panel = h("section", { class: "panel" }, h("h3", { text: t("trading.title") }));
    {
      const settings = d.live?.settings;
      if (!settings) panel.append(h("p", { class: "small muted", text: t("trading.notConnected") }));
      else {
        panel.append(h("dl", { class: "kv" },
          h("dt", { text: t("trading.symbols") }), h("dd", { text: (settings.symbols || []).join(", ") || "—" }),
          h("dt", { text: t("trading.perEntry") }), h("dd", { text: plainMoney(settings.dollar_amount) }),
          h("dt", { text: t("trading.cap") }), h("dd", { text: plainMoney(settings.max_notional) }),
          h("dt", { text: t("trading.hold") }), h("dd", { text: settings.hold_seconds ? duration(settings.hold_seconds) : "—" }),
          h("dt", { text: t("trading.kill") }), h("dd", { class: d.live.kill_switch ? "neg" : "", text: d.live.kill_switch ? t("trading.killOn") : t("trading.killOff") }),
        ));
      }
    }
    const runs = d.executions.filter((r) => r.mode === "LIVE");
    const current = runs.find((r) => r.in_progress);
    const last = current || runs[0];
    if (last) {
      panel.append(h("div", { class: "job" },
        h("div", { class: "job-name" }, (current ? t("trading.inTrade") : t("trading.lastRun")) + " ", ...modeBadge(last.mode, last.synthetic)),
        h("div", { class: "small", text: runSummary(last) || statusName(last.status) }),
        last.realized_pnl != null ? h("div", { class: "small " + tone(last.realized_pnl), text: money(last.realized_pnl) }) : null,
        current && last.exit_due ? h("div", { class: "small muted", text: t("trading.exitsAt", { when: fmtTime(last.exit_due) }) }) : null,
      ));
    } else if (d.live?.settings) {
      panel.append(h("p", { class: "small muted", text: t("trading.noRuns") }));
    }
    return panel;
  }

  function setupPanel() {
    const d = state.data;
    const steps = d.setup.steps;
    if (steps.every((s) => s.done !== false)) return null;
    const list = h("ol", { class: "steps" }, ...steps.map((s) =>
      h("li", { class: s.done ? "done" : "" }, h("div", { text: t("setup.steps." + s.id) }), !s.done && s.command ? h("code", { text: s.command }) : null)));
    // Sign-in and settings are already steps above; list only the other checks.
    const covered = new Set(["Robinhood sign-in", "Live settings"]);
    const failing = d.setup.checks.filter((c) => c.status !== "ok" && !covered.has(c.check));
    return h("section", { class: "panel" }, h("h3", { text: t("setup.title") }), list,
      failing.length ? h("div", { class: "job" }, h("div", { class: "job-name", text: t("setup.check") }),
        ...failing.map((c) => h("div", { class: "small" }, h("span", { class: "status " + c.status, text: checkName(c.check) }), c.fix ? h("div", { class: "muted" }, withCode(checkFix(c))) : null)))
        : h("p", { class: "small muted", text: t("setup.ready") }));
  }

  // ------------------------------------------------------------------ home (account)

  const RANGES = { "1w": 7, "1m": 31, "3m": 92, all: Infinity };
  const toTs = (iso) => (iso ? Date.parse(iso) / 1000 : null);
  const nyDate = (ts) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ts * 1000));

  function liveRuns() {
    return (state.data.executions || []).filter((r) => r.mode === "LIVE" && !r.synthetic);
  }

  function tradeOf(run) {
    const buy = run.orders.find((o) => o.side === "buy" && o.quantity);
    const sell = run.orders.find((o) => o.side === "sell" && o.quantity);
    const cost = buy ? (buy.notional ?? (buy.price != null ? buy.price * buy.quantity : null)) : null;
    return {
      run,
      symbol: buy?.symbol || run.orders[0]?.symbol || null,
      buy,
      sell,
      cost,
      pnl: run.realized_pnl,
      ret: run.realized_pnl != null && cost ? run.realized_pnl / cost : null,
      opened: toTs(buy?.filled_at) || run.started,
      closed: toTs(sell?.filled_at) || run.finished,
      mirror: String(run.strategy || "").startsWith("mirror:"),
      model: String(run.strategy || "").includes(":claude_") ? "claude" : "codex",
    };
  }

  function account() {
    // Account value as the broker reported it: at the start of each live run and after
    // each reconciled fill. Other holdings and deposits move it too, as in any brokerage.
    const points = [];
    for (const e of state.data.ledger || []) {
      if (e.nav != null) points.push({ t: e.asof || e.started, v: e.nav });
      if (e.end_nav != null) points.push({ t: e.end_asof, v: e.end_nav });
    }
    points.sort((x, y) => x.t - y.t);
    const last = points[points.length - 1];
    return { runs: liveRuns(), points, value: last ? last.v : null, asof: last ? last.t : null };
  }

  function signedMoney(v) {
    if (v == null || !isFinite(v)) return "—";
    const sign = v > 0 ? "+" : v < 0 ? "−" : "";
    const a = Math.abs(v);
    return sign + "$" + (a > 0 && a < 0.01 ? a.toFixed(3) : a.toFixed(2));
  }
  function usd(v) {
    return v == null || !isFinite(v) ? "—" : "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function price(v) {
    return v == null ? "—" : "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  const dir = (v) => (v == null || v === 0 ? "flat" : v > 0 ? "up" : "down");

  function heroChange(change, startValue, label) {
    const share = startValue ? change / startValue : null;
    return h("p", { class: "hero-change " + dir(change) },
      h("span", { class: "num", text: `${signedMoney(change)} (${pct(share)})` }),
      h("span", { class: "range-label", text: label }));
  }

  function home() {
    const acct = account();
    const range = state.range || "all";
    const days = RANGES[range];
    const now = Date.now() / 1000;
    const inRange = acct.points.filter((p) => days === Infinity || p.t >= now - days * 86400);
    const before = acct.points.filter((p) => !(days === Infinity || p.t >= now - days * 86400));
    const start = before.length ? before[before.length - 1].v : acct.points[0]?.v;
    const series = before.length ? [{ t: now - days * 86400, v: start }, ...inRange] : inRange;
    const end = series.length ? series[series.length - 1].v : null;
    const change = end != null && start != null ? end - start : null;

    const valueEl = h("h1", { class: "hero-value num", text: acct.value != null ? usd(acct.value) : "" });
    const changeSlot = h("div", {}, acct.value != null ? heroChange(change, start, t("home.ranges." + range + "Label")) : null);
    const restore = () => {
      valueEl.textContent = acct.value != null ? usd(acct.value) : "";
      changeSlot.replaceChildren(acct.value != null ? heroChange(change, start, t("home.ranges." + range + "Label")) : null);
    };
    const scrub = (p) => {
      if (!p) return restore();
      valueEl.textContent = usd(p.v);
      changeSlot.replaceChildren(heroChange(p.v - start, start, fmtDateTime(p.t)));
    };

    const left = h("div", { class: "home-main" },
      h("p", { class: "hero-label", text: t("home.account") }),
      valueEl,
      changeSlot,
      acct.value == null ? h("p", { class: "hero-empty", text: t("home.noTrades") }) : null,
      acct.points.length ? h("div", { class: "hero-chart " + dir(change), "data-hero": "1" }) : null,
      acct.points.length ? h("div", { class: "ranges", role: "tablist" }, ...Object.keys(RANGES).map((k) =>
        h("button", { type: "button", role: "tab", class: "range", "aria-selected": String(k === range), text: t("home.ranges." + k),
          onclick: () => { state.range = k; render(); } }))) : null,
      acct.asof ? h("p", { class: "hero-asof", text: t("home.asof", { when: fmtDateTime(acct.asof) }) }) : null);
    heroScrub = { series, scrub, dir: dir(change) };
    // Grid areas keep today's status beside the chart on desktop and right under it on a phone.
    return h("div", { class: "home" }, left, sidePanel(acct), historyList(acct, 8));
  }

  let heroScrub = null;
  function drawHero() {
    const box = document.querySelector("[data-hero]");
    if (!box || !heroScrub) return;
    const { series, scrub } = heroScrub;
    const W = Math.max(300, box.clientWidth || 640), H = 220, padY = 14;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("home.chartLabel"), preserveAspectRatio: "none" });
    if (series.length < 2) {
      svg.append(s("line", { class: "hero-base", x1: 0, x2: W, y1: H / 2, y2: H / 2 }));
      box.replaceChildren(svg);
      return;
    }
    const t0 = series[0].t, t1 = series[series.length - 1].t;
    const vals = series.map((p) => p.v);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < 1e-9) { lo -= 0.01; hi += 0.01; }
    const x = (tt) => ((tt - t0) / Math.max(1, t1 - t0)) * (W - 8) + 4;
    const y = (v) => padY + (1 - (v - lo) / (hi - lo)) * (H - 2 * padY);
    // Step line: the value changes only when a trade closes.
    let d = `M${x(series[0].t).toFixed(1)} ${y(series[0].v).toFixed(1)}`;
    for (let i = 1; i < series.length; i++) d += ` H${x(series[i].t).toFixed(1)} V${y(series[i].v).toFixed(1)}`;
    svg.append(s("line", { class: "hero-base", x1: 0, x2: W, y1: y(series[0].v), y2: y(series[0].v) }));
    svg.append(s("path", { class: "hero-line", d }));
    const dot = s("circle", { class: "hero-dot", r: 5, cx: 0, cy: 0, visibility: "hidden" });
    const rule = s("line", { class: "hero-rule", x1: 0, x2: 0, y1: 0, y2: H, visibility: "hidden" });
    svg.append(rule, dot);
    const hit = s("rect", { x: 0, y: 0, width: W, height: H, fill: "transparent" });
    const move = (evt) => {
      const rect = svg.getBoundingClientRect();
      const px = ((evt.clientX - rect.left) / rect.width) * W;
      let best = 0;
      for (let i = 0; i < series.length; i++) if (x(series[i].t) <= px) best = i;
      const p = series[best];
      dot.setAttribute("cx", x(p.t)); dot.setAttribute("cy", y(p.v));
      rule.setAttribute("x1", x(p.t)); rule.setAttribute("x2", x(p.t));
      dot.setAttribute("visibility", "visible"); rule.setAttribute("visibility", "visible");
      scrub(p);
    };
    const leave = () => { dot.setAttribute("visibility", "hidden"); rule.setAttribute("visibility", "hidden"); scrub(null); };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerleave", leave);
    svg.append(hit);
    box.replaceChildren(svg);
  }

  function tradeRow(x) {
    const run = x.run;
    const when = x.closed || x.opened || run.started;
    let detail;
    if (x.buy && x.sell) detail = t("home.row.closed", { buy: price(x.buy.price), sell: price(x.sell.price) });
    else if (x.buy) detail = t("home.row.open", { buy: price(x.buy.price), cost: usd(x.cost) });
    else detail = t("home.row.none", { status: statusName(run.status) });
    const row = h("button", { type: "button", class: "trade-row" },
      h("span", { class: "trade-sym" }, h("b", { text: x.symbol || "—" }), h("span", { class: "muted", text: fmtDay(nyDate(when)) })),
      h("span", { class: "trade-detail muted", text: detail }),
      h("span", { class: "trade-pnl num " + dir(x.pnl) },
        x.pnl != null ? h("b", { text: signedMoney(x.pnl) }) : h("b", { class: "muted", text: run.in_progress ? t("home.row.holding") : "—" }),
        x.ret != null ? h("span", { text: pct(x.ret) }) : null));
    row.addEventListener("click", () => openTrade(run));
    return row;
  }

  function historyList(acct, limit) {
    const items = acct.runs.map(tradeOf).filter((x) => x.buy || x.run.in_progress);
    const head = h("div", { class: "section-head" }, h("h2", { text: t("home.history") }),
      items.length > limit ? h("a", { class: "push small", href: "#/trades", text: t("home.allTrades") }) : null);
    if (!items.length) return h("section", { class: "history" }, head, h("p", { class: "muted", text: t("home.historyEmpty") }));
    return h("section", { class: "history" }, head, h("div", { class: "trade-list" }, ...items.slice(0, limit).map(tradeRow)));
  }

  function reasonText(reason, model = followedModel()) {
    const r = String(reason || "");
    const who = modelName(model);
    if (/chose cash/.test(r)) return t("home.reasons.cash", { model: who });
    if (/assessment unavailable/.test(r)) return t("home.reasons.unavailable", { model: who });
    if (/no daily-news-\S+ decision/.test(r)) return t("home.reasons.noDecision");
    if (/not in the owner/.test(r)) return t("home.reasons.notAllowed", { sym: r.split(" ")[0], model: who });
    if (/already attempted/.test(r)) return t("home.reasons.already");
    if (/entry window passed/.test(r)) return t("home.reasons.late");
    return r;
  }

  function todayStatus() {
    const d = state.data;
    const day = d.market?.date;
    const runs = liveRuns();
    const open = runs.find((r) => r.in_progress);
    const record = (d.autotrade?.records || []).find((r) => r.session === day);
    const model = followedModel();
    const who = modelName(model);
    const news = d.research.find((r) => r.protocol === FOLLOW[model].protocol);
    const run = news?.runs?.find((r) => r.session_date === day);
    const pick = run?.decision?.arms?.[FOLLOW[model].arm];
    if (open) {
      const x = tradeOf(open);
      return { tone: "busy", title: t("home.today.holding", { sym: x.symbol || "" }),
        lines: [x.buy ? t("home.today.bought", { cost: usd(x.cost), price: price(x.buy.price), when: fmtTime(x.opened) }) : t("home.today.buying"),
          open.exit_due ? t("home.today.sellsAt", { when: fmtTime(open.exit_due) }) : null] };
    }
    if (record) {
      if (record.status === "CLOSED") {
        const x = runs.map(tradeOf).find((y) => y.symbol === record.symbol && y.closed && nyDate(y.closed) === day);
        return { tone: dir(x?.pnl), title: t("home.today.done", { sym: record.symbol }),
          lines: [x ? t("home.today.result", { pnl: signedMoney(x.pnl), ret: pct(x.ret) }) : null] };
      }
      const recordModel = record.model in FOLLOW ? record.model : model;
      if (record.status === "NO_TRADE") return { tone: "idle", title: t("home.today.noTrade"), lines: [reasonText(record.reason, recordModel)] };
      return { tone: "trouble", title: t("home.today.problem"), lines: [reasonText(record.reason, recordModel) || statusName(record.status)] };
    }
    if (pick?.symbol && d.market?.session && Date.now() / 1000 < d.market.session.close)
      return { tone: "busy", title: t("home.today.picked", { sym: pick.symbol, model: who }), lines: [t("home.today.soon")] };
    const nowTs = Date.now() / 1000;
    if (d.market?.session && nowTs < d.market.session.open + 4800)  // pick is due by about 10:50 ET
      return { tone: "idle", title: t("home.today.waiting"), lines: [t("home.today.waitingLine", { model: who })] };
    if (d.market?.session && nowTs < d.market.session.close)
      return { tone: "trouble", title: t("home.today.noPick"), lines: [t("home.reasons.noDecision")] };
    return { tone: "idle", title: d.market?.session ? t("home.today.closedNow") : t("home.today.holiday"),
      lines: [d.market?.next ? t("home.today.next", { day: fmtDay(d.market.next.date) }) : null] };
  }

  function sidePanel(acct) {
    const d = state.data;
    const at = d.autotrade;
    const parts = [];
    const st = todayStatus();
    parts.push(h("section", { class: "side-block" },
      h("p", { class: "side-label", text: t("home.today.label", { day: fmtDay(d.market?.date) }) }),
      h("p", { class: "side-title " + st.tone }, h("span", { class: "dot " + st.tone, "aria-hidden": "true" }), st.title),
      ...st.lines.filter(Boolean).map((line) => h("p", { class: "side-line", text: line }))));
    const settings = d.live?.settings;
    const rows = [];
    if (at) {
      rows.push([t("home.auto.rule"), t("home.auto.ruleValue", { model: modelName(followedModel()) })]);
      rows.push([t("home.auto.next"), at.timer?.enabled === false ? t("home.auto.off") : at.timer?.next_run ? fmtDateTime(at.timer.next_run) : "—"]);
    }
    if (settings) {
      rows.push([t("home.auto.amount"), plainMoney(settings.dollar_amount)]);
      rows.push([t("home.auto.hold"), settings.hold_seconds ? duration(settings.hold_seconds) : "—"]);
    }
    if (d.live?.kill_switch) rows.push([t("trading.kill"), t("trading.killOn")]);
    parts.push(h("section", { class: "side-block" },
      h("p", { class: "side-label", text: t("home.auto.title") }),
      at ? h("dl", { class: "side-kv" }, ...rows.flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })]))
        : h("div", {}, h("p", { class: "side-line", text: t("home.auto.notInstalled") }), h("code", { text: "tradeagent autotrade install --enable" }))));
    const model = followedModel();
    const news = d.research.find((r) => r.protocol === FOLLOW[model].protocol);
    const arm = news?.arms?.find((a) => a.arm === FOLLOW[model].arm);
    if (arm) {
      parts.push(h("a", { class: "side-block side-link", href: "#/research" },
        h("p", { class: "side-label", text: t("home.ai.title", { model: modelName(model) }) }),
        h("p", { class: "side-big" }, h("span", { class: "num " + dir(arm.mean_net), text: pct(arm.mean_net) }), h("span", { class: "muted small", text: " " + t("home.ai.perDay") })),
        h("p", { class: "side-line", text: t("home.ai.days", { n: news.valid_paired_days || 0, need: MIN_DAYS }) })));
    }
    const duel = duelSource();
    if (duel) {
      const m = duel.duel.modes.news;
      const scored = m.scored_days > 0;
      parts.push(h("a", { class: "side-block side-link", href: "#/compare" },
        h("p", { class: "side-label", text: t("home.duel.title") }),
        scored ? h("div", { class: "side-duel" },
          duelFigure("codex", m.codex_arm, m.codex_total),
          duelFigure("claude", m.claude_arm, m.claude_total)) : null,
        h("p", { class: "side-line", text: scored ? t("home.duel.line", { n: m.scored_days, same: m.same_pick }) : t("home.duel.early") })));
    }
    const setup = setupPanel();
    return h("aside", { class: "home-side" }, h("div", { class: "side-card" }, ...parts), setup);
  }

  // ------------------------------------------------------------------ overview

  function emptyState() {
    return h("section", { class: "welcome" }, h("div", { class: "stack" },
      h("h2", { text: t("empty.title") }), h("p", { text: t("empty.body") })));
  }

  function verdictChip(v, base) {
    if (!v) return h("span", { class: "muted small", text: t("research.baseline") });
    const label = t("research.verdict." + v.state, { d: v.days, n: v.needed || MIN_DAYS });
    const chip = h("span", { class: "verdict " + v.state, text: label });
    if (v.low != null) chip.title = t("research.ci", { lo: pct(v.low), hi: pct(v.high) });
    return chip;
  }

  function snapshot() {
    const d = state.data;
    if (!d.research.some((r) => r.scored)) return null;
    return h("section", { class: "section" },
      h("div", { class: "section-head" }, h("h2", { text: t("snapshot.title") }), h("a", { class: "push small", href: "#/research", text: t("snapshot.more") })),
      ...d.research.filter((r) => r.scored).map((r) => h("div", { class: "panel" },
        h("div", { class: "decision-head" }, h("span", { class: "when", text: protoName(r.protocol) }), h("span", { class: "badges" }, evidenceBadge(r.evidence_kind))),
        progress(r),
        h("div", { class: "arms" }, ...r.arms.map((a) => h("span", { class: "arm" },
          h("i", { class: "key", style: `background:${armColor(a.arm)}` }), h("span", { class: "who", text: armName(a.arm) }),
          a.vs_baseline_verdict ? verdictChip(a.vs_baseline_verdict) : h("span", { class: "muted small", text: t("research.baseline") })))))));
  }

  function overview() {
    const d = state.data;
    const nothing = !d.research.length && !d.executions.length && !d.labs.length && !d.jobs.length;
    const recent = sessions().slice(0, 5);
    const left = h("section", { class: "section" },
      h("div", { class: "section-head" }, h("h2", { text: t("decisions.title") }), h("a", { class: "push small", href: "#/activity", text: t("decisions.all") })),
      recent.length ? recent.map(decisionCard) : h("p", { class: "empty", text: t("decisions.empty") }));
    if (nothing) {
      // First run: the welcome and the setup steps take the main column.
      return [ribbon(), h("div", { class: "cols" },
        h("div", { class: "section" }, emptyState(), setupPanel()),
        h("div", { class: "section" }, jobsPanel(), tradingPanel()))];
    }
    const right = h("div", { class: "section" }, jobsPanel(), tradingPanel(), setupPanel());
    return [ribbon(), h("div", { class: "cols" }, left, right), snapshot()];
  }

  // ------------------------------------------------------------------ research

  function progress(r) {
    const n = r.valid_paired_days || 0;
    const share = Math.min(1, n / MIN_DAYS);
    return h("div", { class: "progress" },
      h("span", { class: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(MIN_DAYS), "aria-valuenow": String(Math.min(n, MIN_DAYS)) }, h("i", { style: `width:${share * 100}%` })),
      h("span", { text: n >= MIN_DAYS ? t("research.progressDone", { n }) + t("stop") + t("research.enough") : t("research.progress", { n, need: MIN_DAYS }) + t("stop") + t("research.firstRead", { need: MIN_DAYS }) }));
  }

  function armsTable(r) {
    const base = r.baseline ? t("research.vsBase", { base: armName(r.baseline) }) : t("research.vsPaired");
    return h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {},
        h("th", { text: t("research.arm") }), h("th", { class: "n", text: t("research.took") }), h("th", { class: "n", text: t("research.avgNet") }),
        h("th", { class: "n", text: t("research.vsSpy") }), h("th", { text: base }))),
      h("tbody", {}, ...r.arms.map((a) => {
        const ex = a.excess || {};
        return h("tr", {},
          h("td", {}, h("span", { class: "arm" }, h("i", { class: "key", style: `background:${armColor(a.arm)}` }), armName(a.arm))),
          h("td", { class: "n", text: `${a.selected_days}/${r.frozen_sessions}` }),
          h("td", { class: "n " + tone(a.mean_net), text: pct(a.mean_net) }),
          h("td", { class: "n" }, h("span", { class: tone(ex.mean), text: pct(ex.mean) }),
            ex.standard_error != null && ex.days >= 2 ? h("span", { class: "muted small", text: ` ±${(1.96 * ex.standard_error * 100).toFixed(2)}` }) : null),
          h("td", {}, a.vs_baseline_verdict ? verdictChip(a.vs_baseline_verdict) : h("span", { class: "muted small", text: t("research.baseline") }),
            !r.baseline && a.vs_baseline_verdict ? h("span", { class: "muted small", text: " " + t("research.vsArm", { arm: armName(a.baseline) }) }) : null));
      }))));
  }

  function chartPlaceholder(r) {
    const n = r.series.length;
    if (n < 2) return h("p", { class: "empty gate", text: t("research.chartGate", { need: MIN_DAYS, n }) });
    const box = h("div", { class: "chart", "data-chart": r.id },
      h("h3", { text: t("research.chartTitle") }),
      h("div", { class: "legend" }, ...r.arms.map((a) => h("span", {}, h("i", { style: `background:${armColor(a.arm)}` }), armName(a.arm)))),
      h("div", { class: "plot" }),
      h("p", { class: "small muted", text: t("research.chartNote") + (n < MIN_DAYS ? " " + t("research.chartEarly", { n, need: MIN_DAYS }) : "") }));
    return box;
  }

  function niceTicks(min, max, count = 4) {
    const span = max - min || 1;
    const step0 = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count + 1) || step0;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-12; v += step) ticks.push(+v.toFixed(10));
    return ticks;
  }

  function drawCharts() {
    for (const box of document.querySelectorAll("[data-chart]")) {
      const r = chartSources[box.dataset.chart] || state.data.research.find((x) => x.id === box.dataset.chart);
      if (!r) continue;
      const label = (a) => r.names?.[a] || armName(a);
      const plot = box.querySelector(".plot");
      const W = Math.max(320, plot.clientWidth || 640), H = 240;
      const pad = { l: 52, r: 120, t: 12, b: 28 };
      const arms = r.arms.map((a) => a.arm);
      const cum = {};
      for (const a of arms) {
        let sum = 0;
        cum[a] = r.series.map((p) => (sum += (p.net ? p.net[a] : p.excess[a]) || 0));
      }
      const all = Object.values(cum).flat().concat([0]);
      let lo = Math.min(...all), hi = Math.max(...all);
      const padY = (hi - lo) * 0.1 || 0.001; lo -= padY; hi += padY;
      const n = r.series.length;
      const x = (i) => pad.l + (i / Math.max(1, n - 1)) * (W - pad.l - pad.r);
      const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
      const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("research.chartTitle") });
      for (const v of niceTicks(lo, hi)) {
        svg.append(s("line", { class: v === 0 ? "zero" : "grid", x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v) }));
        svg.append(s("text", { class: "axis", x: pad.l - 8, y: y(v) + 4, "text-anchor": "end" }, pct(v, 1)));
      }
      const labelAt = W < 560 ? [0, n - 1] : [0, Math.floor((n - 1) / 2), n - 1];
      for (const i of new Set(labelAt)) {
        svg.append(s("text", { class: "axis", x: x(i), y: H - 8, "text-anchor": i === 0 ? "start" : i === n - 1 ? "end" : "middle" }, fmtDay(r.series[i].session_date)));
      }
      const ends = [];
      for (const a of arms) {
        const d = cum[a].map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
        svg.append(s("path", { class: "series", d, stroke: armColor(a) }));
        ends.push({ a, y: y(cum[a][n - 1]), v: cum[a][n - 1] });
      }
      if (arms.length <= 4) {
        ends.sort((p, q) => p.y - q.y);
        for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
        for (const e of ends) {
          svg.append(s("text", { class: "end-label", x: W - pad.r + 8, y: e.y + 4 }, `${label(e.a)} ${pct(e.v, 1)}`));
        }
      }
      const cross = s("line", { class: "cross", y1: pad.t, y2: H - pad.b, x1: -10, x2: -10 });
      svg.append(cross);
      const tip = h("div", { class: "tip", hidden: true });
      const hit = s("rect", { x: pad.l, y: pad.t, width: W - pad.l - pad.r, height: H - pad.t - pad.b, fill: "transparent" });
      const move = (evt) => {
        const rect = svg.getBoundingClientRect();
        const px = ((evt.clientX - rect.left) / rect.width) * W;
        const i = Math.max(0, Math.min(n - 1, Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (n - 1))));
        cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
        const rows = arms.map((a) => ({ a, v: cum[a][i] })).sort((p, q) => q.v - p.v);
        tip.replaceChildren(h("div", { class: "small", text: fmtDay(r.series[i].session_date) }),
          ...rows.map((row) => h("div", { class: "row" }, h("span", {}, h("i", { style: `background:${armColor(row.a)}` }), label(row.a)), h("span", { class: "num", text: pct(row.v) }))));
        tip.hidden = false;
        const left = (x(i) / W) * rect.width;
        tip.style.left = Math.min(left + 14, rect.width - 180) + "px";
        tip.style.top = "40px";
      };
      hit.addEventListener("pointermove", move);
      hit.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("x1", -10); cross.setAttribute("x2", -10); });
      svg.append(hit);
      plot.replaceChildren(svg, tip);
    }
  }

  function recentTable(r) {
    const rows = r.runs.slice(-12).reverse();
    const arms = r.arms.map((a) => a.arm);
    // The pick column needs Codex's ranking; without it, failures still need a place.
    const ranked = rows.some((run) => run.decision?.candidates.some((c) => c.analysis?.price));
    const failures = rows.some((run) => run.failure_kind);
    const pickCol = ranked || failures;
    return h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: t("research.date") }), pickCol ? h("th", { text: ranked ? t("research.pick", { model: analystOf(r.protocol) }) : "" }) : null, ...arms.map((a) => h("th", { class: "n", text: armName(a) })))),
      h("tbody", {}, ...rows.map((run) => {
        const top = run.decision?.candidates.find((c) => c.analysis?.price?.rank === 1);
        const tr = h("tr", { class: run.decision ? "click" : "", tabindex: run.decision ? "0" : null },
          h("td", { text: fmtDay(run.session_date) }),
          pickCol ? h("td", {}, top ? [top.symbol, " ", h("span", { class: "stance " + top.analysis.price.stance, text: t("stance." + top.analysis.price.stance) })] : h("span", { class: "muted", text: run.failure_kind ? t("failures." + run.failure_kind) : ranked ? "—" : "" })) : null,
          ...arms.map((a) => {
            const res = run.outcome?.arms?.[a];
            const pick = run.decision?.arms?.[a];
            const cash = pick && !pick.symbol && pick.status !== "UNAVAILABLE";
            const text = cash ? t("decisions.cash") : run.outcome ? (res ? pct(res.net) : "—") : pick?.symbol || "—";
            return h("td", { class: "n " + (run.outcome && !cash ? tone(res?.net) : "muted"), text });
          }));
        if (run.decision) {
          const open = () => openRun(r.id, run.decision.decision_id);
          tr.addEventListener("click", open);
          tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
        }
        return tr;
      }))));
  }

  function labSection(lab) {
    const maxW = Math.max(...lab.weights.map((w) => w.weight), 0.0001);
    return h("section", { class: "protocol" },
      h("div", { class: "section-head" }, h("h2", { text: t("research.lab") }), lab.synthetic ? evidenceBadge("synthetic") : null),
      h("p", { class: "lede", text: t("research.labLede") }),
      h("dl", { class: "kv" },
        h("dt", { text: t("research.forecasts") }), h("dd", { class: "num", text: lab.predictions }),
        h("dt", { text: t("research.scored") }), h("dd", { class: "num", text: lab.outcomes }),
        h("dt", { text: t("research.learning") }), h("dd", { class: "num", text: lab.learning_runs }),
        h("dt", { text: t("research.challengers") }), h("dd", { class: "num", text: lab.challengers })),
      lab.weights.length ? h("div", { class: "section" }, h("h3", { class: "small muted", text: t("research.weights") }),
        h("ul", { class: "weights" }, ...lab.weights.slice(0, 8).map((w) => h("li", {},
          h("span", { class: "name", title: `${w.strategy} ${w.version}`, text: `${w.strategy.replace(/_/g, " ")}${w.version.startsWith("challenger") ? " ★" : ""}` }),
          h("span", {}, h("span", { class: "w", style: `display:block;width:${(w.weight / maxW) * 100}%` })),
          h("span", { class: "num small", text: w.weight.toFixed(2) })))),
        h("p", { class: "small muted", text: t("research.star") })) : null);
  }

  function research() {
    const d = state.data;
    const parts = [h("div", { class: "section-head" }, h("h2", { text: t("research.title") })), h("p", { class: "lede", text: t("research.lede") })];
    if (!d.research.length && !d.labs.length) {
      parts.push(h("div", { class: "empty stack" }, h("p", { text: t("research.none") + " " + t("research.noneHint") }), h("code", { text: d.setup.steps.find((s) => s.id === "autopilot")?.command || "" })));
    }
    const seen = {};
    for (const r of d.research) seen[r.protocol] = (seen[r.protocol] || 0) + 1;
    for (const r of d.research) {
      const q = t("questions." + r.protocol);
      parts.push(h("section", { class: "protocol" },
        h("div", { class: "section-head" }, h("h2", { text: protoName(r.protocol) }), evidenceBadge(r.evidence_kind),
          // Only when two directories run the same protocol does the path tell them apart.
          seen[r.protocol] > 1 && r.path ? h("span", { class: "aside mono", text: r.path }) : null),
        q.startsWith("questions.") ? null : h("p", { class: "lede", text: q }),
        r.scored ? [progress(r), armsTable(r), chartPlaceholder(r)]
          : h("p", { class: "empty" }, t("research.ownReport"), r.report ? [" ", h("code", { text: r.report })] : null),
        h("div", { class: "section" }, h("div", { class: "section-head" }, h("h3", { text: t("research.recent") })), recentTable(r))));
    }
    for (const lab of d.labs) parts.push(labSection(lab));
    return h("section", { class: "section" }, ...parts);
  }

  // ------------------------------------------------------------------ GPT vs Claude

  const chartSources = {};

  function duelSource() {
    return (state.data?.research || []).find((r) => r.protocol === CLAUDE_P && r.duel);
  }

  function duelFigure(who, arm, total) {
    return h("div", { class: "duel-fig" },
      h("span", { class: "duel-who" }, h("i", { class: "key", style: `background:${armColor(arm)}`, "aria-hidden": "true" }), t("duel." + who)),
      h("span", { class: "num " + dir(total), text: total == null ? "—" : pct(total) }));
  }

  function duelVerdict(m) {
    const v = m.verdict || {};
    const text = v.state === "collecting" ? t("duel.verdict.collecting", { d: v.days, n: v.needed || MIN_DAYS }) : t("duel.verdict." + v.state);
    return h("p", { class: "duel-verdict " + (v.state || "") }, text);
  }

  function duelSide(who, m, models) {
    const arm = who === "codex" ? m.codex_arm : m.claude_arm;
    const total = who === "codex" ? m.codex_total : m.claude_total;
    const lead = m.codex_total != null && m.claude_total != null && Math.abs(m.claude_total - m.codex_total) > 1e-12
      && (who === "claude") === (m.claude_total > m.codex_total);
    return h("div", { class: "duel-side " + who + (lead ? " lead" : "") },
      h("p", { class: "duel-name" }, h("i", { class: "key", style: `background:${armColor(arm)}`, "aria-hidden": "true" }), t("duel." + who)),
      h("p", { class: "duel-via", text: t("duel.via." + who, { model: models[who] || "—" }) }),
      h("p", { class: "duel-total num " + dir(total), text: total == null ? "—" : pct(total) }),
      h("p", { class: "duel-sub", text: m.scored_days ? t("duel.total", { n: m.scored_days }) : "\u00a0" }),
      h("p", { class: "duel-wins", text: t("duel.wins", { n: m.wins[who] || 0 }) }));
  }

  function duelPick(pick, res, outcome) {
    if (!pick) return h("span", { class: "muted", text: "—" });
    if (pick.status === "UNAVAILABLE") return h("span", { class: "muted", text: t("decisions.unavailable") });
    const what = pick.symbol || t("decisions.cash");
    return h("span", { class: "duel-pick" },
      h("b", { text: what }),
      outcome ? h("span", { class: "num " + (pick.symbol ? tone(res?.net) : "muted"), text: res ? pct(res.net) : "—" })
        : h("span", { class: "muted small", text: t("decisions.pending") }));
  }

  function duelRows(r, m) {
    const runs = r.runs.slice().reverse();
    if (!runs.length) return h("p", { class: "empty", text: t("decisions.empty") });
    return h("div", { class: "table-wrap" }, h("table", { class: "duel-table" },
      h("thead", {}, h("tr", {}, h("th", { text: t("research.date") }), h("th", { text: t("duel.codex") }), h("th", { text: t("duel.claude") }), h("th", { class: "n", text: "" }))),
      h("tbody", {}, ...runs.map((run) => {
        const arms = run.decision?.arms || {};
        const out = run.outcome?.arms || {};
        const a = out[m.codex_arm], b = out[m.claude_arm];
        let mark = "";
        if (a && b) {
          const gap = b.net - a.net;
          mark = Math.abs(gap) < 1e-12 ? t("duel.even") : `${t(gap > 0 ? "duel.claude" : "duel.codex")} ${t("duel.better")}`;
        }
        const winner = a && b && Math.abs(b.net - a.net) >= 1e-12 ? (b.net > a.net ? "claude" : "codex") : null;
        const tr = h("tr", { class: run.decision ? "click" : "", tabindex: run.decision ? "0" : null },
          h("td", { class: "day", text: fmtDay(run.session_date) }),
          run.decision ? h("td", { class: winner === "codex" ? "win codex" : "" }, duelPick(arms[m.codex_arm], a, run.outcome)) : h("td", { colspan: "3", class: "muted", text: run.failure_kind ? t("failures." + run.failure_kind) : statusName(run.status) }),
          run.decision ? h("td", { class: winner === "claude" ? "win claude" : "" }, duelPick(arms[m.claude_arm], b, run.outcome)) : null,
          run.decision ? h("td", { class: "n small " + (mark && b && a ? (Math.abs(b.net - a.net) < 1e-12 ? "muted" : b.net > a.net ? "duel-claude" : "duel-codex") : "muted"), text: mark }) : null);
        if (run.decision) {
          const open = () => openRun(r.id, run.decision.decision_id);
          tr.addEventListener("click", open);
          tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
        }
        return tr;
      }))));
  }

  function duelSetup() {
    return h("div", { class: "panel duel-setup" },
      h("h3", { text: t("duel.setupTitle") }),
      h("p", { class: "muted small", text: t("duel.none") }),
      h("ol", { class: "steps" }, ...t("duel.setup").map((step) => h("li", {}, withCode(step)))));
  }

  function compare() {
    const r = duelSource();
    const mode = state.duel === "price" ? "price" : "news";
    const head = h("div", { class: "section-head" }, h("h2", { text: t("duel.title") }), r ? evidenceBadge(r.evidence_kind) : null,
      r ? h("div", { class: "push seg", role: "tablist" }, ...["news", "price"].map((k) =>
        h("button", { type: "button", role: "tab", class: "range", "aria-selected": String(k === mode), text: t("duel.modes." + k),
          onclick: () => { state.duel = k; store.set("ta.duel", k); render(); } }))) : null);
    const parts = [head, h("p", { class: "lede", text: t("duel.lede") })];
    if (!r) {
      parts.push(duelSetup());
      return h("section", { class: "section" }, ...parts);
    }
    const m = r.duel.modes[mode];
    const models = r.duel.models || {};
    parts.push(h("div", { class: "duel-board" },
      duelSide("codex", m, models),
      h("div", { class: "duel-mid" }, h("span", { class: "duel-vs", text: "vs" }), h("span", { class: "small muted", text: t("duel.ties", { n: m.wins.tie || 0 }) })),
      duelSide("claude", m, models)));
    const diff = m.difference || {};
    parts.push(h("div", { class: "duel-facts" },
      duelVerdict(m),
      diff.days >= 2 && diff.standard_error != null ? h("p", { class: "small muted", text: t("duel.ci", { lo: pct(diff.mean - 1.96 * diff.standard_error), hi: pct(diff.mean + 1.96 * diff.standard_error) }) }) : null,
      m.answered_both ? h("p", { class: "small muted", text: t("duel.same", { same: m.same_pick, n: m.answered_both }) }) : null,
      progress({ valid_paired_days: m.scored_days })));
    const followed = state.data.autotrade ? followedModel() : null;
    if (followed) parts.push(h("p", { class: "small muted", text: t("duel.trading", { model: modelName(followed) }) }));
    const id = "duel-" + mode;
    chartSources[id] = { id, arms: [{ arm: m.codex_arm }, { arm: m.claude_arm }], series: m.series, names: { [m.codex_arm]: t("duel.codex"), [m.claude_arm]: t("duel.claude") } };
    if (m.series.length >= 2) {
      parts.push(h("div", { class: "chart", "data-chart": id },
        h("h3", { text: t("duel.chart") }),
        h("div", { class: "legend" }, ...[m.codex_arm, m.claude_arm].map((a) => h("span", {}, h("i", { style: `background:${armColor(a)}` }), chartSources[id].names[a]))),
        h("div", { class: "plot" }),
        h("p", { class: "small muted", text: t("duel.chartNote", { need: MIN_DAYS }) })));
    } else {
      parts.push(h("p", { class: "empty gate", text: t("research.chartGate", { n: m.series.length }) }));
    }
    parts.push(h("div", { class: "section" }, h("div", { class: "section-head" }, h("h3", { text: t("duel.sessions") })), duelRows(r, m)));
    return h("section", { class: "section" }, ...parts);
  }

  function duelNotes(a) {
    const pair = (mode, gpt, claude) => (gpt || claude ? h("div", { class: "note" },
      h("h4", { text: t("duel.modes." + mode) }),
      h("div", { class: "split" },
        analysisBlock(t("duel.codex"), gpt) || h("p", { class: "small muted", text: `${t("duel.codex")}: ${t("drawer.noNote")}` }),
        analysisBlock(t("duel.claude"), claude) || h("p", { class: "small muted", text: `${t("duel.claude")}: ${t("drawer.noNote")}` }))) : null);
    const blocks = [pair("news", a.codex_news, a.news), pair("price", a.codex_price, a.price)].filter(Boolean);
    return blocks.length ? h("div", {}, ...blocks) : h("p", { class: "small muted note", text: t("drawer.noNote") });
  }

  // ------------------------------------------------------------------ activity

  function activityText(item) {
    if (item.kind === "session") {
      const lines = [h("div", {}, h("strong", { text: t("activity.session", { proto: protoName(item.protocol), status: statusName(item.status) }) }), " ", evidenceBadge(item.evidence_kind))];
      if (item.failure_kind) lines.push(h("div", { class: "failure", text: t("failures." + item.failure_kind) }));
      if (item.top) lines.push(h("div", { text: t("activity.picked", { sym: item.top.symbol, stance: t("stance." + item.top.stance), model: analystOf(item.protocol) }) + " " }, h("span", { class: "muted", text: item.top.thesis || "" })));
      if (item.picks) lines.push(armChips(item.picks, item.outcome, item.protocol));
      if (item.decision_id) lines.push(h("button", { class: "link small", type: "button", onclick: () => openRun(item.source_id, item.decision_id), text: t("activity.details") }));
      return lines;
    }
    if (item.kind === "trade") {
      return [h("div", {}, h("strong", { text: t("activity.trade", { mode: item.mode, sym: item.symbol || "", status: statusName(item.status) }) }), " ",
        ...modeBadge(item.mode, item.synthetic)),
        item.realized_pnl != null ? h("div", { class: tone(item.realized_pnl), text: t("activity.pnl", { pnl: money(item.realized_pnl) }) }) : null];
    }
    return [h("div", { text: t("activity.lab", { n: item.predictions, runs: item.learning_runs }) + " " }, item.synthetic ? evidenceBadge("synthetic") : null)];
  }

  function isProblem(i) {
    return (i.kind === "session" && i.status !== "COMPLETED") || (i.kind === "trade" && ["HALTED", "UNKNOWN"].includes(i.status));
  }

  function activity() {
    const d = state.data;
    const filters = h("div", { class: "filters", role: "group" }, ...["all", "session", "trade", "problem"].map((f) =>
      h("button", { class: "chip", type: "button", "aria-pressed": state.filter === f ? "true" : "false", text: t("activity.filters." + f), onclick: () => { state.filter = f; render(); } })));
    const items = d.activity.filter((i) => state.filter === "all" ? true : state.filter === "problem" ? isProblem(i) : i.kind === state.filter);
    const groups = new Map();
    for (const i of items) {
      const key = i.time ? new Date(i.time * 1000).toDateString() : "—";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(i);
    }
    const days = [...groups.entries()].map(([key, list]) => h("section", { class: "day" },
      h("h3", { text: list[0].time ? new Intl.DateTimeFormat(locale(), { weekday: "long", month: "long", day: "numeric" }).format(new Date(list[0].time * 1000)) : "—" }),
      ...list.map((i) => h("div", { class: "event" },
        h("span", { class: "t", text: fmtTime(i.time) }),
        h("span", { class: "glyph " + (isProblem(i) ? "problem" : i.kind), "aria-hidden": "true" }),
        h("div", {}, ...activityText(i))))));
    return h("section", { class: "section" }, h("div", { class: "section-head" }, h("h2", { text: t("activity.title") })), filters,
      days.length ? days : h("p", { class: "empty", text: t("activity.empty") }));
  }

  // ------------------------------------------------------------------ trades

  function trades() {
    const d = state.data;
    const head = [h("div", { class: "section-head" }, h("h2", { text: t("trades.title") })), h("p", { class: "lede", text: t("trades.lede") })];
    if (!d.executions.length) {
      return h("section", { class: "section" }, ...head, h("div", { class: "empty stack" }, h("p", { text: t("trades.empty") }), h("code", { text: "tradeagent autotrade install --enable" })));
    }
    const live = d.executions.filter((r) => r.mode === "LIVE" && !r.synthetic);
    const other = d.executions.filter((r) => !(r.mode === "LIVE" && !r.synthetic));
    const totals = d.trade_totals || { pnl: 0, closed: 0, wins: 0 };
    const stats = h("dl", { class: "stats" },
      h("div", {}, h("dt", { text: t("trades.total") }), h("dd", { class: "num " + dir(totals.pnl), text: signedMoney(totals.pnl) })),
      h("div", {}, h("dt", { text: t("trades.count") }), h("dd", { class: "num", text: String(totals.closed) })),
      h("div", {}, h("dt", { text: t("trades.winRate") }), h("dd", { class: "num", text: totals.closed ? Math.round((totals.wins / totals.closed) * 100) + "%" : "—" })));
    const list = (runs) => h("div", { class: "trade-list" }, ...runs.map((r) => tradeRow(tradeOf(r))));
    return h("section", { class: "section trades-page" }, ...head,
      live.length ? [stats, list(live)] : h("p", { class: "muted", text: t("home.historyEmpty") }),
      other.length ? [h("div", { class: "section-head sub" }, h("h2", { text: t("trades.paper") })), list(other)] : null);
  }

  // ------------------------------------------------------------------ drawer

  let lastFocus = null;
  function showDrawer(content) {
    lastFocus = document.activeElement;
    const drawer = document.getElementById("drawer");
    document.getElementById("drawer-body").replaceChildren(...[content].flat());
    drawer.hidden = false;
    document.getElementById("drawer-close").focus();
  }
  function hideDrawer() {
    document.getElementById("drawer").hidden = true;
    if (lastFocus) lastFocus.focus();
  }

  function note(label, value) {
    if (!value || (Array.isArray(value) && !value.length)) return null;
    return h("div", { class: "note" }, h("h4", { text: label }), Array.isArray(value) ? h("ul", {}, ...value.map((v) => h("li", { text: v }))) : h("p", { text: value }));
  }

  function analysisBlock(label, a) {
    if (!a) return null;
    return h("div", {},
      h("div", { class: "small muted" }, label, " ", h("span", { class: "stance " + (a.stance || ""), text: t("stance." + a.stance) }), a.rank ? ` #${a.rank}` : ""),
      note(t("drawer.thesis"), a.thesis), note(t("drawer.catalyst"), a.catalyst), note(t("drawer.pricedIn"), a.priced_in),
      note(t("drawer.invalidation"), a.invalidation), note(t("drawer.risks"), a.contradictions),
      a.sources?.length ? h("div", { class: "note" }, h("h4", { text: t("drawer.sources") }),
        h("ul", {}, ...a.sources.map((src) => h("li", {}, h("a", { href: src.url, target: "_blank", rel: "noopener noreferrer", text: src.title || src.url }))))) : null);
  }

  async function openRun(source, decision) {
    if (!source || !decision) return;
    showDrawer(h("p", { class: "muted", text: t("drawer.loading") }));
    let run;
    try {
      const res = await fetch(`/api/run?source=${encodeURIComponent(source)}&decision=${encodeURIComponent(decision)}`);
      if (!res.ok) throw new Error();
      run = await res.json();
    } catch (e) {
      document.getElementById("drawer-body").replaceChildren(h("p", { text: t("drawer.missing") }));
      return;
    }
    const dec = run.decision;
    const body = [
      h("h2", { id: "drawer-title", text: `${fmtDay(run.session_date)}, ${protoName(run.protocol)}` }),
      h("div", { class: "decision-head" }, evidenceBadge(run.evidence_kind), h("span", { class: "badge", text: statusName(run.status) })),
      dec?.freeze_time ? h("p", { class: "frozen", text: t("drawer.frozen", {
        time: fmtDay(run.session_date) + " " + fmtNY(dec.freeze_time) + " ET",
        delay: duration((dec.entry_after || dec.freeze_time) - dec.freeze_time),
        hold: duration((dec.exit_time || dec.freeze_time) - dec.freeze_time),
      }) }) : null,
      run.failure_kind ? h("p", { class: "failure", text: t("failures." + run.failure_kind) }) : null,
    ];
    if (dec) {
      body.push(h("div", { class: "section-head" }, h("h3", { text: t("drawer.outcome") })));
      body.push(h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, ...Object.entries(dec.arms).map(([arm, pick]) => {
        const res = run.outcome?.arms?.[arm];
        return h("tr", {},
          h("td", {}, h("span", { class: "arm" }, h("i", { class: "key", style: `background:${armColor(arm)}` }), armName(arm))),
          h("td", { text: pick.status === "UNAVAILABLE" ? t("decisions.unavailable") : pick.symbol || t("decisions.cash") }),
          h("td", { class: "n " + tone(res?.net), text: run.outcome ? (res ? t("drawer.net", { v: pct(res.net) }) : "—") : t("decisions.pending") }),
          h("td", { class: "n muted", text: res ? t("drawer.excess", { v: pct(res.excess) }) : "" }));
      })))));
      body.push(h("div", { class: "section" }, h("div", { class: "section-head" }, h("h3", { text: t("drawer.candidates") }))));
      for (const c of dec.candidates) {
        const out = run.outcome?.symbols?.[c.symbol];
        const a = c.analysis || {};
        body.push(h("div", { class: "cand" },
          h("div", { class: "cand-head" }, h("span", { class: "sym", text: c.symbol }), out ? h("span", { class: tone(out.net), text: pct(out.net) }) : null),
          h("div", { class: "facts" },
            h("span", { text: `${t("drawer.windowReturn")} ${pct(c.window_return)}` }),
            c.relative_strength != null ? h("span", { text: `${t("drawer.rel")} ${pct(c.relative_strength)}` }) : null,
            c.volume_ratio != null ? h("span", { text: `${t("drawer.volume")} ×${Number(c.volume_ratio).toFixed(1)}` }) : null,
            ...c.categories.map((cat) => h("span", { class: "muted", text: cat.replace(/_/g, " ") }))),
          run.protocol === CLAUDE_P ? duelNotes(a)
            : a.price || a.news ? h("div", { class: a.price && a.news ? "split" : "" }, analysisBlock(t("drawer.price", { model: analystOf(run.protocol) }), a.price), analysisBlock(t("drawer.news", { model: analystOf(run.protocol) }), a.news))
            : h("p", { class: "small muted note", text: t("drawer.noAnalysis", { model: analystOf(run.protocol) }) })));
      }
    }
    document.getElementById("drawer-body").replaceChildren(...body.filter(Boolean));
  }

  function openTrade(run) {
    const x = tradeOf(run);
    const facts = [
      [t("trades.detail.bought"), x.buy ? `${price(x.buy.price)}, ${fmtDateTime(x.opened)}` : "—"],
      [t("trades.detail.sold"), x.sell ? `${price(x.sell.price)}, ${fmtDateTime(x.closed)}` : run.in_progress && run.exit_due ? t("home.today.sellsAt", { when: fmtTime(run.exit_due) }) : "—"],
      [t("trades.detail.amount"), usd(x.cost)],
      [t("trades.detail.shares"), x.buy?.quantity != null ? String(x.buy.quantity) : "—"],
      [t("trades.detail.rule"), x.mirror ? t("home.auto.ruleValue", { model: modelName(x.model) }) : t("trades.detail.manual")],
      [t("trades.detail.status"), statusName(run.status)],
      [t("trades.detail.reconciled"), run.reconciled == null ? "—" : run.reconciled ? t("trades.yes") : t("trades.no")],
    ];
    const body = [
      h("h2", { id: "drawer-title", class: "detail-sym", text: x.symbol || "—" }),
      h("div", { class: "decision-head" }, ...modeBadge(run.mode, run.synthetic)),
      x.pnl != null ? h("p", { class: "detail-pnl num " + dir(x.pnl), text: `${signedMoney(x.pnl)} (${pct(x.ret)})` })
        : h("p", { class: "detail-pnl muted", text: run.in_progress ? t("home.row.holding") : statusName(run.status) }),
      h("dl", { class: "side-kv detail-kv" }, ...facts.flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })])),
      run.timeline.length ? h("div", { class: "section" }, h("div", { class: "section-head" }, h("h3", { text: t("trades.timeline") })),
        h("ol", { class: "steps" }, ...run.timeline.map((e) => h("li", { class: "done" }, h("div", { text: t("trades.events." + e.kind) }), h("div", { class: "small muted", text: fmtTime(e.time, { second: "2-digit" }) }))))) : null,
      run.reason ? h("p", { class: "small muted note", text: run.reason }) : null,
    ];
    showDrawer(body.filter(Boolean));
  }

  // ------------------------------------------------------------------ render

  function render() {
    renderChrome();
    renderPulse();
    renderBanner();
    renderFoot();
    const main = document.getElementById("main");
    if (!state.data) {
      main.replaceChildren(h("p", { class: "muted", text: state.offline ? t("pulse.offline") : "…" }));
      return;
    }
    const views = { home, overview, compare, activity, research, trades };
    heroScrub = null;
    main.replaceChildren(...[views[state.route]()].flat().filter(Boolean));
    drawHero();
    drawCharts();
  }

  function route() {
    const name = (location.hash.replace(/^#\/?/, "") || "home").split("?")[0];
    state.route = ROUTES.includes(name) ? name : "home";
    render();
    if (document.activeElement && document.activeElement.closest(".tabs")) document.getElementById("main").focus({ preventScroll: true });
  }

  document.getElementById("lang").addEventListener("click", () => {
    lang = lang === "zh" ? "en" : "zh";
    store.set("ta.lang", lang);
    if (!store.get("ta.updown", "")) updown = lang === "zh" ? "cn" : "us";
    render();
  });
  document.getElementById("theme").addEventListener("click", () => {
    theme = { auto: "light", light: "dark", dark: "auto" }[theme];
    store.set("ta.theme", theme);
    render();
  });
  document.getElementById("updown").addEventListener("click", () => {
    updown = updown === "cn" ? "us" : "cn";
    store.set("ta.updown", updown);
    render();
  });
  document.getElementById("drawer-close").addEventListener("click", hideDrawer);
  document.getElementById("drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") hideDrawer(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !document.getElementById("drawer").hidden) hideDrawer(); });
  window.addEventListener("hashchange", route);
  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { drawHero(); drawCharts(); }, 150); });

  route();
  load();
  setInterval(load, REFRESH_MS);
  setInterval(() => { renderPulse(); renderFoot(); }, 1000);
})();
