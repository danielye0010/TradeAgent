/* TradeAgent weekly portfolios (weekly-hold-v1).
   One renderer for the public page (inside a shadow root), the local dashboard (/weekly)
   and `tradeagent weekly serve`. Reads weekly.json; never invents missing values. */
(() => {
  "use strict";

  const ARMS = ["codex", "claude", "quant", "random", "spy"];
  const UNIVERSE = { "weekly-hold-v1": 92 };
  const TZ = "America/New_York";
  const DOCS = "https://github.com/danielye0010/TradeAgent/blob/main/docs/weekly-hold-v1.md";
  const FEED_PAGE = 12;

  const COPY = {
    en: {
      lang: "中文",
      page: "Weekly portfolios",
      eyebrow: "Weekly portfolios · paper trading",
      title: "Five $10,000 portfolios. One decision a week.",
      lede: "Every week GPT (via Codex) and Claude each search the news and pick up to three US stocks from the same {n}-stock universe. They run next to a 12–1 momentum model, a random pick and buy-and-hold SPY, with identical fills and costs. Every decision is frozen before its first trade.",
      chipRunning: "Running weekly since {d}",
      chipPending: "First decision {d}",
      chipOff: "Not running yet",
      chipUnvalidated: "Not yet validated",
      chipOrders: "Real orders: {n}",
      nextLabel: "Next decision",
      nextAside: "11:30 ET",
      deciding: "Deciding now",
      awaiting: "Waiting for data",
      units: ["d", "h", "m"],
      when: "{d} · 11:30 ET",
      today: "Today",
      sched: {
        decide: ["Research & freeze", "both models search, pick and lock"],
        fill: ["Fill", "sells first, then buys at bid/ask"],
        risk: ["Risk check", "sell any holding 10% below cost"],
        value: ["Mark to close", "every portfolio valued"],
      },
      kpiAgent: "TradeAgent · GPT",
      kpiAgentAside: "Portfolio value",
      kpiStart: "started with $10,000",
      kpiTotal: "{p} since {d}",
      kpiNoMarks: "First close is marked on {d}.",
      ringTitle: "Weeks ahead of random",
      ringAside: "The number to watch",
      ringScored: "{n} scored weeks · GPT vs a random pick from the same universe",
      ringPending: "Scored once two decision days have passed.",
      countTitle: "On the record",
      countAside: "Every call, with its reason",
      counts: ["Decisions frozen", "Sources cited", "Simulated trades", "Real orders"],
      boardTitle: "Five portfolios",
      boardAside: "Same universe, same clock, same costs",
      modes: { main: "T+1 settlement", settled: "Settled cash only" },
      modeNote: "The main result follows cash-account T+1 rules. “Settled cash only” is a stricter sensitivity check that waits for every sale to settle before buying.",
      boardCols: ["Portfolio", "Value", "Last week", "Total", "vs SPY", "Max drawdown", "Cash", "Holdings"],
      cashOnly: "Cash",
      arms: {
        codex: ["TradeAgent · GPT", "Codex, its own news search"],
        claude: ["TradeAgent · Claude", "Claude Code, its own news search"],
        quant: ["12–1 momentum", "rules only, no AI"],
        random: ["Random", "seeded, same universe"],
        spy: ["S&P 500 (SPY)", "buy and hold"],
      },
      short: { codex: "GPT", claude: "Claude", quant: "Momentum", random: "Random", spy: "SPY" },
      chartTitle: "Portfolio value",
      chartAside: "Daily close · gaps stay gaps",
      chartEmpty: "The first value is marked at the close on {d}.",
      pipeTitle: "One week, six steps",
      pipeAside: "On a fixed clock, New York time",
      next: "Next",
      pipe: [
        ["Universe", "{n} US stocks", "A fixed list frozen with the protocol. No ETFs, no leveraged products."],
        ["Data packet", "11:29 ET", "Prices, 1-week to 12-month returns and current holdings, identical for both models."],
        ["AI research", "11:30–11:55 ET", "GPT and Claude each run their own web search and return up to three picks, with sources and what would prove them wrong."],
        ["Freeze", "by 11:55 ET", "Every decision is hashed before any fill. A late or invalid answer keeps last week's holdings."],
        ["Fill", "12:00–12:10 ET", "Sells first, then buys at the quoted bid/ask plus slippage. Equal weights; trades only past a 2% drift."],
        ["Risk & value", "15:45 / 17:15 ET daily", "A holding 10% below cost is sold and stays cash until next week. Every portfolio is marked to the close."],
      ],
      feedTitle: "Decision log",
      feedAside: "Every pick, newest first",
      all: "All",
      tags: { buy: "Buy", hold: "Hold", sell: "Sell", cash: "Cash", miss: "Missed", risk: "Risk exit" },
      why: {
        spy: "Benchmark: buy once, hold for the whole study",
        quant: "Top 3 by 12–1 momentum",
        random: "Seeded random draw · {s}",
        drop: "Not picked this week",
        cash: "No stock cleared the bar; stays in cash",
      },
      memo: {
        reason: "Why",
        invalid: "What would prove it wrong",
        view: "Portfolio view",
        exit: "Early-exit view (recorded, not acted on)",
        sources: "Sources",
        noSources: "No sources returned.",
        model: "Model",
        searches: "{n} web searches",
        method: "Method",
        quant: "Ranks the universe by the 12–1 month return at the previous close and holds the top three.",
        random: "Draws three stocks with a seed derived from the protocol and the date, so anyone can reproduce it.",
        spy: "Buys SPY with the whole account on the first decision day and never trades again.",
        seed: "Seed",
      },
      showAll: "Show all {n}",
      feedEmpty: "The first decision is on {d} at 11:30 ET.",
      posTitle: "Positions",
      posAside: "Open holdings across all five portfolios",
      posCols: ["Portfolio", "Stock", "Shares", "Avg cost", "Cost basis", "Held"],
      days: "{n} d",
      posEmpty: "No open positions yet.",
      weeksTitle: "Weekly returns",
      weeksAside: "Decision day to next decision day, after costs",
      weekCol: "Week",
      statusCol: "Status",
      weekStatus: { COMPLETE: "Final", PENDING: "In progress" },
      weeksEmpty: "The first full week is final on the second decision day.",
      duelTitle: "Head to head",
      duelAside: "Same weeks, paired",
      versus: "{a} vs {b}",
      sameExposure: "{a} vs SPY at the same exposure",
      duelSub: "average weekly difference · {n} weeks",
      duelSe: "standard error {se}",
      duelEmpty: "First paired week after {d}.",
      allComparisons: "All comparisons",
      cmpCols: ["Comparison", "Weeks", "Avg difference", "Std. error"],
      qualityTitle: "Data quality",
      qualityAside: "Missing data stays missing; nothing is backfilled",
      coverage: "Decisions on time",
      coverageVal: "{v} / {s}",
      issues: "Missing and delayed",
      noIssues: "No missing or delayed observations.",
      notesTitle: "How to read this",
      notes: [
        "Paper trading: fills use live quotes and modeled costs, not actual executions.",
        "Dividends count as receivables until they are paid and cannot fund purchases before then.",
        "Neighbouring weeks can be correlated, so the standard error is descriptive, not proof of an edge.",
        "Both main comparisons, GPT vs random and Claude vs random, are always shown. No winner is picked after the fact.",
        "Weeks with missing data are left out of the statistics but stay in the coverage and value records.",
      ],
      foot: "Protocol {p} · config {h}",
      data: "Full data (JSON)",
      method: "Method",
      updated: "Updated {t} ET",
      failed: "Weekly data is unavailable right now. Nothing is shown as zero.",
      loading: "Loading the weekly record…",
    },
    zh: {
      lang: "EN",
      page: "周度持仓",
      eyebrow: "周度持仓 · 模拟盘",
      title: "五个 1 万美元组合，每周只做一次决定。",
      lede: "每周，GPT（Codex）和 Claude 各自联网读新闻，从同一个 {n} 只美股池里挑最多 3 只。旁边同时跑 12–1 动量模型、随机组合和 SPY 买入持有，成交规则和成本完全一样，每个决策都在第一笔成交前冻结。",
      chipRunning: "{d} 起每周运行",
      chipPending: "首次决策 {d}",
      chipOff: "尚未运行",
      chipUnvalidated: "尚未验证",
      chipOrders: "真实订单：{n}",
      nextLabel: "下次决策",
      nextAside: "美东 11:30",
      deciding: "正在决策",
      awaiting: "等待数据",
      units: ["天", "时", "分"],
      when: "{d} · 美东 11:30",
      today: "今天",
      sched: {
        decide: ["研究并冻结", "两个模型各自搜索、选股、锁定"],
        fill: ["成交", "先卖后买，按买卖报价"],
        risk: ["风控检查", "较成本下跌 10% 即卖出"],
        value: ["收盘估值", "所有组合按收盘价计值"],
      },
      kpiAgent: "TradeAgent · GPT",
      kpiAgentAside: "组合市值",
      kpiStart: "初始 1 万美元",
      kpiTotal: "{d} 以来 {p}",
      kpiNoMarks: "{d} 收盘后出现第一个估值。",
      ringTitle: "跑赢随机的周数",
      ringAside: "最值得看的数字",
      ringScored: "已计分 {n} 周 · GPT 对同一股票池的随机组合",
      ringPending: "过完两个决策日后开始计分。",
      countTitle: "全部留档",
      countAside: "每一次决策都附理由",
      counts: ["冻结决策", "引用来源", "模拟成交", "真实订单"],
      boardTitle: "五个组合",
      boardAside: "同一股票池、同一时间表、同样成本",
      modes: { main: "T+1 结算", settled: "仅用已交割资金" },
      modeNote: "主结果按现金账户 T+1 规则计算。“仅用已交割资金”是更严格的敏感性检验：卖出款交割之后才买入。",
      boardCols: ["组合", "市值", "上周", "累计", "相对 SPY", "最大回撤", "现金", "持仓"],
      cashOnly: "现金",
      arms: {
        codex: ["TradeAgent · GPT", "Codex，自行搜新闻"],
        claude: ["TradeAgent · Claude", "Claude Code，自行搜新闻"],
        quant: ["12–1 动量", "纯规则，不用 AI"],
        random: ["随机组合", "固定种子，同一股票池"],
        spy: ["标普 500（SPY）", "买入持有"],
      },
      short: { codex: "GPT", claude: "Claude", quant: "动量", random: "随机", spy: "SPY" },
      chartTitle: "组合市值",
      chartAside: "每日收盘 · 缺失不补",
      chartEmpty: "{d} 收盘后出现第一个估值点。",
      pipeTitle: "一周六步",
      pipeAside: "固定时间表，美东时间",
      next: "下一步",
      pipe: [
        ["股票池", "{n} 只美股", "随协议冻结的固定名单，不含 ETF 和杠杆产品。"],
        ["数据包", "美东 11:29", "价格、1 周到 12 个月收益和当前持仓，两个模型拿到的完全一样。"],
        ["AI 研究", "美东 11:30–11:55", "GPT 和 Claude 各自联网搜索，最多选 3 只，附来源和失效条件。"],
        ["冻结", "美东 11:55 前", "所有决策在成交前写入哈希。超时或格式不对，沿用上周持仓。"],
        ["成交", "美东 12:00–12:10", "先卖后买，按买卖报价加滑点成交。等权持有，偏离超过 2% 才调仓。"],
        ["风控与估值", "每日美东 15:45 / 17:15", "较成本下跌 10% 的持仓卖出，持现金到下周。每天按收盘价估值。"],
      ],
      feedTitle: "决策记录",
      feedAside: "每一笔选择，最新在前",
      all: "全部",
      tags: { buy: "买入", hold: "持有", sell: "卖出", cash: "空仓", miss: "缺失", risk: "风控卖出" },
      why: {
        spy: "基准组：首个决策日买入，之后一直持有",
        quant: "12–1 动量排名前 3",
        random: "固定种子随机抽取 · {s}",
        drop: "本周未入选",
        cash: "没有股票达到门槛，持有现金",
      },
      memo: {
        reason: "理由",
        invalid: "失效条件",
        view: "组合观点",
        exit: "提前退出看法（只记录，不执行）",
        sources: "来源",
        noSources: "没有返回来源。",
        model: "模型",
        searches: "联网搜索 {n} 次",
        method: "方法",
        quant: "按上一交易日收盘的 12–1 个月收益给股票池排序，持有前 3 名。",
        random: "用协议和日期生成的种子抽 3 只，任何人都能复现。",
        spy: "首个决策日全仓买入 SPY，之后不再交易。",
        seed: "种子",
      },
      showAll: "显示全部 {n} 条",
      feedEmpty: "第一次决策在 {d} 美东 11:30。",
      posTitle: "当前持仓",
      posAside: "五个组合的全部持仓",
      posCols: ["组合", "股票", "股数", "平均成本", "持仓成本", "已持有"],
      days: "{n} 天",
      posEmpty: "还没有持仓。",
      weeksTitle: "每周收益",
      weeksAside: "从决策日到下一个决策日，扣除成本",
      weekCol: "周",
      statusCol: "状态",
      weekStatus: { COMPLETE: "已定", PENDING: "进行中" },
      weeksEmpty: "第二个决策日之后出现第一条完整周收益。",
      duelTitle: "正面对比",
      duelAside: "同周配对",
      versus: "{a} 对 {b}",
      sameExposure: "{a} 对同等仓位的 SPY",
      duelSub: "平均每周差值 · {n} 周",
      duelSe: "标准误 {se}",
      duelEmpty: "{d} 之后出现第一组配对周。",
      allComparisons: "全部对比",
      cmpCols: ["对比", "周数", "平均差值", "标准误"],
      qualityTitle: "数据质量",
      qualityAside: "缺失就是缺失，不回填",
      coverage: "按时完成的决策",
      coverageVal: "{v} / {s}",
      issues: "缺失与延迟",
      noIssues: "没有缺失或延迟记录。",
      notesTitle: "怎么看这些数字",
      notes: [
        "模拟盘：按实时报价和模型化成本计算，不是实际成交。",
        "分红在派息日到账前记为应收，不能用来买入。",
        "相邻几周的收益可能相关，标准误只作描述，不代表已经证明有超额收益。",
        "两组主要对比（GPT 对随机、Claude 对随机）始终同时展示，不事后挑赢家。",
        "缺失的周不计入统计，但保留在覆盖率和市值记录里。",
      ],
      foot: "协议 {p} · 配置 {h}",
      data: "完整数据（JSON）",
      method: "方法说明",
      updated: "更新于美东 {t}",
      failed: "周度数据暂时无法读取。不会显示为零收益。",
      loading: "正在读取周度记录…",
    },
  };

  // Engine reasons are fixed English strings; show them in the reader's language.
  const REASONS = [
    ["missed or interrupted weekly decision", "A weekly decision was missed or interrupted; not backfilled", "周度决策错过或中断，不回填"],
    ["missing pre-cutoff packet or decision deadline", "No data before the cutoff, or the answer missed the deadline", "截止前没有数据，或回答错过时限"],
    ["model completed after frozen deadline", "The model answered after the deadline", "模型在截止时间之后才回答"],
    ["decision NAV unavailable", "The portfolio could not be valued at decision time", "决策时无法给组合估值"],
    ["decision valuation unavailable", "No price for this stock at decision time", "决策时缺少该股价格"],
    ["missed fill window", "Missed the fill window; not backfilled", "错过成交窗口，不回填"],
    ["no affordable cash", "Not enough cash to buy", "现金不足以买入"],
    ["settled cash unavailable", "Waiting for sale proceeds to settle (sensitivity book)", "等待卖出款交割（敏感性账本）"],
    ["risk exit", "Fell 10% below cost; sold, cash until next week", "较成本下跌 10%，已卖出，持现金到下周"],
    ["early close", "Early close: no 15:45 risk check that day", "提前收盘，当天没有 15:45 风控检查"],
    ["missed risk check", "Risk check missed; no stale fill", "错过风控检查，不用过期价格补做"],
    ["corporate action source pending", "Corporate-action data pending; no fill guessed", "公司行动数据待定，不猜测成交"],
    ["risk quote missing", "No quote at the risk check", "风控检查时缺少报价"],
    ["funding not settled", "Sale deferred until the purchase funds settle", "买入资金未交割，延后卖出"],
  ];

  const storage = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked */ } },
  };
  const navLang = () => ((navigator.language || "en").toLowerCase().startsWith("zh") ? "zh" : "en");

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "text") el.textContent = v;
      else if (k === "html") el.innerHTML = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid);
    return el;
  }
  const esc = (x) => String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isNum = (x) => typeof x === "number" && Number.isFinite(x);

  function nyParts(ms) {
    const f = new Intl.DateTimeFormat("en-US", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23", weekday: "short" });
    const p = {};
    for (const x of f.formatToParts(new Date(ms))) p[x.type] = x.value;
    return { date: `${p.year}-${p.month}-${p.day}`, hm: Number(p.hour) * 60 + Number(p.minute), wd: p.weekday };
  }
  function nyInstant(iso, hh, mm) {
    const [y, m, d] = iso.split("-").map(Number);
    let guess = Date.UTC(y, m - 1, d, hh + 5, mm);
    for (let i = 0; i < 3; i += 1) {
      const p = nyParts(guess);
      const [py, pm, pd] = p.date.split("-").map(Number);
      const diff = (Date.UTC(py, pm - 1, pd) - Date.UTC(y, m - 1, d)) / 60000 + p.hm - (hh * 60 + mm);
      if (!diff) break;
      guess -= diff * 60000;
    }
    return guess;
  }

  function mount(host, opts) {
    const embedded = Boolean(opts && opts.embedded);
    let lang = (embedded && window.TA_LANG) || storage.get(embedded ? "ta.site.lang" : "ta.lang") || storage.get("ta.site.lang") || navLang();
    if (lang !== "zh") lang = "en";
    const updown = storage.get("ta.updown") || (!embedded && lang === "zh" ? "cn" : "us");
    let mode = "main";
    let filter = "all";
    let feedAll = false;
    let data = null;
    let failed = false;
    const root = h("div", { class: "wk", "data-updown": updown });
    host.replaceChildren(root);
    const observers = [];

    const t = (path, vars) => {
      let v = path.split(".").reduce((o, k) => (o == null ? o : o[k]), COPY[lang]);
      if (typeof v === "string" && vars) v = v.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? ""));
      return v;
    };
    const locale = () => (lang === "zh" ? "zh-CN" : "en-US");
    const money = (x) => (isNum(x) ? "$" + x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—");
    const pct = (x, digits = 2) => (isNum(x) ? (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x * 100).toFixed(digits) + "%" : "—");
    const tone = (x) => (isNum(x) && x > 0 ? "up" : isNum(x) && x < 0 ? "down" : "");
    const isoDate = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d, 12)); };
    const day = (iso) => (iso ? new Intl.DateTimeFormat(locale(), { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(isoDate(iso)) : "—");
    const dayShort = (iso) => (iso ? new Intl.DateTimeFormat(locale(), { month: lang === "zh" ? "numeric" : "short", day: "numeric", timeZone: "UTC" }).format(isoDate(iso)) : "—");
    const stamp = (sec) => new Intl.DateTimeFormat(locale(), { timeZone: TZ, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(sec * 1000));
    const reason = (text) => {
      if (!text) return "";
      const hit = REASONS.find(([k]) => String(text).startsWith(k));
      return hit ? hit[lang === "zh" ? 2 : 1] : text;
    };
    const color = (arm) => `var(--${arm})`;
    const key = (arm) => h("i", { class: "wk-key", style: `background:${color(arm)}` });

    const book = (arm) => ((data && data.books) || {})[`${mode}:${arm}`] || {};
    const weeks = () => ((data && data.weeks) || []).slice().sort((a, b) => (a.week < b.week ? -1 : 1));
    const ret = (w, arm) => (w && w.returns ? w.returns[`${mode}:${arm}`] : null);
    const universe = () => UNIVERSE[(data && data.protocol) || "weekly-hold-v1"] || 92;
    const started = () => data && data.status === "UNVALIDATED";

    function schedule() {
      const ws = weeks();
      const last = ws[ws.length - 1];
      return { current: last ? last.week : null, next: last ? last.end : data && data.start_date };
    }

    function head(title, aside, tools) {
      return h("div", { class: "wk-head" }, h("h2", { text: title }), aside ? h("p", { text: aside }) : null, tools ? h("div", { class: "wk-tools" }, tools) : null);
    }
    function section(id, title, aside, body, tools) {
      return h("section", { class: "wk-sec", id: "wk-" + id }, head(title, aside, tools), body);
    }

    // ------------------------------------------------------------------ hero
    function chips() {
      const out = [];
      const status = data ? data.status : null;
      if (status === "UNVALIDATED") out.push(h("span", { class: "wk-chip" }, h("i", { class: "wk-dot" }), t("chipRunning", { d: day(data.start_date) })));
      else if (status === "PENDING_START") out.push(h("span", { class: "wk-chip" }, h("i", { class: "wk-dot idle" }), t("chipPending", { d: day(data.start_date) })));
      else out.push(h("span", { class: "wk-chip" }, h("i", { class: "wk-dot off" }), t("chipOff")));
      out.push(h("span", { class: "wk-chip", text: t("chipUnvalidated") }));
      out.push(h("span", { class: "wk-chip", text: t("chipOrders", { n: (data && data.real_orders_submitted) ?? 0 }) }));
      return h("div", { class: "wk-chips" }, out);
    }

    function clock() {
      const card = h("div", { class: "wk-card wk-clock" });
      const { current, next } = data ? schedule() : {};
      card.append(h("div", { class: "wk-label-row" }, h("span", { class: "wk-label", text: t("nextLabel") }), h("span", { class: "wk-aside", text: t("nextAside") })));
      if (!next) {
        card.append(h("div", { class: "wk-count", text: "—" }), h("p", { class: "wk-when", text: t("chipOff") }));
        return card;
      }
      const now = Date.now();
      const target = nyInstant(next, 11, 30);
      const diff = target - now;
      const count = h("div", { class: "wk-count num" });
      if (diff > 0) {
        const mins = Math.floor(diff / 60000);
        const parts = [Math.floor(mins / 1440), Math.floor((mins % 1440) / 60), mins % 60];
        const u = t("units");
        parts.forEach((v, i) => { if (i > 0 || v > 0) { count.append(String(v)); count.append(h("small", { text: u[i] })); } });
      } else {
        count.textContent = diff > -90 * 60000 ? t("deciding") : t("awaiting");
        count.style.fontSize = "28px";
      }
      card.append(count, h("p", { class: "wk-when", text: t("when", { d: day(next) }) }));
      const from = current ? nyInstant(current, 11, 30) : null;
      const progress = from && target > from ? Math.min(1, Math.max(0, (now - from) / (target - from))) : 0;
      card.append(
        h("div", { class: "wk-track" }, h("i", { style: `width:${(progress * 100).toFixed(1)}%` }), h("b", { style: `left:${(progress * 100).toFixed(1)}%` })),
        h("div", { class: "wk-track-ends" }, h("span", { text: current ? dayShort(current) : t("today") }), h("span", { text: dayShort(next) })),
      );
      const p = nyParts(now);
      const weekday = !["Sat", "Sun"].includes(p.wd);
      const decisionDay = p.date === next || p.date === current;
      const live = started();
      const events = [
        ["decide", "11:30", 690, decisionDay],
        ["fill", "12:00", 730, decisionDay],
        ["risk", "15:45", 945, weekday && (live || decisionDay)],
        ["value", "17:15", 1035, weekday && (live || decisionDay)],
      ];
      let marked = false;
      const list = h("ol", { class: "wk-sched" });
      for (const [k, time, at, today] of events) {
        const done = today && p.hm >= at;
        let state = done ? "done" : "";
        if (!marked && today && !done) { state = "next"; marked = true; }
        const [name, sub] = t("sched." + k);
        list.append(h("li", { class: state }, h("span", { class: "t", text: time }), h("span", { class: "s" }), h("span", { class: "what" }, h("b", { text: name }), " ", h("span", { text: "· " + sub }))));
      }
      if (!marked) list.firstChild.className = "next";
      card.append(list);
      return card;
    }

    function nextStage() {
      if (!data || !data.start_date) return 1;
      const { current, next } = schedule();
      const p = nyParts(Date.now());
      const decisionDay = p.date === next || p.date === current;
      const weekday = !["Sat", "Sun"].includes(p.wd);
      if (decisionDay && p.hm < 689) return 1;
      if (decisionDay && p.hm < 715) return 2;
      if (decisionDay && p.hm < 720) return 3;
      if (decisionDay && p.hm < 730) return 4;
      if (weekday && started() && p.hm < 1035) return 5;
      return 1;
    }

    function hero() {
      return h("div", { class: "wk-hero" },
        h("div", {},
          h("p", { class: "wk-eyebrow", text: t("eyebrow") }),
          h("h1", { class: "wk-title", text: t("title") }),
          h("p", { class: "wk-lede", text: t("lede", { n: universe() }) }),
          chips()),
        clockHost);
    }

    // ------------------------------------------------------------------ KPI row
    function spark(series) {
      const pts = series.filter((m) => isNum(m.nav));
      const W = 400, H = 86;
      if (pts.length < 2) {
        return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><line x1="0" x2="${W}" y1="${H - 20}" y2="${H - 20}" stroke="var(--rule)" stroke-dasharray="4 4"/></svg>`;
      }
      const vals = pts.map((m) => m.nav).concat(10000);
      const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
      const x = (i) => (i / (pts.length - 1)) * W;
      const y = (v) => H - 6 - ((v - lo) / span) * (H - 14);
      const line = pts.map((m, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(m.nav).toFixed(1)}`).join(" ");
      const last = pts[pts.length - 1];
      return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="wkfill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="var(--codex)" stop-opacity=".22"/><stop offset="1" stop-color="var(--codex)" stop-opacity="0"/></linearGradient></defs><line x1="0" x2="${W}" y1="${y(10000).toFixed(1)}" y2="${y(10000).toFixed(1)}" stroke="var(--rule)" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/><path d="${line} L${W},${H} L0,${H} Z" fill="url(#wkfill)"/><path d="${line}" fill="none" stroke="var(--codex)" stroke-width="2" vector-effect="non-scaling-stroke"/><circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(last.nav).toFixed(1)}" r="3.5" fill="var(--codex)"/></svg>`;
    }

    function kpis() {
      const b = book("codex");
      const total = b.cumulative_return;
      const agent = h("div", { class: "wk-card wk-kpi" },
        h("div", { class: "wk-label-row" }, h("span", { class: "wk-label", text: t("kpiAgent") }), h("span", { class: "wk-aside", text: t("kpiAgentAside") })),
        h("div", { class: "wk-big num", text: money(isNum(b.nav) ? b.nav : 10000) }),
        h("p", { class: "wk-sub" }, isNum(total)
          ? h("span", {}, h("b", { class: tone(total), text: pct(total) }), " · ", t("kpiStart"))
          : (data && data.start_date ? t("kpiNoMarks", { d: day(data.start_date) }) : t("kpiStart"))),
        h("div", { class: "wk-spark", html: spark(b.nav_series || []) }));

      let wins = 0, losses = 0;
      for (const w of weeks()) {
        const a = ret(w, "codex"), r = ret(w, "random");
        if (isNum(a) && isNum(r)) { if (a > r) wins += 1; else if (a < r) losses += 1; }
      }
      const n = wins + losses;
      const share = n ? wins / n : 0;
      const C = 2 * Math.PI * 46;
      const ring = `<svg viewBox="0 0 116 116" aria-hidden="true"><circle cx="58" cy="58" r="46" fill="none" stroke="var(--soft-2)" stroke-width="11"/>${n ? `<circle cx="58" cy="58" r="46" fill="none" stroke="var(--codex)" stroke-width="11" stroke-linecap="round" stroke-dasharray="${(share * C).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 58 58)"/>` : ""}</svg>`;
      const gauge = h("div", { class: "wk-card wk-kpi" },
        h("div", { class: "wk-label-row" }, h("span", { class: "wk-label", text: t("ringTitle") }), h("span", { class: "wk-aside", text: t("ringAside") })),
        h("div", { class: "wk-ring" }, h("div", { html: ring }),
          h("div", { class: "wk-ring-txt" },
            h("div", { class: "v num", text: n ? Math.round(share * 100) + "%" : "—" }),
            h("div", { class: "wl" }, h("span", { class: "w", text: "W " + wins }), " / ", h("span", { class: "l", text: "L " + losses })),
            h("p", { text: n ? t("ringScored", { n }) : t("ringPending") }))));

      const decisions = (data && data.decisions) || [];
      let sources = 0;
      for (const d of decisions) for (const r of Object.values(d.arms || {})) sources += ((r.selection && r.selection.sources) || []).length;
      const values = [decisions.length, sources, (data && data.trade_count) || 0, (data && data.real_orders_submitted) ?? 0];
      const counter = h("div", { class: "wk-card wk-kpi" },
        h("div", { class: "wk-label-row" }, h("span", { class: "wk-label", text: t("countTitle") }), h("span", { class: "wk-aside", text: t("countAside") })),
        h("div", { class: "wk-counts" }, t("counts").map((label, i) => h("div", {}, h("b", { class: "num", text: values[i].toLocaleString("en-US") }), h("span", { text: label })))));
      return h("div", { class: "wk-kpis" }, agent, gauge, counter);
    }

    // ------------------------------------------------------------------ board
    function modeSwitch() {
      return h("div", { class: "wk-seg", role: "group", "aria-label": t("modes.main") },
        ["main", "settled"].map((m) => h("button", { type: "button", "aria-pressed": String(mode === m), text: t("modes." + m), onclick: () => { mode = m; render(); } })));
    }

    function board() {
      const ws = weeks().filter((w) => w.status === "COMPLETE");
      const lastWeek = ws[ws.length - 1];
      const spyTotal = book("spy").cumulative_return;
      const order = ARMS.slice();
      if (ARMS.some((a) => isNum(book(a).cumulative_return))) {
        order.sort((a, b) => (book(b).cumulative_return ?? -Infinity) - (book(a).cumulative_return ?? -Infinity));
      }
      const rows = order.map((arm) => {
        const b = book(arm);
        const total = b.cumulative_return;
        const vs = arm !== "spy" && isNum(total) && isNum(spyTotal) ? total - spyTotal : null;
        const lw = lastWeek ? ret(lastWeek, arm) : null;
        const cash = isNum(b.cash) && isNum(b.nav) && b.nav > 0 ? b.cash / b.nav : null;
        const held = (b.positions || []).map((p) => p.symbol);
        const [name, sub] = t("arms." + arm);
        return h("tr", {},
          h("td", {}, h("div", { class: "wk-arm" }, key(arm), h("div", {}, h("b", { text: name }), h("small", { text: sub })))),
          h("td", { class: "num", text: money(isNum(b.nav) ? b.nav : null) }),
          h("td", { class: "num " + tone(lw), text: pct(lw) }),
          h("td", { class: "num " + tone(total), text: pct(total) }),
          h("td", { class: "num " + tone(vs), text: pct(vs) }),
          h("td", { class: "num " + (isNum(b.drawdown) && b.drawdown < 0 ? "down" : ""), text: isNum(b.drawdown) ? pct(b.drawdown) : "—" }),
          h("td", { class: "num", text: isNum(cash) ? Math.round(cash * 100) + "%" : "—" }),
          h("td", {}, held.length ? h("span", { class: "wk-tickers" }, held.map((s) => h("span", { class: "wk-tk", text: s }))) : h("span", { class: "wk-empty-cell", text: t("cashOnly") })));
      });
      const table = h("div", { class: "wk-card wk-scroll" }, h("table", { class: "wk-tbl" },
        h("thead", {}, h("tr", {}, t("boardCols").map((c) => h("th", { text: c })))),
        h("tbody", {}, rows)));
      return section("board", t("boardTitle"), t("boardAside"), h("div", {}, table, h("p", { class: "wk-note", text: t("modeNote") })), modeSwitch());
    }

    // ------------------------------------------------------------------ chart
    function chart() {
      const box = h("div", { class: "wk-chart" });
      const series = ARMS.map((arm) => ({ arm, marks: (book(arm).nav_series || []) }));
      const dates = [...new Set(series.flatMap((s) => s.marks.map((m) => m.date)))].sort();
      const legend = h("div", { class: "wk-legend" }, ARMS.map((arm) => h("span", {}, h("i", { class: arm === "spy" || arm === "random" ? "dash" : "", style: `border-color:${color(arm)}` }), t("short." + arm))));
      const card = h("div", { class: "wk-card" }, box);
      if (dates.length < 2) {
        box.append(legend, h("div", { class: "wk-placeholder" }, h("span", { text: t("chartEmpty", { d: day((data && data.start_date) || dates[0]) }) })));
        return section("chart", t("chartTitle"), t("chartAside"), card);
      }
      const svgHost = h("div");
      const tip = h("div", { class: "wk-tip", hidden: true });
      box.append(legend, svgHost, tip);
      const draw = () => {
        const W = Math.max(320, box.clientWidth - 28);
        const small = W < 560;
        const H = small ? 240 : 300;
        const L = 58, R = small ? 58 : 112, T = 10, B = 26;
        const all = series.flatMap((s) => s.marks.filter((m) => isNum(m.nav)).map((m) => m.nav)).concat(10000);
        let lo = Math.min(...all), hi = Math.max(...all);
        const padv = (hi - lo || 100) * 0.12; lo -= padv; hi += padv;
        const x = (i) => L + (i / (dates.length - 1)) * (W - L - R);
        const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
        const step = (() => { const raw = (hi - lo) / 4; const p = 10 ** Math.floor(Math.log10(raw)); return [1, 2, 2.5, 5, 10].map((k) => k * p).find((k) => raw <= k) || raw; })();
        let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(t("chartTitle"))}">`;
        for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
          svg += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"/><text class="axis" x="${L - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end">${Math.round(v).toLocaleString("en-US")}</text>`;
        }
        svg += `<line class="base" x1="${L}" x2="${W - R}" y1="${y(10000).toFixed(1)}" y2="${y(10000).toFixed(1)}"/>`;
        const ticks = [0, Math.floor((dates.length - 1) / 2), dates.length - 1].filter((v, i, a) => a.indexOf(v) === i);
        for (const i of ticks) svg += `<text class="axis" x="${x(i).toFixed(1)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === dates.length - 1 ? "end" : "middle"}">${esc(dayShort(dates[i]))}</text>`;
        const ends = [];
        for (const s of series) {
          let path = "", open = false, last = null;
          for (const m of s.marks) {
            const i = dates.indexOf(m.date);
            if (!isNum(m.nav) || i < 0) { open = false; continue; }
            path += `${open ? "L" : "M"}${x(i).toFixed(1)},${y(m.nav).toFixed(1)} `;
            open = true; last = { i, v: m.nav };
          }
          const dash = s.arm === "spy" ? ' stroke-dasharray="5 4"' : s.arm === "random" ? ' stroke-dasharray="3 3"' : "";
          if (path) svg += `<path class="line" d="${path}" stroke="${color(s.arm)}"${dash}/>`;
          if (last) ends.push({ arm: s.arm, y: y(last.v), v: last.v, x: x(last.i) });
        }
        ends.sort((a, b) => a.y - b.y);
        for (let i = 1; i < ends.length; i += 1) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
        for (const e of ends) svg += `<text class="end" x="${(e.x + 8).toFixed(1)}" y="${(e.y + 4).toFixed(1)}" fill="${color(e.arm)}">${small ? "" : esc(t("short." + e.arm)) + " "}${esc(pct(e.v / 10000 - 1, 1))}</text>`;
        svg += `<line class="cross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/><rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/></svg>`;
        svgHost.innerHTML = svg;
        const el = svgHost.firstChild;
        const cross = el.querySelector(".cross");
        const overlay = el.querySelector("rect");
        overlay.addEventListener("mousemove", (ev) => {
          const r = el.getBoundingClientRect();
          const px = ((ev.clientX - r.left) / r.width) * W;
          const i = Math.max(0, Math.min(dates.length - 1, Math.round(((px - L) / (W - L - R)) * (dates.length - 1))));
          cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
          tip.replaceChildren(h("div", { class: "d", text: day(dates[i]) }), ...series.map((s) => {
            const m = s.marks.find((q) => q.date === dates[i]);
            return h("div", {}, h("span", { style: `color:${color(s.arm)}`, text: t("short." + s.arm) }), h("b", { class: "num", text: m && isNum(m.nav) ? money(m.nav) : "—" }));
          }));
          tip.hidden = false;
          const left = (x(i) / W) * r.width + 14;
          tip.style.left = (left + 190 > r.width ? left - 204 : left) + "px";
          tip.style.top = "48px";
        });
        overlay.addEventListener("mouseleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
      };
      if (typeof ResizeObserver === "function") {
        const ro = new ResizeObserver(() => draw());
        ro.observe(box);
        observers.push(ro);
      }
      requestAnimationFrame(draw);
      return section("chart", t("chartTitle"), t("chartAside"), card);
    }

    // ------------------------------------------------------------------ pipeline
    function pipeline() {
      const nxt = nextStage();
      return section("pipe", t("pipeTitle"), t("pipeAside"), h("ol", { class: "wk-pipe" }, t("pipe").map(([name, at, text], i) =>
        h("li", { class: "wk-stage" + (i === nxt ? " is-next" : "") },
          i === nxt ? h("span", { class: "wk-stamp", text: t("next") }) : null,
          h("span", { class: "idx", text: String(i + 1).padStart(2, "0") }),
          h("h3", { text: name }),
          h("p", { class: "at", text: at.replace("{n}", universe()) }),
          h("p", { text }))))) ;
    }

    // ------------------------------------------------------------------ decision log
    function feedRows() {
      const decisions = ((data && data.decisions) || []).slice().sort((a, b) => (a.week < b.week ? -1 : 1));
      const prev = {};
      const rows = [];
      for (const d of decisions) {
        ARMS.forEach((arm, order) => {
          const r = (d.arms || {})[arm];
          if (!r) return;
          const base = { date: d.week, arm, order, r, d };
          if (r.status !== "VALID") { rows.push({ ...base, tag: "miss", sym: "", why: reason(r.reason) || r.status }); return; }
          const picks = (r.selection && r.selection.picks) || [];
          if (arm === "spy") { if (picks.length) rows.push({ ...base, tag: "buy", sym: "SPY", why: t("why.spy") }); return; }
          const syms = picks.map((p) => p.symbol);
          const before = prev[arm] || [];
          const auto = arm === "quant" ? t("why.quant") : arm === "random" ? t("why.random", { s: String(r.seed || "").slice(0, 8) }) : "";
          if (!syms.length) rows.push({ ...base, tag: "cash", sym: "", why: (r.selection && r.selection.reason) || t("why.cash") });
          for (const p of picks) rows.push({ ...base, tag: before.includes(p.symbol) ? "hold" : "buy", sym: p.symbol, pick: p, why: p.reason || auto });
          for (const s of before) if (!syms.includes(s)) rows.push({ ...base, tag: "sell", sym: s, why: t("why.drop") });
          prev[arm] = syms;
        });
      }
      for (const m of (data && data.missing) || []) {
        if (!String(m.reason || "").startsWith("risk exit") || !String(m.book || "").startsWith(mode + ":")) continue;
        const arm = m.book.split(":")[1];
        rows.push({ date: m.date || m.week, arm, order: ARMS.indexOf(arm), tag: "risk", sym: m.symbol, why: reason(m.reason) });
      }
      const rank = { sell: 0, risk: 0, buy: 1, hold: 2, cash: 3, miss: 4 };
      rows.sort((a, b) => (a.date === b.date ? a.order - b.order || rank[a.tag] - rank[b.tag] : a.date < b.date ? 1 : -1));
      return rows;
    }

    function memo(row) {
      const r = row.r || {};
      const sel = r.selection || {};
      const left = h("dl");
      const add = (label, value) => { if (value) left.append(h("dt", { text: label }), h("dd", { text: value })); };
      if (row.arm === "codex" || row.arm === "claude") {
        add(t("memo.reason"), (row.pick && row.pick.reason) || (row.tag === "miss" ? row.why : ""));
        add(t("memo.invalid"), row.pick && row.pick.invalidation);
        add(t("memo.view"), sel.reason);
        add(t("memo.exit"), sel.early_exit_advice);
        add(t("memo.model"), [r.model, isNum(r.web_searches) ? t("memo.searches", { n: r.web_searches }) : null].filter(Boolean).join(" · "));
        const src = sel.sources || [];
        const right = h("div", {}, h("dt", { text: t("memo.sources") }),
          src.length
            ? h("ul", { class: "src" }, src.map((s) => h("li", {},
              /^https:\/\//.test(s.url || "") ? h("a", { href: s.url, target: "_blank", rel: "noopener noreferrer", text: s.claim || s.url }) : h("span", { text: s.claim || "" }),
              s.published_at ? h("time", { text: String(s.published_at).replace("T", " ").slice(0, 16) }) : null)))
            : h("dd", { class: "muted", text: t("memo.noSources") }));
        return h("div", { class: "wk-memo" }, left, right);
      }
      add(t("memo.method"), t("memo." + row.arm));
      if (row.arm === "random" && r.seed) add(t("memo.seed"), String(r.seed));
      if (row.tag === "miss" || row.tag === "risk") add(t("memo.reason"), row.why);
      return h("div", { class: "wk-memo" }, left, h("div"));
    }

    function feed() {
      const rows = feedRows().filter((r) => filter === "all" || r.arm === filter);
      const filters = h("div", { class: "wk-filters" },
        ["all", ...ARMS].map((f) => h("button", { type: "button", "aria-pressed": String(filter === f), text: f === "all" ? t("all") : t("short." + f), onclick: () => { filter = f; feedAll = false; render(); } })));
      const card = h("div", { class: "wk-card wk-feed" });
      if (!rows.length) {
        card.append(h("p", { class: "wk-empty", text: t("feedEmpty", { d: day(schedule().next) }) }));
      } else {
        for (const row of feedAll ? rows : rows.slice(0, FEED_PAGE)) {
          card.append(h("details", {},
            h("summary", {},
              h("span", { class: "d", text: dayShort(row.date) }),
              h("span", { class: "wk-tag " + row.tag, text: t("tags." + row.tag) }),
              h("span", { class: "who" }, key(row.arm), t("short." + row.arm)),
              h("span", { class: "sym", text: row.sym || "—" }),
              h("span", { class: "why", text: row.why || "" })),
            memo(row)));
        }
        if (!feedAll && rows.length > FEED_PAGE) card.append(h("button", { type: "button", class: "wk-more", text: t("showAll", { n: rows.length }), onclick: () => { feedAll = true; render(); } }));
      }
      return section("feed", t("feedTitle"), t("feedAside"), h("div", {}, filters, card));
    }

    // ------------------------------------------------------------------ positions & weeks
    function positions() {
      const rows = [];
      for (const arm of ARMS) {
        for (const p of book(arm).positions || []) {
          const shares = Number(p.shares), wac = Number(p.wac);
          rows.push(h("tr", {},
            h("td", {}, h("div", { class: "wk-arm" }, key(arm), h("b", { text: t("arms." + arm)[0] }))),
            h("td", { class: "num" }, h("b", { text: p.symbol })),
            h("td", { class: "num", text: isNum(shares) ? shares.toLocaleString("en-US", { maximumFractionDigits: 4 }) : "—" }),
            h("td", { class: "num", text: money(wac) }),
            h("td", { class: "num", text: isNum(shares) && isNum(wac) ? money(shares * wac) : "—" }),
            h("td", { class: "num", text: isNum(p.holding_days) ? t("days", { n: p.holding_days.toFixed(1) }) : "—" })));
        }
      }
      const body = rows.length
        ? h("div", { class: "wk-card wk-scroll" }, h("table", { class: "wk-tbl" }, h("thead", {}, h("tr", {}, t("posCols").map((c) => h("th", { text: c })))), h("tbody", {}, rows)))
        : h("div", { class: "wk-card" }, h("p", { class: "wk-empty", text: t("posEmpty") }));
      return section("positions", t("posTitle"), t("posAside"), body);
    }

    function weekTable() {
      const ws = weeks().reverse();
      const body = ws.length
        ? h("div", { class: "wk-card wk-scroll" }, h("table", { class: "wk-tbl" },
          h("thead", {}, h("tr", {}, h("th", { text: t("weekCol") }), ARMS.map((a) => h("th", { text: t("short." + a) })), h("th", { text: t("statusCol") }))),
          h("tbody", {}, ws.map((w) => h("tr", {},
            h("td", { class: "num", text: `${dayShort(w.week)} → ${dayShort(w.end)}` }),
            ARMS.map((a) => { const v = ret(w, a); return h("td", { class: "num " + tone(v), text: pct(v) }); }),
            h("td", { class: "txt muted", text: t("weekStatus." + w.status) || w.status }))))))
        : h("div", { class: "wk-card" }, h("p", { class: "wk-empty", text: t("weeksEmpty") }));
      return section("weeks", t("weeksTitle"), t("weeksAside"), body);
    }

    // ------------------------------------------------------------------ comparisons
    function label(name) {
      const [a, b] = name.split("-minus-");
      if (b === "exposure-matched-spy") return t("sameExposure", { a: t("short." + a) || a });
      return t("versus", { a: t("short." + a) || a, b: t("short." + b) || b });
    }
    function duel() {
      const cmp = (data && data.comparisons) || {};
      const cards = ["codex", "claude"].map((arm) => {
        const c = cmp[`${mode}:${arm}-minus-random`] || { n: 0 };
        return h("div", { class: "wk-card" },
          h("h3", {}, key(arm), t("versus", { a: t("short." + arm), b: t("short.random") })),
          h("div", { class: "wk-big num " + tone(c.mean), text: c.n ? pct(c.mean) : "—" }),
          h("p", { class: "wk-sub", text: c.n ? t("duelSub", { n: c.n }) + (isNum(c.standard_error) ? " · " + t("duelSe", { se: pct(c.standard_error) }) : "") : t("duelEmpty", { d: day(schedule().next) }) }));
      });
      const primary = new Set([`${mode}:codex-minus-random`, `${mode}:claude-minus-random`]);
      const rest = Object.entries(cmp).filter(([k]) => k.startsWith(mode + ":") && !primary.has(k));
      const details = h("details", { class: "wk-details" }, h("summary", { text: t("allComparisons") }),
        h("div", { class: "wk-card wk-scroll" }, h("table", { class: "wk-tbl" },
          h("thead", {}, h("tr", {}, t("cmpCols").map((c) => h("th", { text: c })))),
          h("tbody", {}, rest.map(([k, v]) => h("tr", {},
            h("td", { class: "txt", text: label(k.split(":")[1]) }),
            h("td", { class: "num", text: String(v.n ?? 0) }),
            h("td", { class: "num " + tone(v.mean), text: pct(v.mean, 3) }),
            h("td", { class: "num", text: pct(v.standard_error, 3) })))))));
      return section("duel", t("duelTitle"), t("duelAside"), h("div", {}, h("div", { class: "wk-duel" }, cards), details));
    }

    // ------------------------------------------------------------------ data quality & notes
    function quality() {
      const cov = (data && data.coverage) || {};
      const covList = h("ul", { class: "wk-cov" }, ARMS.map((arm) => {
        const c = cov[arm] || {};
        const s = c.scheduled_weeks || 0, v = c.valid_decisions || 0;
        return h("li", {}, h("span", { class: "wk-arm" }, key(arm), t("short." + arm)), h("span", { class: "num mono", text: t("coverageVal", { v, s }) }),
          h("span", { class: "wk-bar-mini" }, h("i", { style: `width:${s ? (v / s) * 100 : 0}%` })));
      }));
      const issues = [...((data && data.missing) || []), ...((data && data.delays) || [])]
        .filter((m) => !m.book || m.book === "all-active" || String(m.book).startsWith(mode + ":"))
        .sort((a, b) => String(b.date || b.week || "").localeCompare(String(a.date || a.week || "")));
      const issueList = issues.length
        ? h("ul", { class: "wk-issues" }, issues.slice(0, 10).map((m) => {
          const arm = String(m.book || "").split(":")[1];
          const who = [arm && t("short." + arm), m.symbol && m.symbol !== "*" ? m.symbol : null].filter(Boolean).join(" · ");
          return h("li", {}, h("span", { class: "d", text: dayShort(m.date || m.week) }), h("span", {}, who ? h("b", { text: who + " — " }) : null, reason(m.reason)));
        }))
        : h("p", { class: "wk-empty", text: t("noIssues") });
      return section("quality", t("qualityTitle"), t("qualityAside"), h("div", { class: "wk-two" },
        h("div", { class: "wk-card" }, h("div", { class: "wk-label-row", style: "padding:14px 18px 6px" }, h("span", { class: "wk-label", text: t("coverage") })), covList),
        h("div", { class: "wk-card" }, h("div", { class: "wk-label-row", style: "padding:14px 18px 6px" }, h("span", { class: "wk-label", text: t("issues") })), issueList)));
    }

    function notes() {
      return section("notes", t("notesTitle"), null, h("ol", { class: "wk-notes" }, t("notes").map((n) => h("li", { text: n }))));
    }

    function foot() {
      const hash = (data && data.config_hash) || "";
      return h("footer", { class: "wk-foot" },
        h("span", { title: hash, text: t("foot", { p: (data && data.protocol) || "weekly-hold-v1", h: hash ? hash.slice(0, 12) : "—" }) }),
        data && data.generated_at ? h("span", { text: t("updated", { t: stamp(data.generated_at) }) }) : null,
        h("a", { href: "weekly.json", text: t("data") }),
        h("a", { href: DOCS, target: "_blank", rel: "noopener noreferrer", text: t("method") }));
    }

    // ------------------------------------------------------------------ shell
    function bar() {
      return h("header", { class: "wk-bar" }, h("div", { class: "wk-bar-row" },
        h("a", { class: "wk-mark", href: "./" }, h("span", { class: "wk-logo", "aria-hidden": "true" }), "TradeAgent"),
        h("span", { class: "wk-bar-title", text: t("page") }),
        h("div", { class: "wk-bar-tools" }, h("button", { type: "button", class: "wk-pill", text: t("lang"), onclick: () => {
          lang = lang === "zh" ? "en" : "zh";
          storage.set("ta.lang", lang);
          storage.set("ta.site.lang", lang);
          render();
        } }))));
    }

    const clockHost = h("div");
    function render() {
      observers.splice(0).forEach((o) => o.disconnect());
      if (!embedded) {
        document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
        document.title = "TradeAgent · " + t("page");
      }
      const wrap = h("div", { class: "wk-wrap" });
      clockHost.replaceChildren(clock());
      wrap.append(hero());
      if (failed) wrap.append(h("div", { class: "wk-card", style: "margin-top:28px" }, h("p", { class: "wk-empty", text: t("failed") })));
      else if (!data) wrap.append(h("div", { class: "wk-card", style: "margin-top:28px" }, h("p", { class: "wk-empty", text: t("loading") })));
      else wrap.append(kpis(), board(), chart(), pipeline(), feed(), positions(), weekTable(), duel(), quality(), notes(), foot());
      root.replaceChildren(...(embedded ? [] : [bar()]), wrap);
    }

    window.addEventListener("ta:lang", (ev) => {
      const next = ev && ev.detail && (ev.detail.lang || ev.detail);
      if ((next === "zh" || next === "en") && next !== lang) { lang = next; render(); }
    });
    setInterval(() => { if (data) clockHost.replaceChildren(clock()); }, 30000);
    render();
    fetch("weekly.json", { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then((d) => { data = d; render(); })
      .catch(() => { failed = true; render(); });
  }

  window.TradeAgentWeekly = { mount };
  const standalone = document.getElementById("wk-app");
  if (standalone) mount(standalone, { embedded: false });
})();
