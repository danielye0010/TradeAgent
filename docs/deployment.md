# Deployment and recovery

## Current state

| Capability | Supported state |
| --- | --- |
| Synthetic equity/option demo | Supported; offline and isolated |
| Native official-MCP SHADOW | Supported; one-shot real-data reads, no placement/cancellation |
| Supervised equity lifecycle | Implemented; exact external approval and production key required |
| Standalone signed canary | Implemented; phase restricted to CANARY, production key unenrolled by default |
| Limited autonomous equity | Policy/lifecycle infrastructure exists; not released by default |
| Live options | Not enabled |
| Unattended production | No scheduler or validated boot/restart deployment supplied |

`AUTONOMOUS_PUBLIC_KEY_SHA256` and the supervised human-key pin are unset in the reusable source. Real commands must fail closed without enrollment and valid external authorization. The standard config is SHADOW. This document describes the reusable checkout, not the current account state of any operator.

## Validate a development change

From the repository root in an activated environment:

```bash
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
python -m compileall -q src
python -m build
python -m twine check dist/*
python scripts/check_project.py
```

Tests use synthetic transports, temporary keys, and isolated state under `work/`. No brokerage login is needed. CI checks installation, tests, style, package metadata and the offline quickstart on Linux. Fresh demo directories must live on a supported local filesystem.

## Before any real release

1. Review the exact code, pinned contracts, account policy, config, risk limits, instrument classification and corporate-action evidence. Validate compatibility through the official authenticated read path in a separately authorized operational task.
2. Generate and verify a complete deployment manifest from those reviewed bytes. Bind its hash and current account/config/risk/schema/universe context to the signed policy. Packaging or source changes invalidate an earlier deployment context.
3. Enroll only the human-owned public verification-key pin through an explicitly reviewed release change. Keep the private signer outside the repository and runtime. Obtain an externally signed, unexpired envelope with phase, quantities, sessions, and economic limits.
4. Establish persistent state ownership, locking, restart/reconciliation handling, monitoring, and broker approval/receipt behavior. Confirm real fills, fees, cash, positions and order state before advancing stages.
5. Release only the reviewed phase. Canary success is not a general limited-equity release. Wider operation needs its own strict evidence, signed bounds and operational validation.

An unsigned template is not a usable policy. This guide does not supply credentials, account context, signatures, key enrollment, or a broker-write recipe.

## Advanced standalone integration

The standalone commands `run-once`, `canary-buy`, and `canary-exit` dispatch to a one-shot runner. Its `--oauth-helper` must be an owner-only local executable outside the checkout, responsible for normal OAuth login/refresh. The project does not implement that helper or read native Codex credentials. Real mode additionally needs `--policy`, `--public-key`, and `--universe-evidence`; the runtime checks these before real network activity.

The standalone helper is not provisioned by this project. Direct authenticated MCP negotiation, current catalog/account compatibility, and unattended host reliability remain unverified. Historical native Codex connectivity does not validate the independent transport. Its synthetic tests establish local behavior only.

Do not treat the existence of these commands as a default live release. The native SHADOW path remains the documented way to connect an account for onboarding. See [architecture](architecture.md) for the transport boundaries.

## State and recovery

Real state belongs on persistent local Linux storage. Network shares, Windows-mounted paths, and volatile tmpfs are rejected. Keep separate simulation and real directories; the default real-data location is `data/`. Preserve database, JSONL, lease, process lock and signed evidence through interruptions. Backups must protect sensitive account/order information and are not source artifacts.

After an interrupted or uncertain submission, stop entry automation and preserve all evidence. Observe the authoritative broker order using the persisted submission identity and reconcile fills, fees, positions, cash and open orders. Do not automatically retry, reprice, cancel, reset quota or issue replacement approval. A crashed worker's durable lease can outlive its OS lock; wait for normal expiry and investigate ownership rather than deleting the lease.

Escalate unresolved identity, cash movement, approval-required receipts, mismatched fills or grant expiry to an operator. Any exit outside the existing signed envelope needs separately authorized handling. A halt is the intended conservative result when safe continuation cannot be established.
