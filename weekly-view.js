/* TradeAgent weekly portfolios (weekly-hold-v1).
   One renderer for the public page (inside a shadow root), the local dashboard (/weekly)
   and `tradeagent weekly serve`. Reads weekly.json; never invents missing values. */
(() => {
  "use strict";

  const ARMS = ["codex", "claude", "quant", "random", "spy"];
  const TZ = "America/New_York";
  const REPO = "https://github.com/danielye0010/TradeAgent";

  const COPY = {
    en: {
      lang: "中文",
      page: "Weekly portfolios",
      eyebrow: "Weekly portfolios · paper trading",
      title: "Five $10,000 portfolios. One decision a week.",
      lede: "Every week GPT and Claude each read the news and pick up to three US stocks for their own $10,000 portfolio. They race a momentum model, a random pick and the S&P 500.",
      next: "Next picks",
      when: "{d} · 11:30 ET",
      deciding: "Picking now",
      soon: "Coming up",
      units: ["d", "h", "m"],
      boardTitle: "Leaderboard",
      boardCols: ["Portfolio", "Value", "Return", "Holding"],
      cash: "Cash",
      arms: {
        codex: ["TradeAgent · GPT", "AI, reads the news"],
        claude: ["TradeAgent · Claude", "AI, reads the news"],
        quant: ["Momentum", "rules, no AI"],
        random: ["Random", "three random stocks"],
        spy: ["S&P 500", "buy and hold SPY"],
      },
      short: { codex: "GPT", claude: "Claude", quant: "Momentum", random: "Random", spy: "S&P 500" },
      chartTitle: "Portfolio value",
      chartEmpty: "The chart starts after the first close on {d}.",
      picksTitle: "This week's AI picks",
      picksWeek: "Week of {d}",
      picksFirst: "First picks on {d} at 11:30 ET.",
      noAnswer: "No pick this week; last week's holdings stay.",
      allCash: "Stays in cash this week.",
      paper: "Paper trading",
      loadFailed: "Results could not load right now. Please refresh in a minute.",
      updated: "Updated {t} ET",
    },
    zh: {
      lang: "EN",
      page: "周度持仓",
      eyebrow: "周度持仓 · 模拟盘",
      title: "五个 1 万美元组合，每周决定一次。",
      lede: "每周，GPT 和 Claude 各自读完新闻，挑最多 3 只美股放进自己的 1 万美元组合，和动量模型、随机组合、标普 500 一起比。",
      next: "下次选股",
      when: "{d} · 美东 11:30",
      deciding: "正在选股",
      soon: "即将开始",
      units: ["天", "时", "分"],
      boardTitle: "排行榜",
      boardCols: ["组合", "市值", "收益", "持仓"],
      cash: "现金",
      arms: {
        codex: ["TradeAgent · GPT", "AI 读新闻选股"],
        claude: ["TradeAgent · Claude", "AI 读新闻选股"],
        quant: ["动量", "纯规则，不用 AI"],
        random: ["随机", "随机挑 3 只"],
        spy: ["标普 500", "买入持有 SPY"],
      },
      short: { codex: "GPT", claude: "Claude", quant: "动量", random: "随机", spy: "标普 500" },
      chartTitle: "组合市值",
      chartEmpty: "{d} 收盘后开始画曲线。",
      picksTitle: "本周 AI 选股",
      picksWeek: "{d} 这一周",
      picksFirst: "第一次选股在 {d} 美东 11:30。",
      noAnswer: "本周没有给出选择，沿用上周持仓。",
      allCash: "本周持有现金。",
      paper: "模拟盘",
      loadFailed: "成绩暂时加载不出来，请稍后刷新。",
      updated: "更新于美东 {t}",
    },
  };

  const storage = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked */ } },
  };
  const navLang = () => ((navigator.language || "en").toLowerCase().startsWith("zh") ? "zh" : "en");
  const isNum = (x) => typeof x === "number" && Number.isFinite(x);
  const esc = (x) => String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

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

  function nyParts(ms) {
    const f = new Intl.DateTimeFormat("en-US", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    const p = {};
    for (const x of f.formatToParts(new Date(ms))) p[x.type] = x.value;
    return { date: `${p.year}-${p.month}-${p.day}`, hm: Number(p.hour) * 60 + Number(p.minute) };
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
    const color = (arm) => `var(--${arm})`;
    const key = (arm) => h("i", { class: "wk-key", style: `background:${color(arm)}` });

    const book = (arm) => ((data && data.books) || {})[`main:${arm}`] || {};
    const weeks = () => ((data && data.weeks) || []).slice().sort((a, b) => (a.week < b.week ? -1 : 1));
    function nextDecision() {
      const ws = weeks();
      return ws.length ? ws[ws.length - 1].end : data && data.start_date;
    }

    function section(id, title, aside, body) {
      return h("section", { class: "wk-sec", id: "wk-" + id },
        h("div", { class: "wk-head" }, h("h2", { text: title }), aside ? h("p", { text: aside }) : null), body);
    }

    // ---------------------------------------------------------------- hero
    function clock() {
      const next = data ? nextDecision() : null;
      const card = h("div", { class: "wk-card wk-clock" }, h("span", { class: "wk-label", text: t("next") }));
      if (!next) return card;
      const diff = nyInstant(next, 11, 30) - Date.now();
      const count = h("div", { class: "wk-count num" });
      if (diff > 0) {
        const mins = Math.floor(diff / 60000);
        const parts = [Math.floor(mins / 1440), Math.floor((mins % 1440) / 60), mins % 60];
        parts.forEach((v, i) => { if (i > 0 || v > 0) { count.append(String(v)); count.append(h("small", { text: t("units")[i] })); } });
      } else {
        count.textContent = diff > -90 * 60000 ? t("deciding") : t("soon");
        count.classList.add("wk-count-text");
      }
      card.append(count, h("p", { class: "wk-when", text: t("when", { d: day(next) }) }));
      return card;
    }
    const clockHost = h("div", { class: "wk-clock-host" });

    function hero() {
      return h("div", { class: "wk-hero" },
        h("div", {},
          h("p", { class: "wk-eyebrow", text: t("eyebrow") }),
          h("h1", { class: "wk-title", text: t("title") }),
          h("p", { class: "wk-lede", text: t("lede") })),
        clockHost);
    }

    // ---------------------------------------------------------------- leaderboard
    function board() {
      const order = ARMS.slice();
      if (ARMS.some((a) => isNum(book(a).cumulative_return))) {
        order.sort((a, b) => (book(b).cumulative_return ?? -Infinity) - (book(a).cumulative_return ?? -Infinity));
      }
      const rows = order.map((arm, i) => {
        const b = book(arm);
        const held = (b.positions || []).map((p) => p.symbol);
        const [name, sub] = t("arms." + arm);
        return h("li", { class: "wk-row" + (arm === "codex" ? " is-agent" : "") },
          h("span", { class: "wk-rank num", text: String(i + 1) }),
          h("div", { class: "wk-arm" }, key(arm), h("div", {}, h("b", { text: name }), h("small", { text: sub }))),
          h("div", { class: "wk-hold" }, held.length ? held.map((s) => h("span", { class: "wk-tk", text: s })) : h("span", { class: "wk-muted", text: t("cash") })),
          h("div", { class: "wk-val num", text: money(b.nav) }),
          h("div", { class: "wk-ret num " + tone(b.cumulative_return), text: pct(b.cumulative_return) }));
      });
      const [c0, c1, c2, c3] = t("boardCols");
      const header = h("li", { class: "wk-row wk-row-head" }, h("span"), h("span", { text: c0 }), h("span", { class: "wk-hold", text: c3 }), h("span", { class: "wk-val", text: c1 }), h("span", { class: "wk-ret", text: c2 }));
      return section("board", t("boardTitle"), null, h("ol", { class: "wk-card wk-board" }, header, rows));
    }

    // ---------------------------------------------------------------- chart
    function chart() {
      const box = h("div", { class: "wk-chart" });
      const series = ARMS.map((arm) => ({ arm, marks: book(arm).nav_series || [] }));
      const dates = [...new Set(series.flatMap((s) => s.marks.map((m) => m.date)))].sort();
      const legend = h("div", { class: "wk-legend" }, ARMS.map((arm) => h("span", {}, h("i", { class: arm === "spy" || arm === "random" ? "dash" : "", style: `border-color:${color(arm)}` }), t("short." + arm))));
      const card = h("div", { class: "wk-card" }, box);
      if (dates.length < 2) {
        box.append(h("div", { class: "wk-placeholder" }, h("span", { text: t("chartEmpty", { d: day((data && data.start_date) || dates[0]) }) })));
        return section("chart", t("chartTitle"), null, card);
      }
      const svgHost = h("div");
      const tip = h("div", { class: "wk-tip", hidden: true });
      box.append(legend, svgHost, tip);
      const draw = () => {
        const W = Math.max(300, box.clientWidth - 28);
        const small = W < 560;
        const H = small ? 220 : 280;
        const L = 56, R = small ? 54 : 110, T = 10, B = 26;
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
        const ticks = [0, dates.length - 1];
        for (const i of ticks) svg += `<text class="axis" x="${x(i).toFixed(1)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : "end"}">${esc(dayShort(dates[i]))}</text>`;
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
          if (path) svg += `<path class="line${s.arm === "codex" ? " lead" : ""}" d="${path}" stroke="${color(s.arm)}"${dash}/>`;
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
          tip.style.top = "44px";
        });
        overlay.addEventListener("mouseleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
      };
      if (typeof ResizeObserver === "function") {
        const ro = new ResizeObserver(() => draw());
        ro.observe(box);
        observers.push(ro);
      }
      requestAnimationFrame(draw);
      return section("chart", t("chartTitle"), null, card);
    }

    // ---------------------------------------------------------------- this week's AI picks
    function picks() {
      const decisions = ((data && data.decisions) || []).slice().sort((a, b) => (a.week < b.week ? -1 : 1));
      const latest = decisions[decisions.length - 1];
      if (!latest) {
        return section("picks", t("picksTitle"), null, h("div", { class: "wk-card" }, h("p", { class: "wk-empty", text: t("picksFirst", { d: day(nextDecision()) }) })));
      }
      const cards = ["codex", "claude"].map((arm) => {
        const r = (latest.arms || {})[arm] || {};
        const sel = r.selection || {};
        const list = sel.picks || [];
        let body;
        if (r.status !== "VALID") body = h("p", { class: "wk-muted", text: t("noAnswer") });
        else if (!list.length) body = h("p", { class: "wk-muted", text: sel.reason || t("allCash") });
        else body = h("ul", { class: "wk-picks" }, list.map((p) => h("li", {}, h("b", { class: "wk-sym", text: p.symbol }), h("span", { text: p.reason || "" }))));
        return h("article", { class: "wk-card wk-pick" },
          h("h3", {}, key(arm), t("arms." + arm)[0]),
          body);
      });
      return section("picks", t("picksTitle"), t("picksWeek", { d: day(latest.week) }), h("div", { class: "wk-duo" }, cards));
    }

    function foot() {
      return h("footer", { class: "wk-foot" },
        h("span", { text: t("paper") }),
        data && data.generated_at ? h("span", { text: t("updated", { t: stamp(data.generated_at) }) }) : null,
        h("a", { href: REPO, target: "_blank", rel: "noopener noreferrer", text: "GitHub" }));
    }

    // ---------------------------------------------------------------- shell
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

    function render() {
      observers.splice(0).forEach((o) => o.disconnect());
      if (!embedded) {
        document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
        document.title = "TradeAgent · " + t("page");
      }
      clockHost.replaceChildren(clock());
      const wrap = h("div", { class: "wk-wrap" }, hero());
      if (data && !failed) wrap.append(board(), chart(), picks(), foot());
      else if (failed) wrap.append(h("div", { class: "wk-sec" }, h("div", { class: "wk-card wk-empty", role: "status", text: t("loadFailed") })), foot());
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

(() => { const host = document.getElementById('weekly-root'); if (!host || !window.TradeAgentWeekly) return; const root = host.attachShadow({mode: 'open'}); root.innerHTML = "<style>/* TradeAgent weekly portfolios. Self-contained: works standalone and inside a shadow root. */\n:host { display: block; }\n.wk-page { margin: 0; background: #f5f6f8; }\n.wk {\n  --page: #f5f6f8; --panel: #ffffff; --soft: #f0f2f5; --ink: #0d1726; --ink-2: #3b4757; --muted: #6a7585;\n  --line: #dde2e8; --rule: #c9d0d9; --navy: #0b1f3a; --accent: #1d4ed8; --up: #0b7a43; --down: #c0262d;\n  --codex: #4b45d1; --claude: #c2572b; --quant: #0f7c86; --random: #98a2b0; --spy: #0d1726;\n  --radius: 12px; --shadow: 0 1px 2px rgba(13, 23, 38, 0.05);\n  --font: \"Inter\", ui-sans-serif, system-ui, -apple-system, \"Segoe UI\", \"PingFang SC\", \"Hiragino Sans GB\", \"Microsoft YaHei\", \"Noto Sans CJK SC\", sans-serif;\n  --mono: ui-monospace, \"SF Mono\", \"Cascadia Mono\", \"Roboto Mono\", Menlo, monospace;\n  color: var(--ink); background: var(--page); font: 15px/1.55 var(--font); -webkit-font-smoothing: antialiased;\n}\n@media (prefers-color-scheme: dark) {\n  .wk-page { background: #070c15; }\n  .wk {\n    --page: #070c15; --panel: #0d1420; --soft: #121b2a; --ink: #e6ebf2; --ink-2: #b4bfcd; --muted: #8391a4;\n    --line: #1d2939; --rule: #2a3a50; --navy: #050a12; --accent: #6c9cff; --up: #2ec27e; --down: #ff6b6b;\n    --codex: #8f8af0; --claude: #ee8a5f; --quant: #3cc3cc; --random: #6b778a; --spy: #e6ebf2; --shadow: none;\n  }\n}\n.wk[data-updown=\"cn\"] { --up: #c0262d; --down: #0b7a43; }\n@media (prefers-color-scheme: dark) { .wk[data-updown=\"cn\"] { --up: #ff6b6b; --down: #2ec27e; } }\n.wk *, .wk *::before, .wk *::after { box-sizing: border-box; }\n.wk h1, .wk h2, .wk h3, .wk p, .wk ol, .wk ul { margin: 0; }\n.wk ol, .wk ul { padding: 0; list-style: none; }\n.wk a { color: inherit; }\n.wk button { font: inherit; color: inherit; }\n.wk .num { font-family: var(--mono); font-variant-numeric: tabular-nums; }\n.wk .up { color: var(--up); }\n.wk .down { color: var(--down); }\n.wk-muted { color: var(--muted); }\n.wk-wrap { max-width: 1080px; margin: 0 auto; padding: 0 24px 56px; }\n.wk-card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); }\n.wk-label { font-size: 12px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted); }\n.wk-key { width: 10px; height: 10px; border-radius: 3px; flex: none; display: inline-block; }\n\n/* Standalone header */\n.wk-bar { background: var(--navy); color: #e8edf4; }\n.wk-bar-row { max-width: 1080px; margin: 0 auto; padding: 0 24px; min-height: 56px; display: flex; align-items: center; gap: 16px; }\n.wk-mark { display: inline-flex; align-items: center; gap: 10px; font-weight: 700; font-size: 16px; text-decoration: none; color: #fff; }\n.wk-logo { width: 22px; height: 22px; border-radius: 4px; background: linear-gradient(135deg, #34c38f, #1d4ed8); }\n.wk-bar-title { color: #9fb0c6; font-size: 13px; }\n.wk-bar-tools { margin-left: auto; }\n.wk .wk-pill { border: 1px solid #2c3f5e; background: transparent; color: #e8edf4; border-radius: 4px; padding: 5px 12px; font-size: 13px; font-weight: 600; cursor: pointer; }\n\n/* Hero */\n.wk-hero { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 40px; align-items: center; padding: 52px 0 12px; }\n.wk-eyebrow { color: var(--accent); font-size: 12px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; }\n.wk-title { margin-top: 14px !important; font-size: clamp(32px, 4.4vw, 50px); line-height: 1.08; letter-spacing: -0.02em; font-weight: 750; }\n.wk-lede { margin-top: 18px !important; font-size: 18px; color: var(--ink-2); max-width: 54ch; }\n.wk-clock { padding: 20px 22px; }\n.wk-count { margin-top: 10px; font-size: 40px; font-weight: 700; line-height: 1; letter-spacing: -0.02em; }\n.wk-count small { font-size: 15px; font-weight: 600; color: var(--muted); margin: 0 6px 0 2px; letter-spacing: 0; }\n.wk-count-text { font-family: var(--font); font-size: 26px; }\n.wk-when { margin-top: 8px; font-size: 14px; color: var(--ink-2); }\n\n/* Sections */\n.wk-sec { margin-top: 48px; }\n.wk-head { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; margin-bottom: 14px; }\n.wk-head h2 { font-size: 24px; letter-spacing: -0.01em; }\n.wk-head p { color: var(--muted); font-size: 14px; }\n\n/* Leaderboard */\n.wk-board { overflow: hidden; }\n.wk-row { display: grid; grid-template-columns: 28px minmax(0, 1.4fr) minmax(0, 1fr) 140px 110px; gap: 16px; align-items: center; padding: 16px 20px; border-bottom: 1px solid var(--line); }\n.wk-row:last-child { border-bottom: 0; }\n.wk-row-head { padding: 10px 20px; background: var(--soft); font-size: 11.5px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }\n.wk-row.is-agent { background: color-mix(in srgb, var(--codex) 5%, transparent); }\n.wk-rank { color: var(--muted); font-size: 13px; }\n.wk-arm { display: flex; align-items: center; gap: 12px; min-width: 0; }\n.wk-arm b { display: block; font-weight: 650; font-size: 15px; }\n.wk-arm small { display: block; color: var(--muted); font-size: 12.5px; }\n.wk-hold { display: flex; gap: 6px; flex-wrap: wrap; }\n.wk-tk { font: 600 12px var(--mono); padding: 3px 7px; border-radius: 5px; background: var(--soft); color: var(--ink-2); }\n.wk-val { text-align: right; font-size: 14px; color: var(--ink-2); }\n.wk-ret { text-align: right; font-size: 20px; font-weight: 700; }\n.wk-row-head .wk-ret, .wk-row-head .wk-val { font-size: inherit; font-weight: inherit; font-family: inherit; }\n\n/* Chart */\n.wk-chart { position: relative; padding: 14px 14px 8px; }\n.wk-legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; color: var(--ink-2); margin: 0 0 8px 4px; }\n.wk-legend span { display: inline-flex; align-items: center; gap: 7px; }\n.wk-legend i { width: 14px; height: 0; border-top: 2px solid; }\n.wk-legend i.dash { border-top-style: dashed; }\n.wk-chart svg { display: block; width: 100%; }\n.wk-chart .grid { stroke: var(--line); }\n.wk-chart .base { stroke: var(--rule); stroke-dasharray: 4 4; }\n.wk-chart .axis { fill: var(--muted); font: 11px var(--mono); }\n.wk-chart .line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }\n.wk-chart .line.lead { stroke-width: 3; }\n.wk-chart .end { font: 700 11.5px var(--mono); }\n.wk-chart .cross { stroke: var(--rule); }\n.wk-tip { position: absolute; pointer-events: none; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; font-size: 12.5px; min-width: 170px; box-shadow: 0 6px 20px rgba(13, 23, 38, 0.12); }\n.wk-tip div { display: flex; justify-content: space-between; gap: 14px; }\n.wk-tip .d { color: var(--muted); margin-bottom: 4px; }\n.wk-placeholder { display: grid; place-items: center; min-height: 200px; color: var(--muted); font-size: 14px; text-align: center; padding: 24px; }\n\n/* AI picks */\n.wk-duo { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }\n.wk-pick { padding: 18px 20px; }\n.wk-pick h3 { font-size: 15px; display: flex; align-items: center; gap: 8px; margin-bottom: 12px !important; }\n.wk-picks li { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 12px; padding: 10px 0; border-top: 1px solid var(--line); font-size: 14px; color: var(--ink-2); }\n.wk-picks li:first-child { border-top: 0; padding-top: 0; }\n.wk-sym { font: 700 15px var(--mono); color: var(--ink); }\n.wk-empty { padding: 26px 20px; text-align: center; color: var(--muted); font-size: 14.5px; }\n\n.wk-foot { margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--line); font-size: 12.5px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 6px 18px; }\n.wk-foot a { color: var(--accent); text-decoration: none; }\n\n@media (max-width: 860px) {\n  .wk-hero { grid-template-columns: 1fr; gap: 24px; padding-top: 32px; }\n  .wk-row { grid-template-columns: 22px minmax(0, 1fr) auto; grid-template-areas: \"rank arm ret\" \". hold val\"; row-gap: 8px; }\n  .wk-row .wk-rank { grid-area: rank; }\n  .wk-row .wk-arm { grid-area: arm; }\n  .wk-row .wk-hold { grid-area: hold; }\n  .wk-row .wk-val { grid-area: val; }\n  .wk-row .wk-ret { grid-area: ret; }\n  .wk-row-head { display: none; }\n}\n@media (max-width: 640px) {\n  .wk-wrap { padding: 0 16px 40px; }\n  .wk-bar-row { padding: 0 16px; }\n  .wk-bar-title { display: none; }\n  .wk-lede { font-size: 16px; }\n  .wk-duo { grid-template-columns: 1fr; }\n  .wk-sec { margin-top: 40px; }\n  .wk-head h2 { font-size: 21px; }\n  .wk-count { font-size: 34px; }\n}\n</style><div id=\"wk-mount\"></div>"; window.TradeAgentWeekly.mount(root.getElementById('wk-mount'), {embedded: true}); })();
