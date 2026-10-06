"""Standalone one-shot CLI; SHADOW default and unenrolled production key fail closed."""

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .broker import Broker
from .canary import CanarySelector, InstrumentFacts
from .model import Halt, digest, load_config
from .policy import (
    PolicyGuard,
    PolicyLimits,
    PolicySignature,
    deployment_hash,
    grant_context,
    require_autonomous_release,
)
from .runner import cycle
from .standalone import run_policy_once
from .standalone_mcp import ExternalOAuthToken, StandaloneExecutionTransport, StandaloneMCP
from .standalone_reference import PublicReferenceReader
from .state import State
from .supervised import OfficialExecutionAdapter


def local_policy_preflight(artifact, public_key, root, config, risk, facts, now=time.time):
    if Path(__file__).resolve().parent != Path(root).resolve() / "src/tradebot":
        raise Halt("executing package differs from frozen deployment root")
    require_autonomous_release(artifact.get("policy", {}).get("phase"))
    PolicySignature(public_key).verify(artifact, False)
    policy = artifact["policy"]
    context = policy.get("context", {})
    if (
        policy.get("phase") not in {"CANARY", "LIMITED_EQUITY"}
        or type(policy.get("issued_at")) not in (int, float)
        or type(policy.get("expires_at")) not in (int, float)
        or not policy["issued_at"] <= now() < policy["expires_at"]
    ):
        raise Halt("signed policy expired/not active")
    if (
        context.get("deployment_hash") != deployment_hash(root)
        or context.get("config_hash") != digest(asdict(config))
        or context.get("risk_hash") != digest(asdict(risk))
        or context.get("universe_evidence_hash")
        != digest({k: asdict(v) for k, v in sorted(facts.items())})
    ):
        raise Halt("local deployment/config/risk/evidence differs from policy")
    if set(facts) != set(config.allowed_symbols):
        raise Halt("signed classification anchors must cover exact configured universe")
    for fact in facts.values():
        fact.validate(now(), canary=policy["phase"] == "CANARY")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["canary-buy", "canary-exit", "run-once"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config/config.example.json"))
    parser.add_argument("--risk", type=Path, default=Path("config/risk.example.json"))
    parser.add_argument("--oauth-helper", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--universe-evidence", type=Path)
    args = parser.parse_args(argv)
    state = None
    try:
        config, risk = load_config(args.config, args.risk)
        shadow = args.action == "run-once" and config.mode == "SHADOW"
        if not shadow:
            require_autonomous_release()  # Before DB mutation, credentials or network.
            if config.mode != "SUPERVISED" or not config.supervised_enabled:
                raise Halt("policy engine requires explicit lifecycle configuration")
            if not all((args.policy, args.public_key, args.universe_evidence)):
                raise Halt("signed policy/public key/classification anchors required")
            artifact = json.loads(args.policy.read_text())
            anchors = json.loads(args.universe_evidence.read_text())
            facts = {k: InstrumentFacts(**v) for k, v in anchors.items()}
            local_policy_preflight(artifact, args.public_key, args.root, config, risk, facts)
        if not args.oauth_helper:
            raise Halt("external noninteractive OAuth helper required; no Codex token access")
        tokens = ExternalOAuthToken(args.oauth_helper, args.root)
        state = State(Path(config.state_dir))
        with StandaloneMCP(tokens, config.request_timeout_seconds) as bridge:
            broker = Broker(bridge, config, risk)
            if shadow:
                result = cycle(broker, state, config, risk)
            else:
                broker.accounts()
                selector = CanarySelector()
                # Bind the immutable signed classification anchors while independently
                # collecting/checking current public facts before each order risk boundary.
                adapter = OfficialExecutionAdapter(broker, bridge)

                def context_reader():
                    return grant_context(args.root, adapter, config, risk, facts, selector)

                context = context_reader()
                guard = PolicyGuard(
                    artifact,
                    context,
                    PolicyLimits(**artifact["policy"]["limits"]),
                    state,
                    config,
                    risk,
                    False,
                    time.time,
                    context_reader,
                    verifier=PolicySignature(args.public_key),
                )
                adapter.transport = StandaloneExecutionTransport(bridge, state, broker, guard)
                current = PublicReferenceReader(bridge, config)
                result = run_policy_once(
                    args.action,
                    state,
                    adapter,
                    config,
                    risk,
                    guard,
                    facts,
                    selector,
                    broker.histories,
                    fresh_facts_reader=current,
                )
            result["broker_calls"] = bridge.calls
        print(json.dumps(result, indent=2, default=str, allow_nan=False))
        return 2 if result["status"] in {"halted", "failed", "unknown"} else 0
    except (Halt, OSError, ValueError, TypeError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "status": "halted",
                    "reason": str(exc)
                    if isinstance(exc, Halt)
                    else "invalid local input/runtime; investigate before retry",
                }
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        if state:
            state.close()


if __name__ == "__main__":
    raise SystemExit(main())
