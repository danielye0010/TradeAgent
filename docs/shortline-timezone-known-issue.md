# Existing shortline timezone issue

Observed 2026-10-26 in monthly-paper-10k-v2: eight midday/afternoon decisions on
2026-10-19 through 2026-10-23 were UNAVAILABLE: `timestamps require timezone`.
weekly-hold-v1 does not repair, rerun or rewrite that experiment. Existing protocol,
configuration, timers and immutable missing observations remain unchanged. A future
repair needs a separate commit, implementation identity and effective timestamp.
