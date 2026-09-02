from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_gateway import LLMGateway
from app.core.orchestrator import GenerateRequest, generate_once, stream_generate
from app.db.session import SessionLocal, get_session

router = APIRouter(prefix="/v1", tags=["generate"])


class GenerateBody(BaseModel):
    prompt: str = Field(min_length=1)
    use_case: str = "internal_copilot"
    model_provider: str | None = None
    model_name: str | None = None
    context_docs: list[str] = Field(default_factory=list)
    # Reversibility drives the action matrix. A draft a human will read is
    # reversible; a payment or an outbound email is not.
    is_reversible: bool = True
    downstream_action: str = "draft"
    session_id: str | None = None
    expected_schema: dict | None = None
    stream_delay: float | None = None


def _to_req(body: GenerateBody) -> GenerateRequest:
    return GenerateRequest(**body.model_dump())


@router.post("/generate")
async def generate(body: GenerateBody):
    """Stream the answer as Server-Sent Events.

    Server-Sent Events is a one-way stream from server to browser over ordinary
    HTTP: the browser opens the request and the server keeps writing lines to
    it. Tokens go out as they arrive; the verdict follows on the same stream.
    """

    async def event_source():
        # Its own session: a streaming response outlives the request-scoped one.
        async with SessionLocal() as session:
            try:
                async for event in stream_generate(session, _to_req(body)):
                    yield f"data: {json.dumps(event, default=str)}\n\n"
            except Exception as exc:  # a failure still has to close cleanly
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@router.post("/generate/sync")
async def generate_sync(body: GenerateBody, session: AsyncSession = Depends(get_session)):
    """Same pipeline, no streaming. Used by tests and the load simulator."""
    body.stream_delay = 0.0 if body.stream_delay is None else body.stream_delay
    return await generate_once(session, _to_req(body))


@router.get("/providers")
async def providers():
    return {"providers": LLMGateway.available_providers()}


class BiasProbeBody(BaseModel):
    prompt: str = Field(min_length=1)
    model_provider: str | None = None
    model_name: str | None = None
    similarity_threshold: float = 0.75


@router.post("/probe/bias")
async def bias_probe(body: BiasProbeBody):
    """Run the counterfactual twin probe on its own, without a full request.

    The same code Ring 1 calls. Exposed directly so the bias mirror can be
    driven interactively -- type any prompt containing a name, a pronoun or an
    age band and watch both answers come back side by side.
    """
    from app.core.config import settings
    from app.core.llm_gateway import LLMGateway
    from app.rings.ring1.counterfactual import counterfactual_probe, detect_protected_attribute

    provider = body.model_provider or settings.default_provider
    model = body.model_name or settings.default_model

    attribute = detect_protected_attribute(body.prompt)
    if attribute is None:
        return {
            "ran": False,
            "bias_flag": False,
            "reason": (
                "No protected attribute found to swap. Include a name, a gendered "
                "pronoun, an age band or a marital status and the probe fires."
            ),
            "known_attributes": _known_attributes(),
        }

    gw = LLMGateway()
    original = await gw.complete(body.prompt, provider, model, max_tokens=600)
    result = await counterfactual_probe(
        body.prompt, provider, model, original.text,
        similarity_threshold=body.similarity_threshold,
    )
    result["original_cost_usd"] = round(original.cost_usd, 6)
    result["known_attributes"] = _known_attributes()
    return result


def _known_attributes() -> list[dict]:
    from app.rings.ring1.counterfactual import ALL_PAIRS

    return [{"a": a, "b": b, "note": note, "kind": kind} for a, b, note, kind in ALL_PAIRS]
