import React, { useEffect, useState } from "react";
import { api, type ResponseDetail } from "../lib/api";
import { Card, money, timeAgo, useCaseLabel, VerdictBadge } from "../components/common";
import { SignalPanel } from "../components/SignalPanel";
import { CounterfactualDiff } from "../components/CounterfactualDiff";

/**
 * The evidence drawer: the full trace behind one verdict. This is the page you
 * open when a judge, a reviewer or an auditor says "prove it".
 */
export function Evidence({ id, onBack }: { id: string; onBack: () => void }) {
  const [d, setD] = useState<ResponseDetail | null>(null);
  const [tab, setTab] = useState<"ring0" | "ring1" | "history" | "raw">("ring0");
  const [exported, setExported] = useState<string | null>(null);

  const load = () => api.responseDetail(id).then(setD);
  useEffect(() => {
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [id]);

  if (!d) return <p className="empty">Loading the record…</p>;

  const r1 = d.ring1_result;

  return (
    <div>
      <div className="page-head">
        <button className="btn sm ghost" onClick={onBack} style={{ marginBottom: 10 }}>
          ← back
        </button>
        <h1>Evidence record</h1>
        <p>
          Everything below was produced at request time and stored verbatim: the signals,
          the thresholds they were measured against, the verdict, and anything a human did
          afterwards. This is the log a DPDP or EU AI Act reviewer asks for.
        </p>
      </div>

      <Card>
        <div className="row" style={{ marginBottom: 12 }}>
          <VerdictBadge action={d.final_action} />
          {d.action !== d.final_action && (
            <span className="pill">
              Ring 0 said “{d.action}”, revised to “{d.final_action}” after the deep check
            </span>
          )}
          {d.gate_state !== "open" && <span className="pill">commit: {d.gate_state}</span>}
          <span className="spacer" />
          <span className="tiny faint mono">{d.id}</span>
        </div>

        <div className="banner info" style={{ marginBottom: 14 }}>
          <span>ℹ</span>
          <div>{d.action_explanation}</div>
        </div>

        <div className="kv" style={{ marginBottom: 14 }}>
          <span className="k">When</span>
          <span>{new Date(d.created_at).toLocaleString()} ({timeAgo(d.created_at)})</span>
          <span className="k">Use case</span>
          <span>{useCaseLabel(d.use_case)} <span className="faint">({d.use_case})</span></span>
          <span className="k">Model</span>
          <span className="mono">{d.model_provider} / {d.model_name}</span>
          <span className="k">Downstream action</span>
          <span>
            {d.downstream_action}{" "}
            <span className="faint">
              ({d.is_reversible ? "reversible" : "irreversible — a mistake here cannot be undone"})
            </span>
          </span>
          <span className="k">Session</span>
          <span className="mono tiny">
            {d.session_id} · turn {d.turn_index + 1}
            {d.conversation && ` · carried risk ${d.conversation.accumulated_risk}`}
          </span>
          <span className="k">Cost</span>
          <span className="num">
            {money(d.cost_usd)} for {d.tokens_used} tokens
            {d.ring1_cost_usd > 0 && ` · deep check added ${money(d.ring1_cost_usd)}`}
          </span>
          <span className="k">Confidence</span>
          <span className="num">{d.confidence.toFixed(3)}</span>
        </div>

        <div className="card-title">The prompt</div>
        <div className="answer" style={{ minHeight: 0 }}>{d.prompt}</div>

        <div className="card-title" style={{ marginTop: 14 }}>
          What the model produced
          {d.redacted_text && <span className="hint">shown redacted, as delivered</span>}
        </div>
        <div className={`answer${d.redacted_text ? " blocked" : ""}`}>
          {d.redacted_text || d.raw_response_text}
        </div>
        {d.redacted_text && (
          <div className="tiny faint" style={{ marginTop: 6 }}>
            The unredacted text is retained in this record and is reachable only by an
            authorised reviewer. Redaction is mechanical — spans are replaced, nothing is
            reworded.
          </div>
        )}

        <div className="card-title" style={{ marginTop: 16 }}>Why this verdict</div>
        {(d.action_reasons || []).map((r, i) => (
          <div className="reason" key={i}>{r}</div>
        ))}
      </Card>

      <Card>
        <div className="tabs">
          <button className={`tab${tab === "ring0" ? " active" : ""}`} onClick={() => setTab("ring0")}>
            Ring 0 signals
          </button>
          <button className={`tab${tab === "ring1" ? " active" : ""}`} onClick={() => setTab("ring1")}>
            Ring 1 deep check {d.ring1_status === "pending" && <span className="spinner" />}
          </button>
          <button className={`tab${tab === "history" ? " active" : ""}`} onClick={() => setTab("history")}>
            Human review {d.overrides.length > 0 && `(${d.overrides.length})`}
          </button>
          <button className={`tab${tab === "raw" ? " active" : ""}`} onClick={() => setTab("raw")}>
            Raw record
          </button>
        </div>

        {tab === "ring0" && <SignalPanel signals={d.ring0_signals} />}

        {tab === "ring1" && (
          <div>
            {d.ring1_status === "skipped" && (
              <div className="banner">
                <span>◦</span>
                <div>
                  No deep check ran. <span className="faint">{d.ring1_reason}</span>
                  <div className="tiny faint" style={{ marginTop: 3 }}>
                    Ring 1 fires only in the grey zone. Spending a second model call on a
                    response that is already resolved is exactly the waste this design
                    avoids.
                  </div>
                </div>
              </div>
            )}
            {d.ring1_status === "deferred" && (
              <div className="banner">
                <span>◦</span>
                <div>
                  Deferred: <span className="faint">{d.ring1_reason}</span>
                  <div className="tiny faint" style={{ marginTop: 3 }}>
                    The budget was full. Deferred work is recorded rather than silently
                    dropped.
                  </div>
                </div>
              </div>
            )}
            {d.ring1_status === "pending" && (
              <div className="banner info">
                <span className="spinner" />
                <div>Deep check running. This page updates itself when it lands.</div>
              </div>
            )}
            {r1 && (
              <div>
                <div className="row" style={{ marginBottom: 12 }}>
                  <span className={`badge ${r1.escalate ? "block" : "allow"}`}>
                    <span className="dot" />
                    {r1.verdict}
                  </span>
                  <span className="pill mono">{r1.latency_ms} ms</span>
                  <span className="pill mono">{money(r1.cost_usd || 0)}</span>
                  {r1.cached && <span className="pill">cache hit</span>}
                  {r1.audit_sample && <span className="pill">random audit of an allowed response</span>}
                </div>

                {(r1.findings || []).map((f: string, i: number) => (
                  <div className="reason" key={i}>{f}</div>
                ))}

                <hr className="sep" />
                <div className="card-title">Independent re-derivation (verifier model)</div>
                {r1.judge?.ran ? (
                  <div className={`banner ${r1.judge.agrees ? "good" : "bad"}`}>
                    <span>{r1.judge.agrees ? "✓" : "✗"}</span>
                    <div>
                      <b>{r1.judge.agrees ? "Agrees" : "Disagrees"}</b>{" "}
                      <span className="faint tiny">
                        confidence {r1.judge.confidence} · {r1.judge.model}
                      </span>
                      <div style={{ marginTop: 4 }}>{r1.judge.judge_reasoning}</div>
                      {r1.judge.corrected_claim && (
                        <div className="tiny" style={{ marginTop: 4 }}>
                          Correction offered: {r1.judge.corrected_claim}
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="banner">
                    <span>◦</span>
                    <div>{r1.judge?.judge_reasoning || "The verifier did not run."}</div>
                  </div>
                )}

                <div className="card-title" style={{ marginTop: 16 }}>
                  Claim-level faithfulness
                  <span className="hint">every atomic claim, checked against the sources</span>
                </div>
                {r1.faithfulness?.ran ? (
                  <>
                    <div className="row tight" style={{ marginBottom: 8 }}>
                      <b className="num">
                        {r1.faithfulness.faithfulness_score ?? "—"}
                      </b>
                      <span className="faint tiny">
                        {r1.faithfulness.n_supported} supported ·{" "}
                        {r1.faithfulness.n_unsupported} unsupported ·{" "}
                        {r1.faithfulness.n_contradicted} contradicted, out of{" "}
                        {r1.faithfulness.n_checkable} checkable claims
                      </span>
                    </div>
                    {(r1.faithfulness.unsupported_claims || []).map((c: any, i: number) => (
                      <div key={i} className={`claim ${c.status}`}>
                        <div>{c.claim}</div>
                        {(c.issues || []).map((issue: string, j: number) => (
                          <div key={j} className={`issue${issue.startsWith("contradicts") ? " contradiction" : ""}`}>
                            {issue}
                          </div>
                        ))}
                        {c.closest_source && (
                          <div className="cite">closest source: “{c.closest_source.slice(0, 220)}”</div>
                        )}
                      </div>
                    ))}
                  </>
                ) : (
                  <div className="banner">
                    <span>◦</span>
                    <div>{r1.faithfulness?.note || "Not measured."}</div>
                  </div>
                )}

                <div className="card-title" style={{ marginTop: 18 }}>
                  Counterfactual twin
                  <span className="hint">same question, one attribute changed</span>
                </div>
                <CounterfactualDiff cf={r1.counterfactual} />
              </div>
            )}
          </div>
        )}

        {tab === "history" && (
          <div>
            {d.overrides.length === 0 && (
              <p className="empty">No human has reviewed this response yet.</p>
            )}
            {d.overrides.map((o) => (
              <div key={o.id} className="claim supported">
                <b>{o.reviewer_id}</b> decided <b>{o.decision}</b>{" "}
                <span className="faint tiny">{timeAgo(o.created_at)}</span>
                <div className="tiny faint">
                  signal the machine relied on: {o.driving_signal} · machine verdict at the
                  time: {o.machine_action}
                </div>
                {o.notes && <div style={{ marginTop: 4 }}>“{o.notes}”</div>}
              </div>
            ))}

            {d.threshold_adjustments.length > 0 && (
              <>
                <div className="card-title" style={{ marginTop: 16 }}>
                  Policy changes this review caused
                </div>
                {d.threshold_adjustments.map((a) => (
                  <div key={a.id} className="claim partial">
                    <b className="mono">
                      {a.field_changed}: {a.old_value} → {a.new_value}
                    </b>
                    <div className="tiny faint" style={{ marginTop: 3 }}>{a.reason}</div>
                  </div>
                ))}
              </>
            )}

            {d.conversation && d.conversation.turns > 1 && (
              <>
                <div className="card-title" style={{ marginTop: 16 }}>
                  Conversation this belongs to
                  <span className="hint">risk carries across turns</span>
                </div>
                <div className="tiny faint" style={{ marginBottom: 6 }}>
                  {d.conversation.turns} turns, {d.conversation.flagged_turns} flagged,
                  accumulated risk {d.conversation.accumulated_risk}. A questionable answer
                  used as the premise for later turns raises the bar for those turns.
                </div>
                {(d.conversation.history || []).map((h: any, i: number) => (
                  <div key={i} className="row tight" style={{ padding: "3px 0" }}>
                    <VerdictBadge action={h.action} />
                    <span className="truncate dim tiny">{h.prompt}</span>
                  </div>
                ))}
              </>
            )}
          </div>
        )}

        {tab === "raw" && (
          <div>
            <div className="row" style={{ marginBottom: 10 }}>
              <button
                className="btn sm"
                onClick={() => api.exportEvidence(d.id).then((r) => setExported(r.text))}
              >
                Generate compliance evidence pack
              </button>
              {exported && (
                <button
                  className="btn sm ghost"
                  onClick={() => navigator.clipboard?.writeText(exported)}
                >
                  copy to clipboard
                </button>
              )}
            </div>
            {exported && <pre className="mono" style={{ whiteSpace: "pre-wrap" }}>{exported}</pre>}
            <details className="raw" open={!exported}>
              <summary>Full stored record (JSON)</summary>
              <pre>{JSON.stringify(d, null, 2)}</pre>
            </details>
          </div>
        )}
      </Card>
    </div>
  );
}
