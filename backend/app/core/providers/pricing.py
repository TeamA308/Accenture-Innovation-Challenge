"""Published per-million-token prices, used for the FinOps lane.

Illustrative and easy to update; the point is that every response carries an
attributable cost, not that the numbers are to the cent.
"""
from __future__ import annotations

PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    # model            (input $/Mtok, output $/Mtok)
    "gpt-4o":                 (2.50, 10.00),
    "gpt-4o-mini":            (0.15,  0.60),
    "gpt-4.1":                (2.00,  8.00),
    "gpt-4.1-mini":           (0.40,  1.60),
    "claude-opus-4":         (15.00, 75.00),
    "claude-sonnet-4":        (3.00, 15.00),
    "claude-3-5-haiku":       (0.80,  4.00),
    # Offline simulation models: priced like a mid-tier and a small model so
    # the cost lane produces realistic-looking numbers with no network access.
    "controlplane-sim-1":     (3.00, 15.00),
    "controlplane-sim-judge": (0.25,  1.25),
}

DEFAULT_PRICE = (1.00, 4.00)


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = PRICE_PER_MTOK.get(model, DEFAULT_PRICE)
    return (tokens_in / 1_000_000) * pin + (tokens_out / 1_000_000) * pout
