# Official MCP contract pin review

Observed 2026-10-07T15:03:46.350915+00:00 via metadata-only initialize and paginated tools/list.
Origin: https://agent.robinhood.com/mcp/trading (HTTPS; no redirects).
Server: `robinhood-trading`, **1.7.0**; negotiated protocol: **2025-11-25**.
Complete authenticated inventory: **84 tools**. No tools/call was permitted by
this diagnostic's pre-network guard; broker reads/review/place/cancel: **0/0/0/0**.

Compared all 18 required tools with
[official-1.6.2.json](../src/tradeagent/contracts/official-1.6.2.json), projecting
its existing inputSchema/outputSchema/annotations fields and recursively removing
only description, exactly as structural() does. **Zero structural differences**.
All required tools exist; every pinned readOnlyHint remains true. Review/place/
cancel equity and option contracts are unchanged, including input requirements,
account targeting, quantity/price, TIF/hours, response schemas and annotations.
Execution tools still have no readOnlyHint in either catalog. No approval tools
were inferred or invented. The other 66 tools are outside the old snapshot;
their presence does not establish that they were newly added or authorize use.

Classification: **version-identifier-only incompatibility (A)**.
[official-1.7.0.json](../src/tradeagent/contracts/official-1.7.0.json) preserves the
exact old structural tool map and records the reviewed live version.
source_schema_sha256 is SHA-256 of the complete captured live tool map serialized
with sorted keys and compact JSON separators: `a930fe7924b76571de412dc12cbc2adf8e8c35a5e94cabb5a23cf6ed68e58b98`.
The unmodified 1.6.2 snapshot remains available for reproducible comparison.

Contracts rejects every version other than 1.7.0 and every required structural
mismatch. Existing strategy, risk, execution, grants, budgets and sizing are unchanged.

Regression coverage checks unchanged old/new structural maps, prior and future
version rejection, required schemas, read-only annotations, and description-only
acceptance. Market-data schemas are separately pinned in
[market-data-1.7.0.json](../src/tradeagent/contracts/market-data-1.7.0.json).
