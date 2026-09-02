from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class GenerationResult:
    """Everything the control plane needs about one model call."""

    text: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    provider: str = ""
    model: str = ""
    # Per-token log probabilities when the provider exposes them. This is the
    # honest uncertainty signal; without it we fall back to a heuristic.
    token_logprobs: list[float] = field(default_factory=list)
    logprobs_available: bool = False
    error: str | None = None


class BaseProvider:
    name = "base"

    async def stream(
        self, prompt: str, model: str, system: str | None = None, max_tokens: int = 512
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    def result(self) -> GenerationResult:
        raise NotImplementedError
