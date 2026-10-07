"""Reserved input boundary. No Agent Crowd strategy or live allocation exists."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentMarketContext:
    observed_at: float
    source: str
    adoption: float | None = None
    model_attention: float | None = None
    synthetic_consensus: float | None = None
    scheduled_flow: float | None = None
    broker_agent_usage: float | None = None


LIVE_WEIGHT = 0.0
