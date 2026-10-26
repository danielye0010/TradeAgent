/* One public entry point; data and protocol accounts remain independent. */
(() => {
  "use strict";
  const weekly = document.getElementById("weekly-root");
  const intraday = document.getElementById("intraday-shell");
  const weeklyTab = document.getElementById("tab-weekly");
  const intradayTab = document.getElementById("tab-intraday");
  function show() {
    const isWeekly = !location.hash || location.hash === "#weekly";
    weekly.hidden = !isWeekly;
    intraday.hidden = isWeekly;
    weeklyTab.setAttribute("aria-selected", String(isWeekly));
    intradayTab.setAttribute("aria-selected", String(!isWeekly));
    document.title = isWeekly ? "TradeAgent · 周度持仓" : "TradeAgent · 短线研究";
    window.dispatchEvent(new Event("resize"));
  }
  weeklyTab.addEventListener("click", () => { location.hash = "weekly"; });
  intradayTab.addEventListener("click", () => { location.hash = "intraday"; });
  window.addEventListener("hashchange", show);
  document.querySelectorAll('[role="tab"]').forEach(tab => tab.addEventListener("keydown", event => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const target = tab === weeklyTab ? intradayTab : weeklyTab;
      target.focus(); target.click(); event.preventDefault();
    }
  }));
  show();
})();
