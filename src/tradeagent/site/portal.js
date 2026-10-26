/* One public entry point; data and protocol accounts remain independent. */
(() => {
  "use strict";
  const LABELS = {
    en: { weekly: "Weekly portfolios", intraday: "Daily picks", weeklyShort: "Weekly", intradayShort: "Daily", tabs: "Research horizon" },
    zh: { weekly: "周度持仓", intraday: "每日短线", weeklyShort: "周度", intradayShort: "短线", tabs: "研究周期" },
  };
  let lang = window.TA_LANG === "zh" ? "zh" : "en";
  const weekly = document.getElementById("weekly-root");
  const intraday = document.getElementById("intraday-shell");
  const weeklyTab = document.getElementById("tab-weekly");
  const intradayTab = document.getElementById("tab-intraday");
  const tabs = document.getElementById("tabs");
  function label(tab, long, short) {
    const a = document.createElement("span"); a.className = "tab-long"; a.textContent = long;
    const b = document.createElement("span"); b.className = "tab-short"; b.textContent = short;
    tab.replaceChildren(a, b);
  }
  function show() {
    const isWeekly = !location.hash || location.hash === "#weekly";
    const copy = LABELS[lang];
    weekly.hidden = !isWeekly;
    intraday.hidden = isWeekly;
    weeklyTab.setAttribute("aria-selected", String(isWeekly));
    intradayTab.setAttribute("aria-selected", String(!isWeekly));
    label(weeklyTab, copy.weekly, copy.weeklyShort);
    label(intradayTab, copy.intraday, copy.intradayShort);
    if (tabs) tabs.setAttribute("aria-label", copy.tabs);
    document.title = "TradeAgent · " + (isWeekly ? copy.weekly : copy.intraday);
    window.dispatchEvent(new Event("resize"));
  }
  weeklyTab.addEventListener("click", () => { location.hash = "weekly"; });
  intradayTab.addEventListener("click", () => { location.hash = "intraday"; });
  window.addEventListener("hashchange", show);
  window.addEventListener("ta:lang", (event) => {
    const next = event.detail && (event.detail.lang || event.detail);
    if (next === "zh" || next === "en") { lang = next; show(); }
  });
  document.querySelectorAll('[role="tab"]').forEach(tab => tab.addEventListener("keydown", event => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const target = tab === weeklyTab ? intradayTab : weeklyTab;
      target.focus(); target.click(); event.preventDefault();
    }
  }));
  show();
})();
