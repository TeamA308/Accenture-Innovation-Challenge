import React from "react";

/**
 * The bias mirror.
 *
 * Two answers to the same question, differing by one protected attribute, side
 * by side. Bias is normally presented as a number nobody can interrogate; here
 * it is two real answers and a list of exactly what changed between them.
 */
export function CounterfactualDiff({ cf }: { cf: any }) {
  if (!cf) return null;

  if (!cf.ran) {
    return (
      <div className="banner">
        <span>◦</span>
        <div>
          The twin probe did not run: {cf.reason || "no protected attribute in the prompt"}.
          <div className="tiny faint" style={{ marginTop: 3 }}>
            Include a name, a pronoun or an age band in the prompt and the probe fires
            automatically.
          </div>
        </div>
      </div>
    );
  }

  const diffs: string[] = cf.decision_diff?.differences || [];

  return (
    <div>
      <div className={`banner ${cf.bias_flag ? "bad" : "good"}`}>
        <span>{cf.bias_flag ? "⚠" : "✓"}</span>
        <div>
          <b>{cf.summary}</b>
          <div className="tiny" style={{ marginTop: 4, opacity: 0.85 }}>
            One attribute was changed and nothing else. Wording similarity{" "}
            {(cf.similarity ?? 0).toFixed(2)} (flag below {cf.similarity_threshold}); the
            decision fields are compared separately, because biased output usually looks
            like the same paragraph with a different number in it.
          </div>
        </div>
      </div>

      <div className="row tight" style={{ margin: "12px 0" }}>
        <span className="pill">attribute: {cf.attribute_kind}</span>
        <span className="pill mono">
          <span className="swap-token">{cf.swapped_attribute}</span> →{" "}
          <span className="swap-token">{cf.swapped_to}</span>
        </span>
        <span className="pill">{cf.attribute_note}</span>
      </div>

      <div className="cf-pair">
        <div className="cf-side">
          <h4>Original — “{cf.swapped_attribute}”</h4>
          <div className="body">{cf.original_response}</div>
          <DecisionChips d={cf.decision_diff?.original_decision} />
        </div>
        <div className="cf-side twin">
          <h4>Counterfactual twin — “{cf.swapped_to}”</h4>
          <div className="body">{cf.twin_response}</div>
          <DecisionChips d={cf.decision_diff?.twin_decision} />
        </div>
      </div>

      {diffs.length > 0 && (
        <div className="cf-diff">
          <div className="card-title" style={{ marginTop: 14 }}>
            What actually changed
          </div>
          {diffs.map((d, i) => (
            <div key={i} className="item">
              <span>→</span>
              <span>{d}</span>
            </div>
          ))}
        </div>
      )}

      <details className="raw" style={{ marginTop: 12 }}>
        <summary>Show both prompts, to confirm only one word differs</summary>
        <pre>
{`original: ${cf.original_prompt}

twin:     ${cf.twin_prompt}`}
        </pre>
      </details>
    </div>
  );
}

function DecisionChips({ d }: { d: any }) {
  if (!d) return null;
  return (
    <div className="chip-list" style={{ marginTop: 10 }}>
      {d.verdict && <span className="pill">verdict: {d.verdict}</span>}
      {d.max_amount != null && (
        <span className="pill mono">amount: {Number(d.max_amount).toLocaleString()}</span>
      )}
      {d.max_percent != null && <span className="pill mono">rate: {d.max_percent}%</span>}
      {(d.conditions || []).map((c: string) => (
        <span key={c} className="pill">
          condition: {c}
        </span>
      ))}
    </div>
  );
}
