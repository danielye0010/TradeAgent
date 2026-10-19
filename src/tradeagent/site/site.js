/* TradeAgent public results page: renders the inlined paper-research snapshot. No dependencies. */
(() => {
  "use strict";

  const data = JSON.parse(document.getElementById("data").textContent);
  const MIN_DAYS = 20;
  const COPY = {
    en: {
      live: "Public track record",
      lang: "中文",
      eyebrow: "Open-source AI trading agent",
      title: ["Your own AI trading agent, with a ", "public track record", "."],
      lede: "Every trading morning it reads the news, picks one US stock or stays in cash, and can trade it for you on Robinhood with $1–$5 under hard limits. Every pick is frozen before the market moves and published here after the close, wins and losses alike.",
      ctaRepo: "Run your own agent",
      ctaRecord: "See every pick",
      agent: "The agent",
      gpt: "GPT",
      claude: "Claude",
      spy: "S&P 500 (SPY)",
      random: "Random pick",
      sessions: "Trading days",
      agentSub: "GPT reading the news, {n} days added up",
      spySub: "holding SPY for the same hours",
      randomSub: "a random pick from the same three",
      sessionsSub: "every day published, since {d}",
      chartTitle: "The agent against the market",
      chartAside: "Each day's return after costs, added up",
      chartEarly: "Early days: a handful of sessions is mostly luck. A verdict comes after {need} days.",
      trustTitle: "Why you can trust the record",
      trust: [
        ["Frozen first", "Each pick is locked in before the market moves; nothing is picked or edited after the fact."],
        ["Same shortlist, real controls", "Every day the agent is scored against SPY, a random pick and a quant signal on the very same three stocks."],
        ["Costs included", "Returns come from market prices after spread and slippage, not from the best price of the hour."],
        ["No early victory laps", "No verdict before {need} trading days, and only when the gap is bigger than day-to-day noise."],
      ],
      picksTitle: "Latest picks",
      cash: "Cash",
      cashLine: "Stayed in cash. Top-ranked idea:",
      noAnswer: "No answer this day",
      pending: "scored after the hour",
      stance: { long: "long", watch: "watch", avoid: "avoid" },
      vsTitle: "Which AI should run your agent?",
      vsLede: "The agent can think with GPT (through Codex) or with Claude. Both get the same three stocks and the same question every day; you choose whose pick it trades.",
      vsEarly: "Claude joined on {d}; the comparison fills in from there.",
      better: "{who} did better",
      same: "picked the same stock",
      ofDays: "of {n} days",
      verdict: {
        collecting: "{d} of {n} days in. Too early to call a winner; check back.",
        ahead: "Claude is ahead of GPT, and the gap is larger than day-to-day noise.",
        behind: "GPT is ahead of Claude, and the gap is larger than day-to-day noise.",
        unclear: "No clear winner yet: the gap is still within day-to-day noise.",
      },
      logTitle: "Every pick",
      logAside: "Newest first",
      date: "Day",
      showAll: "Show all {n} days",
      howTitle: "How it works",
      how: [
        ["Scan", "At 10:30 ET a quant scan finds the three US stocks or ETFs moving most unusually that morning."],
        ["Decide", "The AI reads the news on those three and picks one or stays in cash, with its reasons. It runs on your ChatGPT or Claude subscription."],
        ["Trade within your limits", "A deterministic engine, not the AI, places the order: your symbols, your $1–$5, one trade a day, sold after an hour."],
      ],
      runTitle: "Run your own agent",
      runBody: "Free and open source. No API keys: it uses the ChatGPT or Claude subscription you already have. Start on paper with a local dashboard, and turn on small live trades on Robinhood when you're ready.",
      runBtn: "Get it on GitHub",
      empty: "The first results appear here after the first trading day.",
      foot1: "Paper results: picks are frozen before the market moves and scored at market prices after spread and slippage; they are not live fills. Not investment advice.",
      foot2: "Updated {t}.",
      source: "Source code on GitHub",
    },
    zh: {
      live: "公开战绩",
      lang: "EN",
      eyebrow: "开源 AI 交易代理",
      title: ["属于你自己的 AI 交易代理，", "每一笔都公开", "。"],
      lede: "每个交易日早上，它读新闻、选出一只美股或选择空仓，还可以在 Robinhood 上按严格限额替你交易 1–5 美元。每次选择都在行情变化前锁定，收盘后公开在这里，赚的亏的都在。",
      ctaRepo: "运行你自己的代理",
      ctaRecord: "查看每一次选择",
      agent: "交易代理",
      gpt: "GPT",
      claude: "Claude",
      spy: "标普 500（SPY）",
      random: "随机选股",
      sessions: "交易日",
      agentSub: "GPT 读新闻选股，{n} 个交易日累计",
      spySub: "同样时段持有 SPY",
      randomSub: "从同样三只里随机选一只",
      sessionsSub: "每天公开，自 {d} 起",
      chartTitle: "交易代理 vs 大盘",
      chartAside: "每天扣除成本后的收益，逐日累加",
      chartEarly: "样本还少：几天的差距多半是运气，满 {need} 个交易日后才有结论。",
      trustTitle: "为什么这份战绩可信",
      trust: [
        ["先锁定，后揭晓", "每次选择都在行情变化前锁定，事后不会补选或修改。"],
        ["同一批候选，真对照", "每天都在同样的三只股票上，和 SPY、随机选股、量化信号一起计分。"],
        ["成本算在内", "收益按真实行情价格、扣除价差和滑点计算，而不是挑一小时内的最好价格。"],
        ["不急着庆功", "不满 {need} 个交易日不下结论，差距要超过日常波动才算数。"],
      ],
      picksTitle: "最新选择",
      cash: "空仓",
      cashLine: "选择空仓。排名第一的想法：",
      noAnswer: "当天没有给出答案",
      pending: "一小时后计分",
      stance: { long: "看多", watch: "观望", avoid: "回避" },
      vsTitle: "你的代理该用哪个 AI？",
      vsLede: "代理可以用 GPT（通过 Codex）思考，也可以用 Claude。两者每天拿到同样的三只股票、同一个问题；跟谁的选择下单由你决定。",
      vsEarly: "Claude 自 {d} 起加入，对比从那天开始累积。",
      better: "{who} 更好",
      same: "选了同一只",
      ofDays: "共 {n} 天",
      verdict: {
        collecting: "已有 {d}/{n} 个交易日，现在下结论还太早，过段时间再来看。",
        ahead: "Claude 领先 GPT，差距已超过日常波动。",
        behind: "GPT 领先 Claude，差距已超过日常波动。",
        unclear: "暂时没有明显赢家：差距仍在日常波动范围内。",
      },
      logTitle: "每一次选择",
      logAside: "最新的在前",
      date: "交易日",
      showAll: "显示全部 {n} 天",
      howTitle: "怎么运作",
      how: [
        ["扫描", "美东 10:30，量化扫描找出当天早上走势最异常的三只美股或 ETF。"],
        ["决策", "AI 读这三只的新闻，选一只或空仓，并给出理由。它用你自己的 ChatGPT 或 Claude 订阅运行。"],
        ["在限额内交易", "下单的是确定性的执行引擎，不是 AI：只买你允许的标的、每笔 1–5 美元、每天一笔、一小时后卖出。"],
      ],
      runTitle: "运行你自己的代理",
      runBody: "免费开源。不需要 API key，直接用你已有的 ChatGPT 或 Claude 订阅。先在模拟盘和本地仪表盘上观察，准备好了再开启 Robinhood 小额实盘。",
      runBtn: "前往 GitHub",
      empty: "第一个交易日结束后，结果会显示在这里。",
      foot1: "模拟盘结果：选择在行情变化前锁定，按真实行情价格扣除价差和滑点计分，不是实盘成交。不构成投资建议。",
      foot2: "更新于 {t}。",
      source: "GitHub 源代码",
    },
  };

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked */ } },
  };
  let lang = store.get("ta.site.lang") || ((navigator.language || "en").toLowerCase().startsWith("zh") ? "zh" : "en");
  let showAll = false;

  function t(path, vars) {
    const pick = (table) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), table);
    let v = pick(COPY[lang]);
    if (v == null) v = pick(COPY.en);
    if (typeof v !== "string") return v;
    return v.replace(/\{(\w+)\}/g, (_, k) => (vars && vars[k] != null ? String(vars[k]) : ""));
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

  const pct = (v, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(d)}%`);
  const tone = (v) => (v == null || Math.abs(v) < 1e-12 ? "" : v > 0 ? "up" : "down");
  const day = (iso, long) => {
    const d = new Date(iso + "T12:00:00Z");
    return d.toLocaleDateString(lang === "zh" ? "zh-CN" : "en-US", long ? { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" } : { month: "short", day: "numeric", timeZone: "UTC" });
  };
  const color = { codex: "var(--gpt)", claude: "var(--claude)", spy: "var(--spy)", random: "var(--random)" };

  const scored = data.sessions.filter((x) => x.scored);
  const sum = (key, rows) => rows.reduce((a, x) => a + (key === "spy" ? x.spy || 0 : x[key]?.net || 0), 0);
  const rowsWith = (key) => scored.filter((x) => (key === "spy" ? x.spy != null : x[key] && x[key].net != null));

  // ------------------------------------------------------------ sections

  function hero() {
    const [a, mark, b] = t("title");
    return h("section", { class: "hero" },
      h("p", { class: "eyebrow", text: t("eyebrow") }),
      h("h1", {}, a, h("span", { class: "mark-up", text: mark }), b),
      h("p", { class: "lede", text: t("lede") }),
      h("div", { class: "cta" },
        h("a", { class: "btn primary", href: data.repo, text: t("ctaRepo") }),
        h("a", { class: "btn ghost", href: "#log", text: t("ctaRecord") })),
      board());
  }

  function cell(key, label, value, sub) {
    return h("div", { class: "cell" },
      h("div", { class: "who" }, key ? h("i", { class: "key", style: `background:${color[key]}` }) : null, label),
      h("div", { class: "big num " + tone(value), text: typeof value === "number" ? pct(value) : value }),
      h("div", { class: "sub", text: sub }));
  }

  function board() {
    if (!scored.length) return h("p", { class: "lede", text: t("empty") });
    const g = rowsWith("codex");
    return h("div", { class: "board" },
      cell("codex", t("agent"), sum("codex", g), t("agentSub", { n: g.length })),
      cell("spy", t("spy"), sum("spy", rowsWith("spy")), t("spySub")),
      cell("random", t("random"), sum("random", rowsWith("random")), t("randomSub")),
      cell(null, t("sessions"), String(scored.length), t("sessionsSub", { d: day(data.first_session) })));
  }

  function trust() {
    return h("section", { class: "block" },
      h("div", { class: "head" }, h("h2", { text: t("trustTitle") })),
      h("div", { class: "trust" }, ...t("trust").map(([title, body]) =>
        h("div", { class: "point" }, h("h3", { text: title }), h("p", { text: body.replace("{need}", MIN_DAYS) })))));
  }

  function chart() {
    if (scored.length < 2) return null;
    return h("section", { class: "block" },
      h("div", { class: "head" }, h("h2", { text: t("chartTitle") }), h("span", { class: "aside", text: t("chartAside") })),
      h("div", { class: "chart", id: "chart" },
        h("div", { class: "legend" }, ...["codex", "claude", "spy", "random"]
          .filter((k) => k !== "claude" || rowsWith("claude").length)
          .map((k) => h("span", {}, h("i", { style: `background:${color[k]}` }), label(k)))),
        h("div", { class: "plot" })),
      scored.length < MIN_DAYS ? h("p", { class: "note", text: t("chartEarly", { need: MIN_DAYS }) }) : null);
  }

  const label = (k) => ({ codex: `${t("agent")} (GPT)`, claude: t("claude"), spy: "SPY", random: t("random") })[k];

  function drawChart() {
    const box = document.querySelector("#chart .plot");
    if (!box) return;
    const n = scored.length;
    const keys = ["random", "spy", "codex", "claude"];
    const lines = {};
    for (const k of keys) {
      let acc = 0, started = false;
      lines[k] = scored.map((x) => {
        const v = k === "spy" ? x.spy : x[k]?.net;
        if (v == null) return started ? acc : null;
        started = true;
        acc += v;
        return acc;
      });
    }
    if (!lines.claude.some((v) => v != null)) delete lines.claude;
    const W = Math.max(320, box.clientWidth || 800), H = W < 560 ? 240 : 320;
    const narrow = W < 560;
    const pad = { l: 46, r: narrow ? 12 : 136, t: 12, b: 28 };
    const all = Object.values(lines).flat().filter((v) => v != null).concat([0]);
    let lo = Math.min(...all), hi = Math.max(...all);
    const m = (hi - lo) * 0.12 || 0.002; lo -= m; hi += m;
    const x = (i) => pad.l + (n === 1 ? 0 : (i / (n - 1)) * (W - pad.l - pad.r));
    const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("chartTitle") });
    const step = niceStep(hi - lo);
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      const r = Math.abs(v) < 1e-12 ? 0 : v;
      svg.append(s("line", { class: r === 0 ? "zero" : "grid", x1: pad.l, x2: W - pad.r, y1: y(r), y2: y(r) }));
      svg.append(s("text", { class: "axis", x: pad.l - 8, y: y(r) + 4, "text-anchor": "end" }, pct(r, 1)));
    }
    const marks = W < 560 ? [0, n - 1] : [0, Math.floor((n - 1) / 2), n - 1];
    for (const i of new Set(marks)) svg.append(s("text", { class: "axis", x: x(i), y: H - 8, "text-anchor": i === 0 ? "start" : i === n - 1 ? "end" : "middle" }, day(scored[i].date)));
    const ends = [];
    for (const k of keys) {
      const pts = lines[k];
      if (!pts) continue;
      let d = "", first = true;
      pts.forEach((v, i) => { if (v == null) return; d += `${first ? "M" : "L"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`; first = false; });
      svg.append(s("path", { class: "series" + (k === "spy" || k === "random" ? " thin" : ""), d, stroke: color[k] }));
      const last = pts[n - 1];
      if (last != null && (k === "codex" || k === "claude")) ends.push({ k, y: y(last), v: last });
    }
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 16) ends[i].y = ends[i - 1].y + 16;
    if (!narrow) for (const e of ends) svg.append(s("text", { class: "end", x: W - pad.r + 8, y: e.y + 4, fill: color[e.k] }, `${label(e.k)} ${pct(e.v, 1)}`));
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
        ...keys.slice().reverse().filter((k) => lines[k] && lines[k][i] != null).map((k) =>
          h("div", { class: "row" }, h("span", {}, h("i", { class: "key", style: `background:${color[k]}` }), label(k)), h("b", { class: "num " + tone(lines[k][i]), text: pct(lines[k][i]) }))));
      tip.hidden = false;
      tip.style.left = Math.min((x(i) / W) * r.width + 14, r.width - 190) + "px";
      tip.style.top = "36px";
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

  function pickCard(who, session, note) {
    const pick = session[who];
    if (!pick) return null;
    const model = data.models[who];
    const head = h("div", { class: "who" }, h("i", { class: "key", style: `background:${color[who]}` }), label(who), model ? h("span", { class: "model", text: model }) : null);
    if (!pick.answered) return h("div", { class: "card" }, head, h("p", { class: "muted", style: "margin-top:14px", text: t("noAnswer") }));
    const sym = h("div", { class: "sym" }, h("b", { text: pick.symbol || t("cash") }),
      pick.symbol ? (session.scored ? h("span", { class: "res num " + tone(pick.net), text: pct(pick.net) }) : h("span", { class: "muted", text: t("pending") })) : null);
    const body = [];
    if (note) {
      if (!pick.symbol) body.push(h("p", { class: "muted", style: "margin-top:12px;font-size:14px" }, t("cashLine"), " ", h("b", { text: note.symbol }), " ", note.stance ? h("span", { class: "stance " + note.stance, text: t("stance." + note.stance) }) : null));
      if (note.thesis) body.push(h("blockquote", { text: note.thesis }));
      if (note.sources?.length) body.push(h("div", { class: "src" }, ...note.sources.filter((x) => /^https:\/\//.test(x.url || "")).map((x) => h("a", { href: x.url, target: "_blank", rel: "noopener noreferrer", text: "↗ " + (x.title || x.url) }))));
    }
    return h("div", { class: "card" }, head, sym, ...body);
  }

  function picks() {
    const last = data.sessions[data.sessions.length - 1];
    if (!last) return null;
    const cards = [pickCard("codex", last, data.latest.codex), pickCard("claude", last, data.latest.claude)].filter(Boolean);
    return h("section", { class: "block" },
      h("div", { class: "head" }, h("h2", { text: t("picksTitle") }), h("span", { class: "aside", text: day(last.date, true) })),
      h("div", { class: "picks" }, ...cards));
  }

  function headToHead() {
    const d = data.duel;
    const head = [h("div", { class: "head" }, h("h2", { text: t("vsTitle") })), h("p", { class: "section-lede", text: t("vsLede") })];
    if (!d || !d.scored_days) {
      const since = data.claude_since || (data.models.claude ? data.latest.date : null);
      return h("section", { class: "block" }, ...head, since ? h("p", { class: "verdict", text: t("vsEarly", { d: day(since, true) }) }) : null);
    }
    const v = d.verdict || {};
    const text = v.state === "collecting" ? t("verdict.collecting", { d: v.days, n: v.needed || MIN_DAYS }) : t("verdict." + v.state);
    const stat = (big, label, key) => h("div", { class: "stat" }, h("div", { class: "big num", style: key ? `color:${color[key]}` : null, text: big }), h("div", { class: "label", text: label }));
    return h("section", { class: "block" }, ...head,
      h("div", { class: "vs" },
        stat(String(d.wins.claude || 0), `${t("better", { who: t("claude") })} · ${t("ofDays", { n: d.scored_days })}`, "claude"),
        stat(String(d.wins.codex || 0), `${t("better", { who: t("gpt") })} · ${t("ofDays", { n: d.scored_days })}`, "codex"),
        stat(String(d.same_pick || 0), `${t("same")} · ${t("ofDays", { n: d.answered_both || 0 })}`, null)),
      h("p", { class: "verdict", text: text }));
  }

  function pickCell(p, scoredDay) {
    if (!p) return h("td", { class: "muted", text: "—" });
    if (!p.answered) return h("td", { class: "muted", text: "—" });
    return h("td", { class: "pickcell" }, h("b", { text: p.symbol || t("cash") }), " ",
      p.symbol ? h("span", { class: "num " + (scoredDay ? tone(p.net) : "muted"), text: scoredDay ? pct(p.net) : t("pending") }) : null);
  }

  function log() {
    const rows = data.sessions.slice().reverse();
    if (!rows.length) return null;
    const shown = showAll ? rows : rows.slice(0, 12);
    const withClaude = data.sessions.some((x) => x.claude);
    const table = h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: t("date") }), h("th", { text: t("gpt") }), withClaude ? h("th", { text: t("claude") }) : null, h("th", { class: "n hide-s", text: "SPY" }))),
      h("tbody", {}, ...shown.map((x) => {
        const a = x.codex, b = x.claude;
        const win = x.scored && a?.net != null && b?.net != null && Math.abs(a.net - b.net) > 1e-12 ? (b.net > a.net ? "claude" : "codex") : null;
        const ca = pickCell(a, x.scored), cb = withClaude ? pickCell(b, x.scored) : null;
        if (win === "codex") { ca.classList.add("win"); ca.style.setProperty("--w", color.codex); }
        if (win === "claude" && cb) { cb.classList.add("win"); cb.style.setProperty("--w", color.claude); }
        return h("tr", {}, h("td", { text: day(x.date) }), ca, cb, h("td", { class: "n num hide-s " + tone(x.spy), text: x.scored ? pct(x.spy) : "" }));
      })));
    return h("section", { class: "block", id: "log" },
      h("div", { class: "head" }, h("h2", { text: t("logTitle") }), h("span", { class: "aside", text: t("logAside") })),
      h("div", { class: "table" }, table),
      rows.length > 12 && !showAll ? h("div", { class: "more" }, h("button", { type: "button", class: "pill", text: t("showAll", { n: rows.length }), onclick: () => { showAll = true; render(); } })) : null);
  }

  function how() {
    return h("section", { class: "block" },
      h("div", { class: "head" }, h("h2", { text: t("howTitle") })),
      h("div", { class: "steps" }, ...t("how").map(([title, body]) => h("div", { class: "step" }, h("h3", { text: title }), h("p", { text: body })))),
      h("div", { class: "run" },
        h("div", {}, h("h3", { text: t("runTitle") }), h("p", { text: t("runBody") }), h("a", { class: "btn", href: data.repo, text: t("runBtn") + " →" })),
        h("pre", { text: `git clone ${data.repo}\ncd TradeAgent && python3 -m venv .venv\nsource .venv/bin/activate && pip install .\ntradeagent doctor\ntradeagent dashboard` })));
  }

  function render() {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.getElementById("live-label").textContent = t("live");
    document.getElementById("lang").textContent = t("lang");
    document.getElementById("gh").href = data.repo;
    document.getElementById("top").replaceChildren(...[hero(), chart(), trust(), picks(), headToHead(), log(), how()].filter(Boolean));
    const updated = new Date(data.generated_at * 1000).toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { dateStyle: "medium", timeStyle: "short" });
    document.getElementById("foot").replaceChildren(h("p", { text: t("foot1") }), h("p", {}, t("foot2", { t: updated }), " ", h("a", { href: data.repo, text: t("source") })));
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
