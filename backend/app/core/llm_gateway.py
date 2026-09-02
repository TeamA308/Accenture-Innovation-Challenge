"""Model-agnostic gateway.

One interface in front of every provider. The control plane only ever sees a
stream of text plus a GenerationResult, so swapping the underlying model
changes nothing about the checks, the thresholds or the audit schema. That is
the "governance survives the next model" claim, expressed as an interface.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from app.core.config import settings
from app.core.providers.base import GenerationResult
from app.core.providers.mock import MockProvider

log = logging.getLogger("controlplane.gateway")


class LLMGateway:
    def __init__(self) -> None:
        self._last: GenerationResult = GenerationResult()
        self.scenario_key = ""
        self.scenario_note = ""

    @staticmethod
    def available_providers() -> list[dict]:
        """What the UI is allowed to offer in the model-swap dropdown."""
        out = [{
            "provider": "mock",
            "models": ["controlplane-sim-1", "controlplane-sim-judge"],
            "ready": True,
            "note": "Offline simulator. No key or network needed.",
        }]
        out.append({
            "provider": "openai",
            "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
            "ready": bool(settings.openai_api_key),
            "note": "Set OPENAI_API_KEY to enable." if not settings.openai_api_key else "",
        })
        out.append({
            "provider": "anthropic",
            "models": ["claude-3-5-haiku", "claude-sonnet-4"],
            "ready": bool(settings.anthropic_api_key),
            "note": "Set ANTHROPIC_API_KEY to enable." if not settings.anthropic_api_key else "",
        })
        return out

    def _build(self, provider: str):
        if provider == "openai" and settings.openai_api_key:
            from app.core.providers.remote import OpenAIProvider

            return OpenAIProvider()
        if provider == "anthropic" and settings.anthropic_api_key:
            from app.core.providers.remote import AnthropicProvider

            return AnthropicProvider()
        if provider in ("openai", "anthropic"):
            log.warning("provider %s requested but no API key set; using mock", provider)
        return MockProvider()

    async def generate(
        self,
        prompt: str,
        model_provider: str = "mock",
        model_name: str = "controlplane-sim-1",
        system: str | None = None,
        max_tokens: int = 512,
    ) -> AsyncIterator[str]:
        """Async generator of text chunks. Call `.result()` once it is drained."""
        impl = self._build(model_provider)
        try:
            async for chunk in impl.stream(prompt, model_name, system=system, max_tokens=max_tokens):
                yield chunk
            self._last = impl.result()
        except Exception as exc:  # a failed provider must never kill the request
            log.exception("provider error")
            self._last = GenerationResult(
                text="",
                provider=model_provider,
                model=model_name,
                error=f"{type(exc).__name__}: {exc}",
            )
            yield ""
        self.scenario_key = getattr(impl, "scenario_key", "")
        self.scenario_note = getattr(impl, "scenario_note", "")

    async def complete(
        self,
        prompt: str,
        model_provider: str = "mock",
        model_name: str = "controlplane-sim-judge",
        system: str | None = None,
        max_tokens: int = 512,
    ) -> GenerationResult:
        """Non-streaming convenience wrapper, used by Ring 1."""
        async for _ in self.generate(
            prompt, model_provider, model_name, system=system, max_tokens=max_tokens
        ):
            pass
        return self._last

    def result(self) -> GenerationResult:
        return self._last


gateway = LLMGateway()
