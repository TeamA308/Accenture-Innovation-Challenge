"""Rehearsal check for the learning-loop moment of the demo.

Drives the real API: reads the review queue, accepts a run of flagged items as
false positives, and confirms a threshold actually moved and was logged with a
readable reason. Run it before a demo to be sure the loop will fire on stage.

    python backend/tests/demo_loop_check.py http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.load(r)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main() -> int:
    use_case = "internal_copilot"
    before = get(f"/v1/policies/{use_case}")
    print(f"before: grounding_flag_threshold = {before['grounding_flag_threshold']}")

    queue = get("/v1/review/queue")["items"]
    # Prefer items whose verdict was driven by grounding: that is the signal
    # the tuner can move, and the one the demo narrates.
    targets = [
        q for q in queue
        if q["use_case"] == use_case
        and any("grounding coverage" in r for r in q.get("action_reasons", []))
    ][:6]
    if len(targets) < 5:
        targets = [q for q in queue if q["use_case"] == use_case][:6]
    if len(targets) < 5:
        print(f"only {len(targets)} reviewable items for {use_case}; "
              "replay some traffic first (POST /v1/demo/simulate)")
        return 1

    moved = None
    for i, item in enumerate(targets, 1):
        res = post(f"/v1/review/{item['id']}/override",
                   {"decision": "accept", "notes": "answer was fine; we over-flagged"})
        adj = res["adjustments"]
        print(f"  override {i}: signal={res['override']['driving_signal']:12} "
              f"machine={res['override']['machine_action']:6} "
              f"-> {len(adj)} adjustment(s)  [{res['tuner_note']}]")
        if adj:
            moved = adj[0]
            break

    after = get(f"/v1/policies/{use_case}")
    print(f"after:  grounding_flag_threshold = {after['grounding_flag_threshold']}")

    if not moved:
        print("\nNo threshold moved. The tuner needs a run of overrides on the SAME "
              "signal; check driving_signal above.")
        return 1

    print(f"\nMOVED: {moved['field_changed']} {moved['old_value']} -> {moved['new_value']}")
    print(f"reason: {moved['reason']}")

    history = get(f"/v1/policies/{use_case}/history")["adjustments"]
    print(f"\npolicy history now has {len(history)} entry/entries "
          "(this is what the chart on the Policy page draws)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
