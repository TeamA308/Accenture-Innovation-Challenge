import React, { useCallback, useEffect, useState } from "react";
import { api, type ResponseDetail } from "../lib/api";
import { Card, timeAgo, useCaseLabel, VerdictBadge } from "../components/common";
import { SignalPanel } from "../components/SignalPanel";
import { CounterfactualDiff } from "../components/CounterfactualDiff";

/**
 * Ring 2. Everything waiting on a human, ranked by risk rather than arrival.
 * Each decision here is a labelled example that feeds the threshold tuner.
 */
export function ReviewQueue({ onOpen }: { onOpen: (id: string) => void }) {
  const [items, setItems] = useState<ResponseDetail[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => api.reviewQueue().then((q) => setItems(q.items)), []);
  useEffect(() => {
    load();
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
  }, [load]);

  const decide = async (id: string, decision: string) => {
    setBusy(id);
    try {
      const res = await api.override(id, { decision, notes: notes[id] || null });
      setResult(res);
      await load();
      setOpen(null);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <div className="page-head">
        <h1>Review queue</h1>
        <p>
          Responses the machine would not resolve on its own, highest risk first — a held
          commit outranks a flagged draft no matter which arrived earlier. Every decision
          here is a labelled example: accepting something we flagged tells the system it
          was wrong to flag it.
        </p>
      </div>

      {result && (
        <Card>
          <div className={`banner ${result.adjustments?.length ? "bad" : "good"}`}>
            <span>{result.adjustments?.length ? "⇄" : "✓"}</span>
            <div>
              <b>
                Recorded: {result.override.decision} on a “{result.override.machine_action}”
                verdict.
              </b>
              <div className="tiny" style={{ marginTop: 3 }}>
                {result.tuner_note}
              </div>
              {(result.adjustments || []).map((a: any) => (
                <div key={a.id} style={{ marginTop: 7 }}>
                  <b className="mono">
                    {a.use_case} · {a.field_changed}: {a.old_value} → {a.new_value}
                  </b>
                  <div className="tiny" style={{ marginTop: 2 }}>{a.reason}</div>
                </div>
              ))}
            </div>
          </div>
          <button className="btn sm ghost" style={{ marginTop: 10 }} onClick={() => setResult(null)}>
            dismiss
          </button>
        </Card>
      )}

      {items.length === 0 && (
        <Card>
          <p className="empty">
            Nothing waiting. Send a prompt that lands in the grey zone, or replay some
            traffic from the dashboard.
          </p>
        </Card>
      )}

      {items.map((r) => {
        const isOpen = open === r.id;
        return (
          <div key={r.id} className={`queue-item${r.gate_state === "gated" ? " gated" : ""}`}>
            <div className="head" onClick={() => setOpen(isOpen ? null : r.id)}>
              <VerdictBadge action={r.final_action} />
              {r.gate_state === "gated" && (
                <span className="badge gate">
                  <span className="dot" /> commit held
                </span>
              )}
              <span className="truncate" style={{ flex: 1 }}>{r.prompt}</span>
              <span className="pill">{useCaseLabel(r.use_case)}</span>
              <span className="pill mono">conf {r.confidence.toFixed(2)}</span>
              <span className="tiny faint">{timeAgo(r.created_at)}</span>
              <span className="faint">{isOpen ? "▾" : "▸"}</span>
            </div>

            {isOpen && (
              <div className="body">
                <div className="card-title">The answer as delivered</div>
                <div className={`answer${r.redacted_text ? " blocked" : ""}`}>
                  {r.redacted_text || r.raw_response_text}
                </div>

                <div className="card-title" style={{ marginTop: 14 }}>Why it was flagged</div>
                {(r.action_reasons || []).slice(0, 6).map((x, i) => (
                  <div className="reason" key={i}>{x}</div>
                ))}

                {r.ring1_result?.counterfactual?.ran && (
                  <>
                    <div className="card-title" style={{ marginTop: 16 }}>
                      Counterfactual twin
                    </div>
                    <CounterfactualDiff cf={r.ring1_result.counterfactual} />
                  </>
                )}

                <details className="raw" style={{ marginTop: 14 }}>
                  <summary>All Ring 0 signals</summary>
                  <div style={{ marginTop: 8 }}>
                    <SignalPanel signals={r.ring0_signals} />
                  </div>
                </details>

                <label className="field" style={{ marginTop: 14 }}>
                  <span>Reviewer note (optional, stored in the audit record)</span>
                  <input
                    type="text"
                    value={notes[r.id] || ""}
                    onChange={(e) => setNotes({ ...notes, [r.id]: e.target.value })}
                    placeholder="Why you decided this way"
                  />
                </label>

                <div className="row">
                  <button
                    className="btn ok"
                    disabled={busy === r.id}
                    onClick={() => decide(r.id, "accept")}
                    title="The answer was fine — we were wrong to flag it. Counts as a false positive."
                  >
                    Accept — we over-flagged
                  </button>
                  <button
                    className="btn danger"
                    disabled={busy === r.id}
                    onClick={() => decide(r.id, "reject")}
                    title="The answer really was bad — the flag was correct."
                  >
                    Reject — the flag was right
                  </button>
                  <button
                    className="btn"
                    disabled={busy === r.id}
                    onClick={() => decide(r.id, "edit")}
                    title="Partly right; a human repaired it."
                  >
                    Edit and release
                  </button>
                  <span className="spacer" />
                  <button className="btn sm ghost" onClick={() => onOpen(r.id)}>
                    open full record →
                  </button>
                </div>
                <div className="tiny faint" style={{ marginTop: 8 }}>
                  Accepting several flagged items driven by the same signal will loosen that
                  signal's threshold for this use case — visible on the Policy page. Blocks
                  driven by a validated identity number or a credential are never loosened
                  automatically.
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
