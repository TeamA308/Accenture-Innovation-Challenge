"""Real provider adapters: OpenAI and Anthropic.

Both are optional. The SDKs are only imported when a key is configured, so a
clean clone with no keys never touches the network. Both expose the same
streaming interface as the mock provider, which is what makes the "swap the
model, keep the guardrails" claim true rather than aspirational.
"""
from __future__ import annotations

import math
import time
from collections.abc import AsyncIterator

from app.core.config import settings
from app.core.providers.base import BaseProvider, GenerationResult
from app.core.providers.pricing import cost_usd


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self) -> None:
        self._result = GenerationResult(provider="openai")

    async def stream(
        self, prompt: str, model: str, system: str | None = None, max_tokens: int = 512
    ) -> AsyncIterator[str]:
        from openai import AsyncOpenAI  # imported lazily; optional dependency

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        t0 = time.perf_counter()
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        chunks: list[str] = []
        logprobs: list[float] = []
        usage_in = usage_out = 0

        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
            logprobs=True,
            stream_options={"include_usage": True},
        )
        async for event in stream:
            if event.usage:
                usage_in = event.usage.prompt_tokens
                usage_out = event.usage.completion_tokens
            for choice in event.choices or []:
                delta = getattr(choice.delta, "content", None)
                if delta:
                    chunks.append(delta)
                    yield delta
                lp = getattr(choice, "logprobs", None)
                for item in (getattr(lp, "content", None) or []):
                    logprobs.append(item.logprob)

        text = "".join(chunks)
        usage_in = usage_in or max(1, len(prompt) // 4)
        usage_out = usage_out or max(1, len(text) // 4)
        self._result = GenerationResult(
            text=text,
            tokens_in=usage_in,
            tokens_out=usage_out,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            cost_usd=cost_usd(model, usage_in, usage_out),
            provider="openai",
            model=model,
            token_logprobs=logprobs,
            logprobs_available=bool(logprobs),
        )

    def result(self) -> GenerationResult:
        return self._result


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self) -> None:
        self._result = GenerationResult(provider="anthropic")

    async def stream(
        self, prompt: str, model: str, system: str | None = None, max_tokens: int = 512
    ) -> AsyncIterator[str]:
        from anthropic import AsyncAnthropic  # imported lazily; optional dependency

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        t0 = time.perf_counter()
        chunks: list[str] = []
        usage_in = usage_out = 0

        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        async with client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                chunks.append(text)
                yield text
            final = await stream.get_final_message()
            usage_in = final.usage.input_tokens
            usage_out = final.usage.output_tokens

        text = "".join(chunks)
        self._result = GenerationResult(
            text=text,
            tokens_in=usage_in,
            tokens_out=usage_out,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            cost_usd=cost_usd(model, usage_in, usage_out),
            provider="anthropic",
            model=model,
            # Anthropic does not expose token log probabilities. Ring 0 detects
            # this and switches to the lexical uncertainty estimator -- the
            # honest degradation path, surfaced in the evidence drawer.
            token_logprobs=[],
            logprobs_available=False,
        )

    def result(self) -> GenerationResult:
        return self._result


def perplexity_from_logprobs(logprobs: list[float]) -> float:
    if not logprobs:
        return 0.0
    return math.exp(-sum(logprobs) / len(logprobs))
