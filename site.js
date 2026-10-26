/* TradeAgent public page: track record, pipeline and comparison. Renders the inlined snapshot. No dependencies. */
(() => {
  "use strict";

  const data = JSON.parse(document.getElementById("data").textContent);
  const MIN_DAYS = 20;
  const TZ = "America/New_York";
  const method = data.method || {};
  const UNIVERSE = (method.universe && method.universe.length) || 31;
  const SHORTLIST = method.shortlist || 3;
  const HOLD = data.holding_minutes || 60;
  const CHECKS = 34;

  const COPY = {
    en: {
      lang: "中文",
      nav: ["Performance", "Pipeline", "Compare", "Trade log"],
      asOf: "As of {t} ET",
      eyebrow: "Open-source AI trading agent · US equities",
      title: "A quant desk with an AI analyst. Not a chatbot's hunch.",
      lede: "Every trading day at 10:30 ET, TradeAgent scans {u} liquid US stocks and ETFs on live one-minute data, ranks them with quantitative signals, has GPT or Claude research the news on the top {k}, and executes the winner in your own Robinhood account under {c} hard risk checks. Every decision is locked before the market moves and scored after costs.",
      ctaInstall: "Install",
      ctaRecord: "Track record",
      ctaGh: "GitHub",
      kpiTitle: "Track record",
      kpiSub: "Paper portfolio · net of costs · {h}-minute holds",
      kpiMain: "Cumulative net return",
      kpiAgent: "TradeAgent · GPT with news",
      vsSpy: "Excess vs S&P 500",
      vsSpySub: "SPY, same hour",
      vsRandom: "Excess vs random pick",
      vsRandomSub: "same shortlist",
      beatSpy: "Days ahead of SPY",
      hit: "Hit rate",
      hitSub: "positive / traded days",
      days: "Trading days",
      inception: "since {d}",
      empty: "The track record starts after the first trading day.",

      perfTitle: "Performance",
      perfAside: "Cumulative net return. Every arm: same shortlist, same minute, same hour.",
      sample: "n = {n} trading days. Differences over fewer than {need} days are mostly noise; the record updates after every close.",
      statCols: ["Strategy", "Cumulative", "Avg / day", "Hit rate", "Best", "Worst", "Days"],
      arms: {
        codex: ["TradeAgent · GPT", "quant scan + GPT with news"],
        claude: ["TradeAgent · Claude", "quant scan + Claude with news"],
        quant: ["Quant engine only", "no AI"],
        random: ["Random pick", "same shortlist, seeded"],
        spy: ["S&P 500 (SPY)", "same hour"],
      },
      short: { codex: "GPT", claude: "Claude", quant: "Quant", random: "Random", spy: "SPY" },

      pipeTitle: "From {u} tickers to one trade.",
      pipeLede: "Six stages on a fixed clock. The AI is one stage of the system, not the whole system.",
      pipe: [
        ["10:30 ET", "Universe", "{u} liquid US large caps & ETFs", "Live one-minute bars and bid/ask quotes. A ticker with stale, missing or out-of-session data is disqualified."],
        ["10:30 ET", "Quant scan", "7 signals → top {k}", "Over the last 60 minutes: volatility-scaled abnormal move, relative strength vs SPY, unusual volume, volatility regime shift, opening-gap continuation or reversal, breakout. Ranked by a composite score."],
        ["10:30 ET", "Alpha & cost models", "3 signal families", "Opening continuation, stabilized reversal and beta-adjusted residual strength forecast each finalist. Every trade plan is priced after spread, slippage and fees."],
        ["10:31 ET", "AI analyst", "GPT or Claude, reading the news", "Receives the quant features, searches the web and returns a schema-validated verdict per stock: long, watch or avoid, with thesis, catalyst and dated sources. News-driven theses need a source under 24 hours old. Cash is a valid answer."],
        ["≈10:36 ET", "Lock & execute", "{c} hard risk checks", "The decision is timestamped and frozen. The engine, not the AI, places the order: $1–$5, regular session only, no leverage or shorts, spread and liquidity gates, exposure, concentration, daily-loss and drawdown halts."],
        ["+{h} min", "Exit, score, publish", "Against 3 controls", "Automatic exit after {h} minutes. Each result is scored after costs against SPY over the same hour, a random pick from the same shortlist and the quant engine alone, then published here at 17:20 ET."],
      ],

      vsTitle: "Same AI. Inside a quant desk.",
      vsLede: "Ask ChatGPT or Claude for a stock and you get an opinion. TradeAgent uses the same models as one stage of a disciplined pipeline, then executes and keeps score.",
      vsCols: ["", "TradeAgent", "Asking an LLM directly"],
      vsRows: [
        ["Where ideas come from", "Quant scan of {u} tickers on live one-minute data", "Whatever the model recalls, usually the most-discussed names"],
        ["Market data", "Live bars and bid/ask quotes, rejected if stale", "Training data or a news search; no live quote"],
        ["Quantitative signals", "7 scan signals + 3 alpha families, measured against SPY", "None: a narrative"],
        ["News", "Live web search; news-driven calls need a source < 24 h old", "May cite stale or unverifiable stories"],
        ["Output", "Schema-validated stance, thesis, catalyst and sources", "Free text that changes every time you ask"],
        ["Trading costs", "Spread, slippage and fees in every result", "Ignored"],
        ["Timing", "Locked by 10:37 ET; fixed {h}-minute hold and exit", "No entry, no exit, no clock"],
        ["Execution", "Places and closes the order in your account", "You trade by hand"],
        ["Risk control", "{c} hard checks; $1–$5 per trade; loss and drawdown halts", "None"],
        ["Track record", "Every call locked first, scored vs SPY, random and quant-only, published", "None; the misses are forgotten"],
        ["Model choice", "GPT or Claude, measured head-to-head on the same shortlist", "One model, never measured"],
      ],
      vsNote: "Unlike AI trading arenas, it trades your account rather than a showcase portfolio. Unlike signal bots, every call is locked before the market moves and published with its result, losses included.",

      memoTitle: "Latest decision",
      memoAside: "{d} · 10:30 ET session",
      pick: "Pick",
      result: "Result, {h} min",
      pending: "Scored after the hour",
      cash: "Cash",
      cashLine: "No candidate cleared the gate. Top idea:",
      thesis: "Thesis",
      catalyst: "Catalyst",
      sources: "Sources",
      noAnswer: "No answer this session.",
      stance: { long: "Long", watch: "Watch", avoid: "Avoid" },

      modelTitle: "Your choice of AI",
      modelLede: "Run the agent on GPT (through Codex) or Claude (through Claude Code) with the subscription you already have. No API keys. Both answer the same question on the same shortlist every day, so you can see which one earns its place.",
      modelDefault: "Default",
      modelOptional: "Optional",
      modelGpt: "ChatGPT plan · Codex CLI",
      modelClaude: "Claude plan · Claude Code",
      modelPending: "Head-to-head statistics appear after the first session both models have scored.",
      duelCols: ["", "GPT", "Claude"],
      duelRows: { total: "Cumulative net return", wins: "Days ahead", same: "Same pick" },
      verdict: {
        collecting: "{d} of {n} days scored. No verdict before {n}.",
        ahead: "Claude leads by more than day-to-day noise.",
        behind: "GPT leads by more than day-to-day noise.",
        unclear: "Within day-to-day noise: no significant difference yet.",
      },

      logTitle: "Trade log",
      logAside: "Every decision, newest first",
      date: "Date",
      showAll: "Show all {n} sessions",

      runTitle: "Run your own agent.",
      runLede: "Free and open source. Start with paper trading and a local dashboard; switch to live trading when you choose.",
      runSteps: ["Install", "Check your setup", "Open the dashboard"],
      runReq: "Linux or WSL2 · Python 3.12+ · a ChatGPT or Claude plan · Robinhood for live trading",
      runBtn: "Documentation on GitHub",

      foot: [
        "Hypothetical performance. Results come from paper trading: each decision is locked before entry and scored at market prices with modeled costs ({cost} bp per side: half-spread, slippage and fees), not live fills. Cumulative figures sum daily {h}-minute returns and are not compounded. Short records are dominated by noise. Past performance does not predict future results. Not investment advice.",
      ],
      updated: "Data as of {t} ET.",
      source: "Source code",
    },
    zh: {
      lang: "EN",
      nav: ["业绩", "流程", "对比", "交易记录"],
      asOf: "数据截至美东 {t}",
      eyebrow: "开源 AI 交易代理 · 美股",
      title: "量化系统打底，AI 分析师把关。不靠聊天机器人拍脑袋。",
      lede: "每个交易日美东 10:30，TradeAgent 用实时分钟行情扫描 {u} 只高流动性美股和 ETF，按量化信号排序，再交给 GPT 或 Claude 研究前 {k} 名的新闻。最终选择过完 {c} 项硬性风控，在自己的 Robinhood 账户里执行。每个决策都在行情变化前锁定，扣除成本后计分。",
      ctaInstall: "安装",
      ctaRecord: "历史战绩",
      ctaGh: "GitHub",
      kpiTitle: "历史战绩",
      kpiSub: "模拟组合 · 扣除成本 · 每笔持有 {h} 分钟",
      kpiMain: "累计净收益",
      kpiAgent: "TradeAgent · GPT 读新闻",
      vsSpy: "相对标普 500 超额",
      vsSpySub: "SPY，同一小时",
      vsRandom: "相对随机选股超额",
      vsRandomSub: "同一候选名单",
      beatSpy: "跑赢 SPY 天数",
      hit: "胜率",
      hitSub: "盈利天数 / 交易天数",
      days: "交易日",
      inception: "自 {d} 起",
      empty: "第一个交易日结束后开始记录战绩。",

      perfTitle: "业绩表现",
      perfAside: "累计净收益。所有策略：同一候选名单、同一分钟、同一小时。",
      sample: "样本 n = {n} 个交易日。少于 {need} 天的差异主要是噪声；每日收盘后更新。",
      statCols: ["策略", "累计", "日均", "胜率", "最好", "最差", "天数"],
      arms: {
        codex: ["TradeAgent · GPT", "量化扫描 + GPT 读新闻"],
        claude: ["TradeAgent · Claude", "量化扫描 + Claude 读新闻"],
        quant: ["纯量化引擎", "不用 AI"],
        random: ["随机选股", "同一候选名单，固定种子"],
        spy: ["标普 500（SPY）", "同一小时"],
      },
      short: { codex: "GPT", claude: "Claude", quant: "量化", random: "随机", spy: "SPY" },

      pipeTitle: "从 {u} 个标的到一笔交易。",
      pipeLede: "六个环节，固定时间表运行。AI 只是系统中的一环，而不是全部。",
      pipe: [
        ["美东 10:30", "股票池", "{u} 只高流动性大盘股与 ETF", "实时一分钟 K 线和买卖报价。数据过期、缺失或不在交易时段的标的直接剔除。"],
        ["美东 10:30", "量化扫描", "7 类信号 → 前 {k} 名", "基于最近 60 分钟：波动率标准化的异常涨跌、相对 SPY 强弱、异常成交量、波动率状态切换、开盘跳空延续或反转、突破。按综合得分排序。"],
        ["美东 10:30", "Alpha 与成本模型", "3 个信号族", "开盘延续、企稳反转、beta 调整后的残差强度，分别对每个候选给出预测。每个交易计划都计入价差、滑点和手续费。"],
        ["美东 10:31", "AI 分析师", "GPT 或 Claude，阅读新闻", "接收量化特征，联网搜索，对每只股票给出经过结构校验的结论：看多、观望或回避，附逻辑、催化剂和带日期的来源。新闻驱动的判断必须有 24 小时内的来源。空仓也是合法答案。"],
        ["约美东 10:36", "锁定并执行", "{c} 项硬性风控", "决策加时间戳后冻结。下单的是执行引擎而不是 AI：每笔 1–5 美元，仅限常规时段，不加杠杆、不做空，价差与流动性门槛，敞口、集中度、单日亏损和回撤熔断。"],
        ["+{h} 分钟", "平仓、计分、公开", "对比 3 个对照组", "{h} 分钟后自动平仓。扣除成本后，与同一小时的 SPY、同一候选名单中的随机选择、以及纯量化引擎对比计分，美东 17:20 在此公开。"],
      ],

      vsTitle: "同一个 AI，放进一整套量化流程。",
      vsLede: "直接问 ChatGPT 或 Claude 买什么，得到的只是一个观点。TradeAgent 把同样的模型当作严格流程中的一环：先筛选、再研究、再风控，成交之后持续计分。",
      vsCols: ["", "TradeAgent", "直接问大模型"],
      vsRows: [
        ["选股来源", "用实时一分钟行情量化扫描 {u} 个标的", "模型记得什么就说什么，通常是讨论最多的热门股"],
        ["行情数据", "实时 K 线与买卖报价，过期即拒绝", "训练数据或新闻搜索，没有实时报价"],
        ["量化信号", "7 类扫描信号 + 3 个 Alpha 信号族，以 SPY 为基准", "没有，只有叙事"],
        ["新闻", "实时联网搜索；新闻驱动的判断须有 24 小时内的来源", "可能引用过时或无法核实的消息"],
        ["输出", "结构校验的立场、逻辑、催化剂和来源", "自由文本，每次问都不一样"],
        ["交易成本", "每个结果都扣除价差、滑点和手续费", "忽略"],
        ["时机", "美东 10:37 前锁定；固定持有 {h} 分钟并平仓", "没有入场、没有离场、没有时间表"],
        ["执行", "在自己的账户里自动下单和平仓", "只能手动下单"],
        ["风控", "{c} 项硬性检查；每笔 1–5 美元；亏损与回撤熔断", "没有"],
        ["历史战绩", "每次决策先锁定，与 SPY、随机、纯量化对比计分并公开", "没有记录，错的很快就忘了"],
        ["模型选择", "GPT 或 Claude，在同一候选名单上正面对比", "单一模型，从未被衡量"],
      ],
      vsNote: "和 AI 交易擂台不同，它交易的是真实账户，不是展示用的组合；和信号机器人不同，每次决策都在行情变化前锁定，连同结果一起公开，亏损也照样公开。",

      memoTitle: "最新决策",
      memoAside: "{d} · 美东 10:30 场次",
      pick: "选择",
      result: "{h} 分钟结果",
      pending: "一小时后计分",
      cash: "空仓",
      cashLine: "没有候选通过门槛。排名第一：",
      thesis: "逻辑",
      catalyst: "催化剂",
      sources: "来源",
      noAnswer: "本场次没有给出答案。",
      stance: { long: "看多", watch: "观望", avoid: "回避" },

      modelTitle: "模型自己选",
      modelLede: "直接用现有订阅运行：GPT（通过 Codex）或 Claude（通过 Claude Code），不需要 API key。两者每天在同一候选名单上回答同一个问题，谁更靠谱，看数据。",
      modelDefault: "默认",
      modelOptional: "可选",
      modelGpt: "ChatGPT 订阅 · Codex CLI",
      modelClaude: "Claude 订阅 · Claude Code",
      modelPending: "两个模型都有计分场次后，这里显示正面对比统计。",
      duelCols: ["", "GPT", "Claude"],
      duelRows: { total: "累计净收益", wins: "领先天数", same: "选择相同" },
      verdict: {
        collecting: "已计分 {d}/{n} 天，满 {n} 天前不下结论。",
        ahead: "Claude 领先，差距超过日常波动。",
        behind: "GPT 领先，差距超过日常波动。",
        unclear: "仍在日常波动范围内，暂无显著差异。",
      },

      logTitle: "交易记录",
      logAside: "每一次决策，最新在前",
      date: "日期",
      showAll: "显示全部 {n} 个场次",

      runTitle: "部署一个自己的交易代理。",
      runLede: "免费开源。先用模拟盘和本地仪表盘跑起来，准备好了再切实盘。",
      runSteps: ["安装", "检查环境", "打开仪表盘"],
      runReq: "Linux 或 WSL2 · Python 3.12+ · ChatGPT 或 Claude 订阅 · 实盘需 Robinhood",
      runBtn: "GitHub 文档",

      foot: [
        "模拟业绩。结果来自模拟盘：每个决策在入场前锁定，按市场价格计分并扣除模型化成本（每边 {cost} 个基点：半价差、滑点和手续费），不是实盘成交。累计数字为每日 {h} 分钟收益之和，不复利。短期记录主要受噪声影响。过往表现不代表未来结果。不构成投资建议。",
      ],
      updated: "数据截至美东时间 {t}。",
      source: "源代码",
    },
  };

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked */ } },
  };
  let lang = store.get("ta.site.lang") || ((navigator.language || "en").toLowerCase().startsWith("zh") ? "zh" : "en");
  let showAll = false;

  const VARS = { u: UNIVERSE, k: SHORTLIST, c: CHECKS, h: HOLD };
  function fill(v, vars) {
    if (Array.isArray(v)) return v.map((x) => fill(x, vars));
    if (typeof v !== "string") return v;
    const all = { ...VARS, ...(vars || {}) };
    return v.replace(/\{(\w+)\}/g, (_, k) => (all[k] != null ? String(all[k]) : ""));
  }
  function t(path, vars) {
    const pick = (table) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), table);
    let v = pick(COPY[lang]);
    if (v == null) v = pick(COPY.en);
    return fill(v, vars);
  }

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "text") el.textContent = v;
      else if (k === "style") el.style.cssText = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v);
    }
    for (const c of children.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : String(c));
    return el;
  }
  const NS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, text) {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    if (text != null) el.textContent = text;
    return el;
  }

  const locale = () => (lang === "zh" ? "zh-CN" : "en-US");
  const pct = (v, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(d)}%`);
  const tone = (v) => (v == null || Math.abs(v) < 1e-12 ? "" : v > 0 ? "up" : "down");
  const day = (iso, long) => new Date(iso + "T12:00:00Z").toLocaleDateString(locale(),
    long ? { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" } : { month: "short", day: "numeric", timeZone: "UTC" });
  const stamp = (seconds) => new Date(seconds * 1000).toLocaleString(locale(),
    { timeZone: TZ, month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
  const color = { codex: "var(--gpt)", claude: "var(--claude)", quant: "var(--quant)", spy: "var(--spy)", random: "var(--random)" };

  // ------------------------------------------------------------ statistics

  const scored = data.sessions.filter((x) => x.scored);
  const value = (x, key) => (key === "spy" ? x.spy : x[key] && x[key].answered ? x[key].net : null);
  const held = (x, key) => (key === "spy" ? x.spy != null : !!(x[key] && x[key].symbol));
  const has = (key) => scored.some((x) => value(x, key) != null);

  function stats(key) {
    const rows = scored.filter((x) => value(x, key) != null);
    if (!rows.length) return null;
    const vals = rows.map((x) => value(x, key));
    const traded = rows.filter((x) => held(x, key));
    const total = vals.reduce((a, b) => a + b, 0);
    return {
      days: rows.length,
      total,
      mean: total / vals.length,
      hits: traded.filter((x) => value(x, key) > 0).length,
      traded: traded.length,
      best: Math.max(...vals),
      worst: Math.min(...vals),
    };
  }
  function excess(key, base) {
    const rows = scored.filter((x) => value(x, key) != null && value(x, base) != null);
    if (!rows.length) return null;
    return {
      total: rows.reduce((a, x) => a + value(x, key) - value(x, base), 0),
      ahead: rows.filter((x) => value(x, key) - value(x, base) > 1e-12).length,
      days: rows.length,
    };
  }
  const rate = (st) => (st.traded ? `${Math.round((st.hits / st.traded) * 100)}%` : "—");

  // ------------------------------------------------------------ hero

  function hero() {
    return h("section", { class: "hero" },
      h("div", { class: "hero-copy" },
        h("p", { class: "eyebrow", text: t("eyebrow") }),
        h("h1", { text: t("title") }),
        h("p", { class: "lede", text: t("lede") }),
        h("div", { class: "cta" },
          h("a", { class: "btn primary", href: "#run", text: t("ctaInstall") }),
          h("a", { class: "btn", href: "#performance", text: t("ctaRecord") }),
          h("a", { class: "btn", href: data.repo, text: t("ctaGh") + " ↗" }))),
      kpis());
  }

  function kpis() {
    const a = stats("codex");
    const top = h("div", { class: "kpi-head" }, h("b", { text: t("kpiTitle") }), h("span", { text: t("kpiSub") }));
    if (!a) return h("aside", { class: "kpi" }, top, h("p", { class: "muted pad", text: t("empty") }));
    const spy = excess("codex", "spy"), rnd = excess("codex", "random");
    const row = (name, sub, val, cls) => h("div", { class: "kpi-row" },
      h("div", {}, h("div", { class: "k", text: name }), sub ? h("div", { class: "ks", text: sub }) : null),
      h("div", { class: "v num " + (cls || ""), text: val }));
    return h("aside", { class: "kpi", "aria-label": t("kpiTitle") }, top,
      h("div", { class: "kpi-main" },
        h("div", { class: "k", text: t("kpiMain") }),
        h("div", { class: "big num " + tone(a.total), text: pct(a.total) }),
        h("div", { class: "ks" }, h("i", { class: "key", style: `background:${color.codex}` }), t("kpiAgent"))),
      spy ? row(t("vsSpy"), t("vsSpySub"), pct(spy.total), tone(spy.total)) : null,
      rnd ? row(t("vsRandom"), t("vsRandomSub"), pct(rnd.total), tone(rnd.total)) : null,
      spy ? row(t("beatSpy"), null, `${spy.ahead} / ${spy.days}`) : null,
      row(t("hit"), t("hitSub"), a.traded ? `${rate(a)} (${a.hits}/${a.traded})` : "—"),
      row(t("days"), t("inception", { d: day(data.first_session, true) }), String(scored.length)));
  }

  // ------------------------------------------------------------ performance

  const ORDER = ["codex", "claude", "quant", "random", "spy"];

  function head(title, aside) {
    return h("div", { class: "head" }, h("h2", { text: title }), aside ? h("span", { class: "aside", text: aside }) : null);
  }

  function performance() {
    if (!scored.length) return null;
    const keys = ORDER.filter(has);
    const hide = new Set([2, 4, 5, 6]);
    const table = h("table", { class: "stats" },
      h("thead", {}, h("tr", {}, ...t("statCols").map((c, i) => h("th", { class: (i ? "n" : "") + (hide.has(i) ? " hide-s" : ""), text: c })))),
      h("tbody", {}, ...keys.map((k) => {
        const st = stats(k);
        const [name, sub] = t("arms." + k);
        return h("tr", { class: k === "codex" ? "lead" : "" },
          h("th", { scope: "row" }, h("span", { class: "name" }, h("i", { class: "key", style: `background:${color[k]}` }), name), h("span", { class: "sub", text: sub })),
          h("td", { class: "n num strong " + tone(st.total), text: pct(st.total) }),
          h("td", { class: "n num hide-s " + tone(st.mean), text: pct(st.mean) }),
          h("td", { class: "n num", text: rate(st) }),
          h("td", { class: "n num hide-s " + tone(st.best), text: pct(st.best) }),
          h("td", { class: "n num hide-s " + tone(st.worst), text: pct(st.worst) }),
          h("td", { class: "n num hide-s", text: String(st.days) }));
      })));
    return h("section", { class: "block", id: "performance" },
      head(t("perfTitle"), t("perfAside")),
      scored.length >= 2 ? h("div", { class: "panel chart", id: "chart" },
        h("div", { class: "legend" }, ...keys.map((k) => h("span", {}, h("i", { class: k === "spy" || k === "random" ? "dash" : "", style: `--c:${color[k]}` }), t("short." + k)))),
        h("div", { class: "plot" })) : null,
      h("div", { class: "panel table" }, table),
      h("p", { class: "note", text: t("sample", { n: scored.length, need: MIN_DAYS }) }));
  }

  function drawChart() {
    const box = document.querySelector("#chart .plot");
    if (!box) return;
    const n = scored.length;
    const keys = ["random", "spy", "quant", "claude", "codex"].filter(has);
    const lines = {};
    for (const k of keys) {
      let acc = 0, started = false;
      lines[k] = scored.map((x) => {
        const v = value(x, k);
        if (v == null) return started ? acc : null;
        started = true;
        acc += v;
        return acc;
      });
    }
    const W = Math.max(300, box.clientWidth || 800), narrow = W < 560, H = narrow ? 230 : 320;
    const pad = { l: 52, r: narrow ? 12 : 96, t: 14, b: 30 };
    const all = Object.values(lines).flat().filter((v) => v != null).concat([0]);
    let lo = Math.min(...all), hi = Math.max(...all);
    const m = (hi - lo) * 0.1 || 0.002; lo -= m; hi += m;
    const x = (i) => pad.l + (n === 1 ? 0 : (i / (n - 1)) * (W - pad.l - pad.r));
    const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("perfTitle") });
    const step = niceStep(hi - lo);
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      const r = Math.abs(v) < 1e-12 ? 0 : v;
      svg.append(s("line", { class: r === 0 ? "zero" : "grid", x1: pad.l, x2: W - pad.r, y1: y(r), y2: y(r) }));
      svg.append(s("text", { class: "axis", x: pad.l - 8, y: y(r) + 4, "text-anchor": "end" }, pct(r, 1)));
    }
    const marks = narrow ? [0, n - 1] : n <= 8 ? [...Array(n).keys()] : [0, Math.round((n - 1) / 3), Math.round((2 * (n - 1)) / 3), n - 1];
    for (const i of new Set(marks)) svg.append(s("text", { class: "axis", x: x(i), y: H - 8, "text-anchor": i === 0 ? "start" : i === n - 1 ? "end" : "middle" }, day(scored[i].date)));
    const ends = [];
    for (const k of keys) {
      const pts = lines[k];
      let d = "", first = true;
      pts.forEach((v, i) => { if (v == null) return; d += `${first ? "M" : "L"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`; first = false; });
      svg.append(s("path", { class: "series" + (k === "spy" || k === "random" ? " thin" : k === "codex" ? " lead" : ""), d, stroke: color[k] }));
      const last = pts[n - 1];
      if (last != null) ends.push({ k, y: y(last), v: last });
    }
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 15) ends[i].y = ends[i - 1].y + 15;
    if (!narrow) for (const e of ends) svg.append(s("text", { class: "end", x: W - pad.r + 8, y: e.y + 4, fill: color[e.k] }, `${t("short." + e.k)} ${pct(e.v, 1)}`));
    const cross = s("line", { class: "cross", y1: pad.t, y2: H - pad.b, x1: -10, x2: -10 });
    svg.append(cross);
    const tip = h("div", { class: "tip", hidden: true });
    const hit = s("rect", { x: pad.l, y: pad.t, width: W - pad.l - pad.r, height: H - pad.t - pad.b, fill: "transparent" });
    hit.addEventListener("pointermove", (evt) => {
      const r = svg.getBoundingClientRect();
      const px = ((evt.clientX - r.left) / r.width) * W;
      const i = Math.max(0, Math.min(n - 1, Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (n - 1))));
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
      tip.replaceChildren(h("div", { class: "muted", text: day(scored[i].date, true) }),
        ...keys.slice().reverse().filter((k) => lines[k][i] != null).map((k) =>
          h("div", { class: "row" }, h("span", {}, h("i", { class: "key", style: `background:${color[k]}` }), t("short." + k)), h("b", { class: "num " + tone(lines[k][i]), text: pct(lines[k][i]) }))));
      tip.hidden = false;
      tip.style.left = Math.min((x(i) / W) * r.width + 14, r.width - 180) + "px";
      tip.style.top = "40px";
    });
    hit.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("x1", -10); cross.setAttribute("x2", -10); });
    svg.append(hit);
    box.replaceChildren(svg, tip);
  }

  function niceStep(span) {
    const raw = span / 4;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    return [1, 2, 2.5, 5, 10].map((k) => k * mag).find((v) => span / v <= 5) || raw;
  }

  // ------------------------------------------------------------ pipeline

  function pipeline() {
    return h("section", { class: "block", id: "pipeline" },
      head(t("pipeTitle")),
      h("p", { class: "section-lede", text: t("pipeLede") }),
      h("ol", { class: "pipe" }, ...t("pipe").map(([when, name, key, body], i) =>
        h("li", { class: "stage" + (i === 3 ? " ai" : "") },
          h("div", { class: "stage-top" }, h("span", { class: "idx num", text: String(i + 1).padStart(2, "0") }), h("span", { class: "when num", text: when })),
          h("h3", { text: name }),
          h("p", { class: "key-fact", text: key }),
          h("p", { class: "body", text: body })))));
  }

  // ------------------------------------------------------------ comparison

  function versus() {
    const [, ours, theirs] = t("vsCols");
    return h("section", { class: "block", id: "compare" },
      head(t("vsTitle")),
      h("p", { class: "section-lede", text: t("vsLede") }),
      h("div", { class: "panel table" }, h("table", { class: "versus" },
        h("thead", {}, h("tr", {}, h("th", { text: "" }), h("th", { class: "us", text: ours }), h("th", { text: theirs }))),
        h("tbody", {}, ...t("vsRows").map(([cap, a, b]) => h("tr", {},
          h("th", { scope: "row", text: cap }),
          h("td", { class: "us" }, h("div", { class: "vcell" }, h("span", { class: "tick yes", "aria-hidden": "true", text: "✓" }), h("span", { text: a }))),
          h("td", { class: "them" }, h("div", { class: "vcell" }, h("span", { class: "tick no", "aria-hidden": "true", text: "✕" }), h("span", { text: b })))))))),
      h("p", { class: "note", text: t("vsNote") }));
  }

  // ------------------------------------------------------------ latest decision

  function memo(who, session, note) {
    const pick = session[who];
    if (!pick) return null;
    const top = h("div", { class: "memo-head" }, h("i", { class: "key", style: `background:${color[who]}` }), h("b", { text: t("arms." + who)[0] }), data.models[who] ? h("span", { class: "model", text: data.models[who] }) : null);
    if (!pick.answered) return h("article", { class: "panel memo" }, top, h("p", { class: "muted pad", text: t("noAnswer") }));
    const field = (label, ...content) => h("div", { class: "field" }, h("div", { class: "label", text: label }), h("div", { class: "content" }, ...content));
    const parts = [top, h("div", { class: "memo-grid" },
      field(t("pick"), h("span", { class: "ticker", text: pick.symbol || t("cash") }),
        note && note.stance && pick.symbol ? h("span", { class: "stance " + note.stance, text: t("stance." + note.stance) }) : null),
      field(t("result"), pick.symbol
        ? session.scored ? h("span", { class: "result num " + tone(pick.net), text: pct(pick.net) }) : h("span", { class: "muted", text: t("pending") })
        : h("span", { class: "result num", text: pct(0) })))];
    if (note) {
      if (!pick.symbol && note.symbol) parts.push(h("p", { class: "cash-line" }, t("cashLine"), " ", h("b", { text: note.symbol }), " ", note.stance ? h("span", { class: "stance " + note.stance, text: t("stance." + note.stance) }) : null));
      if (note.thesis) parts.push(field(t("thesis"), h("p", { text: note.thesis })));
      if (note.catalyst) parts.push(field(t("catalyst"), h("p", { text: note.catalyst })));
      const links = (note.sources || []).filter((x) => /^https:\/\//.test(x.url || ""));
      if (links.length) parts.push(field(t("sources"), h("ul", { class: "src" }, ...links.map((x) => h("li", {}, h("a", { href: x.url, target: "_blank", rel: "noopener noreferrer", text: (x.title || x.url) + " ↗" }))))));
    }
    return h("article", { class: "panel memo" }, ...parts);
  }

  function latest() {
    const last = data.sessions[data.sessions.length - 1];
    if (!last) return null;
    const cards = [memo("codex", last, data.latest.codex), memo("claude", last, data.latest.claude)].filter(Boolean);
    return h("section", { class: "block", id: "latest" },
      head(t("memoTitle"), t("memoAside", { d: day(last.date, true) })),
      h("div", { class: "memos" + (cards.length === 1 ? " one" : "") }, ...cards));
  }

  // ------------------------------------------------------------ model choice

  function models() {
    const d = data.duel;
    const card = (key, tag, sub) => h("div", { class: "panel model-card" },
      h("div", { class: "memo-head" }, h("i", { class: "key", style: `background:${color[key]}` }), h("b", { text: key === "codex" ? "GPT" : "Claude" }), h("span", { class: "tag", text: tag })),
      h("div", { class: "model-id", text: data.models[key] || (key === "codex" ? "Codex" : "Claude Code") }),
      h("p", { class: "muted", text: sub }));
    let body;
    if (d && d.scored_days) {
      const v = d.verdict || {};
      const verdict = v.state === "collecting" ? t("verdict.collecting", { d: v.days, n: v.needed || MIN_DAYS }) : t("verdict." + v.state);
      const [, gpt, claude] = t("duelCols");
      body = [h("div", { class: "panel table" }, h("table", { class: "duel" },
        h("thead", {}, h("tr", {}, h("th", { text: "" }), h("th", { class: "n", text: gpt }), h("th", { class: "n", text: claude }))),
        h("tbody", {},
          h("tr", {}, h("th", { scope: "row", text: t("duelRows.total") }), h("td", { class: "n num " + tone(d.codex_total), text: pct(d.codex_total) }), h("td", { class: "n num " + tone(d.claude_total), text: pct(d.claude_total) })),
          h("tr", {}, h("th", { scope: "row", text: t("duelRows.wins") }), h("td", { class: "n num", text: `${d.wins.codex || 0} / ${d.scored_days}` }), h("td", { class: "n num", text: `${d.wins.claude || 0} / ${d.scored_days}` })),
          h("tr", {}, h("th", { scope: "row", text: t("duelRows.same") }), h("td", { class: "n num", colspan: "2", text: `${d.same_pick || 0} / ${d.answered_both || 0}` }))))),
        h("p", { class: "note", text: verdict })];
    } else {
      body = [h("p", { class: "note", text: t("modelPending") })];
    }
    return h("section", { class: "block", id: "models" },
      head(t("modelTitle")),
      h("p", { class: "section-lede", text: t("modelLede") }),
      h("div", { class: "model-cards" }, card("codex", t("modelDefault"), t("modelGpt")), card("claude", t("modelOptional"), t("modelClaude"))),
      ...body);
  }

  // ------------------------------------------------------------ trade log

  function cell(p, scoredDay, extra) {
    if (!p || !p.answered) return h("td", { class: "muted " + (extra || ""), text: "—" });
    return h("td", { class: "pickcell " + (extra || "") }, h("b", { text: p.symbol || t("cash") }),
      p.symbol ? h("span", { class: "num " + (scoredDay ? tone(p.net) : "muted"), text: scoredDay ? pct(p.net) : "…" }) : null);
  }

  function tradeLog() {
    const rows = data.sessions.slice().reverse();
    if (!rows.length) return null;
    const shown = showAll ? rows : rows.slice(0, 10);
    const withClaude = data.sessions.some((x) => x.claude);
    const table = h("table", { class: "log" },
      h("thead", {}, h("tr", {},
        h("th", { text: t("date") }), h("th", { text: t("short.codex") }), withClaude ? h("th", { text: t("short.claude") }) : null,
        h("th", { class: "hide-s", text: t("short.quant") }), h("th", { class: "hide-s", text: t("short.random") }), h("th", { class: "n", text: "SPY" }))),
      h("tbody", {}, ...shown.map((x) => h("tr", {},
        h("td", { class: "num", text: day(x.date) }),
        cell(x.codex, x.scored), withClaude ? cell(x.claude, x.scored) : null,
        cell(x.quant, x.scored, "hide-s"), cell(x.random, x.scored, "hide-s"),
        h("td", { class: "n num " + tone(x.spy), text: x.scored ? pct(x.spy) : "…" })))));
    return h("section", { class: "block", id: "log" },
      head(t("logTitle"), t("logAside")),
      h("div", { class: "panel table" }, table),
      rows.length > 10 && !showAll ? h("div", { class: "more" }, h("button", { type: "button", class: "btn", text: t("showAll", { n: rows.length }), onclick: () => { showAll = true; render(); } })) : null);
  }

  // ------------------------------------------------------------ get started

  function run() {
    const cmds = ["pipx install tradeagent", "tradeagent doctor", "tradeagent dashboard"];
    return h("section", { class: "block run", id: "run" },
      h("div", { class: "run-copy" },
        h("h2", { text: t("runTitle") }),
        h("p", { class: "section-lede", text: t("runLede") }),
        h("p", { class: "req", text: t("runReq") }),
        h("a", { class: "btn primary", href: data.repo + "#get-started", text: t("runBtn") + " ↗" })),
      h("ol", { class: "cmds" }, ...cmds.map((c, i) => h("li", {}, h("span", { class: "label", text: t("runSteps")[i] }), h("code", { text: c })))));
  }

  function render() {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    window.TA_LANG = lang;
    window.dispatchEvent(new CustomEvent("ta:lang", { detail: { lang } }));
    document.getElementById("lang").textContent = t("lang");
    document.getElementById("gh").href = data.repo;
    document.querySelectorAll(".bar-nav a").forEach((a, i) => { a.textContent = t("nav")[i]; });
    document.getElementById("asof").textContent = data.generated_at ? t("asOf", { t: stamp(data.generated_at) }) : "";
    document.getElementById("top").replaceChildren(...[hero(), performance(), pipeline(), versus(), latest(), models(), tradeLog(), run()].filter(Boolean));
    const c = method.costs_bps || {};
    const cost = ["half_spread_bps", "slippage_bps", "fee_bps"].every((k) => typeof c[k] === "number")
      ? +(c.half_spread_bps + c.slippage_bps + c.fee_bps).toFixed(2) : 2.1;
    document.getElementById("foot").replaceChildren(...t("foot", { cost }).map((p) => h("p", { text: p })),
      h("p", {}, data.generated_at ? t("updated", { t: stamp(data.generated_at) }) + " " : "", h("a", { href: data.repo, text: t("source") })));
    drawChart();
  }

  document.getElementById("lang").addEventListener("click", () => {
    lang = lang === "zh" ? "en" : "zh";
    store.set("ta.site.lang", lang);
    render();
  });
  let resize;
  window.addEventListener("resize", () => { clearTimeout(resize); resize = setTimeout(drawChart, 150); });
  render();
})();
