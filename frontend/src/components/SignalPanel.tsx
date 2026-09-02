import React from "react";
import { Meter } from "./common";

/**
 * The evidence view for one response: every Ring 0 signal, with the spans,
 * validators and citations that produced it. This is what you open when
 * somebody asks "prove it".
 */
export function SignalPanel({ signals }: { signals: any }) {
  if (!signals) return <p className="empty">No signals recorded.</p>;

  const pii: any[] = signals.pii || [];
  const secrets: any[] = signals.secrets || [];
  const arith = signals.schema_arithmetic || {};
  const unc = signals.uncertainty || {};
  const ground = signals.grounding || {};
  const cost = signals.cost || {};

  return (
    <div>
      <Row label="Ring 0 latency">
        <span className="mono">
          {(signals.elapsed_us ?? 0).toLocaleString()} microseconds
        </span>{" "}
        <span className="faint tiny">
          ({((signals.elapsed_us ?? 0) / 1000).toFixed(2)} ms — runs on 100% of traffic,
          no second model call)
        </span>
      </Row>

      <Row label="Personal data">
        {pii.length === 0 ? (
          <span className="faint">none detected</span>
        ) : (
          <div className="chip-list">
            {pii.map((h, i) => (
              <span
                key={i}
                className="pill mono"
                title={`characters ${h.start}-${h.end}${
                  h.validator ? `, validated by ${h.validator}` : ""
                } (${h.engine})`}
              >
                {h.entity_type} {h.score}
                {h.validator ? " ✓" : ""}
              </span>
            ))}
          </div>
        )}
        {pii.some((h) => h.validator) && (
          <div className="tiny faint" style={{ marginTop: 4 }}>
            A tick means the value passed its checksum — Luhn for cards, Verhoeff for
            Aadhaar, issuance rules for a US social security number. That is what turns a
            pattern match into evidence.
          </div>
        )}
      </Row>

      <Row label="Credentials">
        {secrets.length === 0 ? (
          <span className="faint">none detected</span>
        ) : (
          <div className="chip-list">
            {secrets.map((s, i) => (
              <span key={i} className="pill mono" title={`${s.method} at ${s.start}-${s.end}`}>
                {s.secret_type} {s.score}
              </span>
            ))}
          </div>
        )}
      </Row>

      <Row label="Arithmetic">
        {(arith.arithmetic_checked ?? 0) === 0 ? (
          <span className="faint">no equations in this answer</span>
        ) : (
          <>
            <span className={arith.arithmetic_failed ? "bad" : "good"}>
              {arith.arithmetic_checked - arith.arithmetic_failed} of{" "}
              {arith.arithmetic_checked} recomputed correctly
            </span>
            {(arith.arithmetic || [])
              .filter((a: any) => !a.correct)
              .map((a: any, i: number) => (
                <div key={i} className="claim contradicted" style={{ marginTop: 6 }}>
                  <span className="mono">{a.expression}</span>
                  <div className="issue contradiction">{a.message}</div>
                </div>
              ))}
          </>
        )}
      </Row>

      <Row label="Model uncertainty">
        <div className="row tight">
          <b className="num">{unc.score?.toFixed?.(3) ?? "-"}</b>
          <span className="pill">{unc.method}</span>
          {unc.perplexity && <span className="pill mono">perplexity {unc.perplexity}</span>}
        </div>
        <Meter value={1 - (unc.score ?? 0)} />
        {unc.method === "lexical_fallback_no_logprobs" && (
          <div className="tiny warn" style={{ marginTop: 5 }}>
            This provider does not expose token probabilities, so this is a weaker lexical
            estimate. Never used as sole grounds to block.
          </div>
        )}
        {unc.least_confident_tokens?.length > 0 && (
          <div style={{ marginTop: 6 }}>
            <div className="tiny faint">Words the model itself was least sure of:</div>
            <div className="chip-list" style={{ marginTop: 4 }}>
              {unc.least_confident_tokens.map((t: any, i: number) => (
                <span key={i} className="pill mono" title={`p = ${t.p}`}>
                  {t.token} <span className="faint">{t.p}</span>
                </span>
              ))}
            </div>
          </div>
        )}
        {unc.certainty_phrases?.length > 0 && (
          <div className="tiny warn" style={{ marginTop: 5 }}>
            Assertive language detected ({unc.certainty_phrases.join(", ")}) — high fluency
            with weak evidence is the "confidently wrong" pattern.
          </div>
        )}
      </Row>

      <Row label="Grounding">
        <GroundingBlock ground={ground} />
      </Row>

      <Row label="Cost">
        <div className="row tight">
          <span className="pill">intent: {cost.intent}</span>
          <span className="pill mono">{cost.tokens_total} tokens</span>
          <span className="pill mono">${(cost.cost_usd ?? 0).toFixed(5)}</span>
          {cost.baseline?.baseline_tokens != null && (
            <span className="pill mono" title="z-score against this intent's rolling baseline">
              z = {cost.z}
            </span>
          )}
        </div>
        {(cost.flags || []).map((f: string, i: number) => (
          <div key={i} className="tiny warn" style={{ marginTop: 4 }}>
            {f}
          </div>
        ))}
        {(cost.flags || []).length === 0 && (
          <div className="tiny faint" style={{ marginTop: 4 }}>
            spend in line with the baseline for this intent
          </div>
        )}
      </Row>

      {(signals.degraded || []).length > 0 && (
        <Row label="Degraded checks">
          {signals.degraded.map((d: string, i: number) => (
            <div key={i} className="bad tiny">
              {d}
            </div>
          ))}
        </Row>
      )}
    </div>
  );
}

export function GroundingBlock({ ground }: { ground: any }) {
  if (!ground || Object.keys(ground).length === 0)
    return <span className="faint">not evaluated</span>;

  if (ground.score === null || ground.score === undefined) {
    return (
      <div>
        <span className="badge neutral">ungroundable</span>
        <div className="tiny faint" style={{ marginTop: 5 }}>
          {ground.note ||
            "No source documents were supplied, so nothing in this answer could be verified."}
        </div>
        <div className="tiny warn" style={{ marginTop: 4 }}>
          Reported as unverified rather than silently scored as correct — the honest answer
          when there is no ground truth to check against.
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="row tight">
        <b className="num">{ground.score?.toFixed(2)}</b>
        <span className={`badge ${ground.status === "grounded" ? "allow" : ground.status === "contradicted" ? "block" : "flag"}`}>
          <span className="dot" />
          {ground.status}
        </span>
        <span className="faint tiny">
          {ground.supported} supported · {ground.unsupported} unsupported ·{" "}
          {ground.contradicted} contradicted
        </span>
      </div>
      <Meter value={ground.score} />
      <div style={{ marginTop: 8 }}>
        {(ground.claims || []).map((c: any, i: number) => (
          <div key={i} className={`claim ${c.status}`}>
            <div>{c.claim}</div>
            {c.status === "not_a_factual_claim" && (
              <div className="tiny faint">not a checkable factual claim</div>
            )}
            {c.citation && (
              <div className="cite">
                source: “{c.citation.text.slice(0, 240)}
                {c.citation.text.length > 240 ? "…" : ""}”
              </div>
            )}
            {(c.issues || []).map((issue: string, j: number) => (
              <div
                key={j}
                className={`issue${issue.startsWith("contradicts") ? " contradiction" : ""}`}
              >
                {issue}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="signal-row">
      <div className="signal-key">{label}</div>
      <div className="signal-val">{children}</div>
    </div>
  );
}
