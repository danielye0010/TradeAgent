/* TradeAgent public page: the live record and why to use it. Renders the inlined snapshot. No dependencies. */
(() => {
  "use strict";

  const data = JSON.parse(document.getElementById("data").textContent);
  const MIN_DAYS = 20;
  const COPY = {
    en: {
      live: "Live record",
      lang: "中文",
      eyebrow: "Open source · runs on your ChatGPT or Claude plan",
      title: ["The AI trading agent", "that has to prove it."],
      lede: "It reads the news, picks one stock a day and trades it for you on Robinhood, $1 to $5, under hard limits. Every pick is locked before the market moves and published here. Wins and losses.",
      ctaRun: "Run your own agent",
      ctaRecord: "See the record",
      agent: "The agent",
      claude: "Claude",
      spy: "S&P 500",
      random: "Random pick",
      days: "Days on record",
      agentSub: "GPT reading the news",
      spySub: "same hours",
      randomSub: "same three stocks",
      daysSub: "since {d}, every one published",
      arenaTitle: "The live arena",
      arenaAside: "Same three stocks. Same minute. Same hour. After costs.",
      early: "Day {n} of {need}. Early numbers are mostly luck; the verdict waits for {need} days.",
      duelLede: "Both AIs get the same stocks and the same question every day. You pick which one runs your agent.",
      better: "days {who} won",
      same: "days they agreed",
      soon: "Claude is now running next to GPT. Its first picks land here after the next trading day.",
      since: "Claude joined {d}. The head-to-head fills in from here.",
      verdict: {
        collecting: "{d} of {n} days. Too early to crown a winner.",
        ahead: "Claude leads, by more than day-to-day noise.",
        behind: "GPT leads, by more than day-to-day noise.",
        unclear: "Neck and neck: the gap is still within day-to-day noise.",
      },
      whyTitle: "Not another AI arena.",
      whyLede: "Arenas let you watch AIs trade someone else's money. TradeAgent trades yours, inside your limits, and keeps a public record like an arena does.",
      cols: ["TradeAgent", "AI trading arenas", "Signal bots & copy trading", "Asking a chatbot"],
      rows: [
        ["Trades your own account", ["yes", "$1–$5 on Robinhood"], ["no", "you watch"], ["yes", ""], ["no", "you trade by hand"]],
        ["Every pick locked first and published, losses too", ["yes", ""], ["yes", ""], ["no", "mostly backtests"], ["no", ""]],
        ["Scored against SPY and a random pick", ["yes", "every day"], ["rare", ""], ["rare", ""], ["no", ""]],
        ["Choose GPT or Claude", ["yes", "compare them live"], ["na", "you can't run it"], ["no", ""], ["yes", ""]],
        ["No API keys, no extra fees", ["yes", "your existing plan"], ["na", ""], ["no", "subscriptions"], ["yes", ""]],
        ["Open source, on your computer", ["yes", ""], ["no", ""], ["no", ""], ["no", ""]],
      ],
      mark: { yes: "Yes", no: "No", rare: "Rarely", na: "—" },
      rowsNote: "What is typical of each kind of product, not a claim about any single one.",
      picksTitle: "Today's call",
      cash: "Cash",
      cashLine: "Sat out. Top idea was",
      noAnswer: "No answer this day",
      pending: "scored after the hour",
      stance: { long: "long", watch: "watch", avoid: "avoid" },
      logTitle: "Every pick, on the record",
      date: "Day",
      showAll: "Show all {n} days",
      howTitle: "Three steps, every trading day",
      how: [
        ["10:30 · Scan", "A quant scan finds the three US stocks or ETFs moving most unusually."],
        ["10:31 · Decide", "The AI reads their news and picks one, or cash, with its reasons. Locked."],
        ["10:36 · Trade", "The engine, not the AI, buys your $1–$5 within your limits and sells an hour later."],
      ],
      runTitle: "Your agent. Your account. Your AI.",
      runBody: "Free and open source. Start on paper with a local dashboard; go live when you're ready.",
      runBtn: "Get it on GitHub",
      empty: "The record starts after the first trading day.",
      foot1: "Paper results: picks are locked before the market moves and scored at market prices after spread and slippage; not live fills. Not investment advice.",
      foot2: "Updated {t}.",
      source: "Source on GitHub",
    },
    zh: {
      live: "实时战绩",
      lang: "EN",
      eyebrow: "开源 · 用你的 ChatGPT 或 Claude 订阅运行",
      title: ["一个必须", "用战绩说话的 AI 交易代理。"],
      lede: "它每天读新闻、选一只股票，在 Robinhood 上替你交易，每笔 1–5 美元，严格限额。每次选择在行情变化前锁定、在这里公开。赚的亏的都在。",
      ctaRun: "运行你自己的代理",
      ctaRecord: "查看战绩",
      agent: "交易代理",
      claude: "Claude",
      spy: "标普 500",
      random: "随机选股",
      days: "公开天数",
      agentSub: "GPT 读新闻选股",
      spySub: "同样时段",
      randomSub: "同样三只股票",
      daysSub: "自 {d} 起，每天都公开",
      arenaTitle: "实时擂台",
      arenaAside: "同样三只股票，同一分钟，同一个小时，扣除成本。",
      early: "第 {n}/{need} 天。早期数字多半是运气，满 {need} 天才下结论。",
      duelLede: "两个 AI 每天拿到同样的股票、同一个问题。你的代理用哪个，由你决定。",
      better: "{who} 赢的天数",
      same: "两者选得一样的天数",
      soon: "Claude 已在 GPT 旁边运行，下一个交易日后，它的第一批选择会出现在这里。",
      since: "Claude 自 {d} 起加入，正面对比从这里开始累积。",
      verdict: {
        collecting: "已有 {d}/{n} 天，现在封王还太早。",
        ahead: "Claude 领先，差距已超过日常波动。",
        behind: "GPT 领先，差距已超过日常波动。",
        unclear: "难分高下：差距仍在日常波动范围内。",
      },
      whyTitle: "不是又一个 AI 擂台。",
      whyLede: "擂台让你看 AI 拿别人的钱交易。TradeAgent 在你的限额内交易你自己的账户，同时像擂台一样公开每一笔。",
      cols: ["TradeAgent", "AI 交易擂台", "信号机器人 / 跟单", "自己问聊天机器人"],
      rows: [
        ["交易你自己的账户", ["yes", "Robinhood 每笔 1–5 美元"], ["no", "只能围观"], ["yes", ""], ["no", "要自己手动下单"]],
        ["每次选择先锁定再公开，亏损也公开", ["yes", ""], ["yes", ""], ["no", "多是回测"], ["no", ""]],
        ["和 SPY、随机选股对比计分", ["yes", "每天"], ["rare", ""], ["rare", ""], ["no", ""]],
        ["可选 GPT 或 Claude", ["yes", "实时对比"], ["na", "无法自己运行"], ["no", ""], ["yes", ""]],
        ["不需要 API key，没有额外费用", ["yes", "用你现有的订阅"], ["na", ""], ["no", "订阅收费"], ["yes", ""]],
        ["开源，运行在你自己的电脑上", ["yes", ""], ["no", ""], ["no", ""], ["no", ""]],
      ],
      mark: { yes: "是", no: "否", rare: "很少", na: "—" },
      rowsNote: "按每类产品的典型情况，不针对任何单一产品。",
      picksTitle: "今天的选择",
      cash: "空仓",
      cashLine: "选择观望。排名第一的是",
      noAnswer: "当天没有给出答案",
      pending: "一小时后计分",
      stance: { long: "看多", watch: "观望", avoid: "回避" },
      logTitle: "每一次选择，全部公开",
      date: "交易日",
      showAll: "显示全部 {n} 天",
      howTitle: "每个交易日，三步",
      how: [
        ["10:30 · 扫描", "量化扫描找出走势最异常的三只美股或 ETF。"],
        ["10:31 · 决策", "AI 读它们的新闻，选一只或空仓，并给出理由。立即锁定。"],
        ["10:36 · 交易", "下单的是执行引擎，不是 AI：在你的限额内买入 1–5 美元，一小时后卖出。"],
      ],
      runTitle: "你的代理，你的账户，你选的 AI。",
      runBody: "免费开源。先在模拟盘和本地仪表盘上观察，准备好了再开启实盘。",
      runBtn: "前往 GitHub",
      empty: "第一个交易日结束后，战绩从这里开始。",
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
  const label = (k) => ({ codex: `${t("agent")} · GPT`, claude: t("claude"), spy: "SPY", random: t("random") })[k];

  const scored = data.sessions.filter((x) => x.scored);
  const sum = (key, rows) => rows.reduce((a, x) => a + (key === "spy" ? x.spy || 0 : x[key]?.net || 0), 0);
  const rowsWith = (key) => scored.filter((x) => (key === "spy" ? x.spy != null : x[key] && x[key].net != null));

  // ------------------------------------------------------------ hero

  function hero() {
    const [a, b] = t("title");
    return h("section", { class: "hero" },
      h("p", { class: "eyebrow", text: t("eyebrow") }),
      h("h1", {}, h("span", { text: a }), " ", h("span", { class: "accent", text: b })),
      h("p", { class: "lede", text: t("lede") }),
      h("div", { class: "cta" },
        h("a", { class: "btn primary", href: data.repo, text: t("ctaRun") + " →" }),
        h("a", { class: "btn ghost", href: "#arena", text: t("ctaRecord") })),
      strip());
  }

  function stat(key, name, value, sub, lead) {
    return h("div", { class: "cell" + (lead ? " lead" : "") },
      h("div", { class: "who" }, key ? h("i", { class: "key", style: `background:${color[key]}` }) : null, name),
      h("div", { class: "big num " + (typeof value === "number" ? tone(value) : ""), text: typeof value === "number" ? pct(value) : value }),
      h("div", { class: "sub", text: sub }));
  }

  function strip() {
    if (!scored.length) return h("p", { class: "lede", text: t("empty") });
    return h("div", { class: "board" },
      stat("codex", t("agent"), sum("codex", rowsWith("codex")), t("agentSub"), true),
      stat("spy", t("spy"), sum("spy", rowsWith("spy")), t("spySub")),
      stat("random", t("random"), sum("random", rowsWith("random")), t("randomSub")),
      stat(null, t("days"), String(scored.length), t("daysSub", { d: day(data.first_session) })));
  }

  // ------------------------------------------------------------ arena

  function arena() {
    if (!scored.length) return null;
    return h("section", { class: "block", id: "arena" },
      h("div", { class: "head" }, h("h2", { text: t("arenaTitle") }), h("span", { class: "aside", text: t("arenaAside") })),
      scored.length >= 2 ? h("div", { class: "chart", id: "chart" },
        h("div", { class: "legend" }, ...["codex", "claude", "spy", "random"]
          .filter((k) => k !== "claude" || rowsWith("claude").length)
          .map((k) => h("span", {}, h("i", { style: `background:${color[k]}` }), label(k)))),
        h("div", { class: "plot" })) : null,
      scored.length < MIN_DAYS ? h("p", { class: "note", text: t("early", { n: scored.length, need: MIN_DAYS }) }) : null,
      duel());
  }

  function duel() {
    const d = data.duel;
    const parts = [h("h3", { class: "duel-title" }, h("span", { style: `color:${color.codex}`, text: "GPT" }), " vs ", h("span", { style: `color:${color.claude}`, text: "Claude" })),
      h("p", { class: "muted", text: t("duelLede") })];
    if (!d || !d.scored_days) {
      const note = data.claude_since ? t("since", { d: day(data.claude_since, true) }) : data.models.claude ? t("soon") : null;
      return h("div", { class: "duel" }, ...parts, note ? h("p", { class: "verdict", text: note }) : null);
    }
    const v = d.verdict || {};
    const text = v.state === "collecting" ? t("verdict.collecting", { d: v.days, n: v.needed || MIN_DAYS }) : t("verdict." + v.state);
    const box = (big, name, key) => h("div", { class: "stat" }, h("div", { class: "big num", style: key ? `color:${color[key]}` : null, text: big }), h("div", { class: "label", text: name }));
    return h("div", { class: "duel" }, ...parts,
      h("div", { class: "vs" },
        box(`${d.wins.codex || 0}`, t("better", { who: "GPT" }), "codex"),
        box(`${d.wins.claude || 0}`, t("better", { who: "Claude" }), "claude"),
        box(`${d.same_pick || 0}/${d.answered_both || 0}`, t("same"), null)),
      h("p", { class: "verdict", text: text }));
  }

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
    const W = Math.max(320, box.clientWidth || 800), H = W < 560 ? 240 : 340;
    const narrow = W < 560;
    const pad = { l: 46, r: narrow ? 12 : 150, t: 12, b: 28 };
    const all = Object.values(lines).flat().filter((v) => v != null).concat([0]);
    let lo = Math.min(...all), hi = Math.max(...all);
    const m = (hi - lo) * 0.12 || 0.002; lo -= m; hi += m;
    const x = (i) => pad.l + (n === 1 ? 0 : (i / (n - 1)) * (W - pad.l - pad.r));
    const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("arenaTitle") });
    const step = niceStep(hi - lo);
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      const r = Math.abs(v) < 1e-12 ? 0 : v;
      svg.append(s("line", { class: r === 0 ? "zero" : "grid", x1: pad.l, x2: W - pad.r, y1: y(r), y2: y(r) }));
      svg.append(s("text", { class: "axis", x: pad.l - 8, y: y(r) + 4, "text-anchor": "end" }, pct(r, 1)));
    }
    const marks = narrow ? [0, n - 1] : [0, Math.floor((n - 1) / 2), n - 1];
    for (const i of new Set(marks)) svg.append(s("text", { class: "axis", x: x(i), y: H - 8, "text-anchor": i === 0 ? "start" : i === n - 1 ? "end" : "middle" }, day(scored[i].date)));
    const ends = [];
    for (const k of keys) {
      const pts = lines[k];
      if (!pts) continue;
      let d = "", first = true;
      pts.forEach((v, i) => { if (v == null) return; d += `${first ? "M" : "L"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`; first = false; });
      svg.append(s("path", { class: "series" + (k === "spy" || k === "random" ? " thin" : ""), d, stroke: color[k] }));
      const last = pts[n - 1];
      if (last != null) ends.push({ k, y: y(last), v: last });
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
      tip.style.left = Math.min((x(i) / W) * r.width + 14, r.width - 200) + "px";
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

  // ------------------------------------------------------------ why us

  function why() {
    const cols = t("cols");
    const mark = (cell) => {
      const [state, note] = cell;
      return h("td", { class: "m " + state },
        h("span", { class: "tick", text: state === "yes" ? "✓" : state === "no" ? "✕" : state === "rare" ? "~" : "—", "aria-hidden": "true" }),
        h("span", { class: "say", text: t("mark." + state) }),
        note ? h("span", { class: "why-note", text: note }) : null);
    };
    return h("section", { class: "block why" },
      h("h2", { class: "big-h", text: t("whyTitle") }),
      h("p", { class: "section-lede", text: t("whyLede") }),
      h("div", { class: "table compare" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "" }), ...cols.map((c, i) => h("th", { class: i === 0 ? "us" : "", text: c })))),
        h("tbody", {}, ...t("rows").map(([name, ...cells]) =>
          h("tr", {}, h("th", { scope: "row", text: name }), ...cells.map((c, i) => {
            const td = mark(c);
            if (i === 0) td.classList.add("us");
            return td;
          })))))),
      h("p", { class: "note", text: t("rowsNote") }));
  }

  // ------------------------------------------------------------ picks and log

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
      h("div", { class: "picks" + (cards.length === 1 ? " one" : "") }, ...cards));
  }

  function pickCell(p, scoredDay) {
    if (!p || !p.answered) return h("td", { class: "muted", text: "—" });
    return h("td", { class: "pickcell" }, h("b", { text: p.symbol || t("cash") }), " ",
      p.symbol ? h("span", { class: "num " + (scoredDay ? tone(p.net) : "muted"), text: scoredDay ? pct(p.net) : t("pending") }) : null);
  }

  function log() {
    const rows = data.sessions.slice().reverse();
    if (!rows.length) return null;
    const shown = showAll ? rows : rows.slice(0, 10);
    const withClaude = data.sessions.some((x) => x.claude);
    const table = h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: t("date") }), h("th", { text: label("codex") }), withClaude ? h("th", { text: t("claude") }) : null, h("th", { class: "n hide-s", text: "SPY" }))),
      h("tbody", {}, ...shown.map((x) => {
        const a = x.codex, b = x.claude;
        const win = x.scored && a?.net != null && b?.net != null && Math.abs(a.net - b.net) > 1e-12 ? (b.net > a.net ? "claude" : "codex") : null;
        const ca = pickCell(a, x.scored), cb = withClaude ? pickCell(b, x.scored) : null;
        if (win === "codex") { ca.classList.add("win"); ca.style.setProperty("--w", color.codex); }
        if (win === "claude" && cb) { cb.classList.add("win"); cb.style.setProperty("--w", color.claude); }
        return h("tr", {}, h("td", { text: day(x.date) }), ca, cb, h("td", { class: "n num hide-s " + tone(x.spy), text: x.scored ? pct(x.spy) : "" }));
      })));
    return h("section", { class: "block", id: "log" },
      h("div", { class: "head" }, h("h2", { text: t("logTitle") })),
      h("div", { class: "table" }, table),
      rows.length > 10 && !showAll ? h("div", { class: "more" }, h("button", { type: "button", class: "pill", text: t("showAll", { n: rows.length }), onclick: () => { showAll = true; render(); } })) : null);
  }

  // ------------------------------------------------------------ how and call to action

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
    document.getElementById("top").replaceChildren(...[hero(), arena(), why(), picks(), log(), how()].filter(Boolean));
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
