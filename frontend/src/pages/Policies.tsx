import React, { useCallback, useEffect, useState } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, type Policy } from "../lib/api";
import { Card, useCaseLabel } from "../components/common";

const SLIDERS: {
  key: keyof Policy;
  label: string;
  help: string;
  min: number;
  max: number;
  step: number;
}[] = [
  {
    key: "grounding_flag_threshold",
    label: "Grounding floor",
    help: "Flag when fewer than this share of the answer's claims can be traced to a source document. Higher = stricter.",
    min: 0.2, max: 0.95, step: 0.05,
  },
  {
    key: "uncertainty_flag_threshold",
    label: "Uncertainty ceiling",
    help: "Flag when the model's own doubt about its wording exceeds this. Lower = stricter.",
    min: 0.2, max: 0.95, step: 0.05,
  },
  {
    key: "pii_block_threshold",
    label: "Personal-data block confidence",
    help: "Block when a personal-data match scores at least this. Lower = stricter. Checksum-validated matches score 0.95+ regardless.",
    min: 0.4, max: 0.99, step: 0.05,
  },
  {
    key: "confidence_block_threshold",
    label: "Confidence floor",
    help: "Below this overall confidence the response is held whatever the individual signals say.",
    min: 0.05, max: 0.6, step: 0.05,
  },
  {
    key: "ring1_sample_rate",
    label: "Deep-check volume cap",
    help: "The most traffic Ring 1 may run on. 1.00 means every grey-zone case gets a deep check.",
    min: 0.01, max: 1.0, step: 0.01,
  },
  {
    key: "ring1_spend_cap_pct",
    label: "Oversight spend cap (%)",
    help: "The most Ring 1 may spend, as a percentage of what the production model spent. Enforced before each deep check, not reported afterwards.",
    min: 0.5, max: 15, step: 0.5,
  },
  {
    key: "flag_rate_slo",
    label: "Flag-rate service level",
    help: "The share of traffic this team accepts being asked to review. The tuner steers towards it so reviewers do not drown in alerts.",
    min: 0.02, max: 0.5, step: 0.01,
  },
  {
    key: "cost_anomaly_z",
    label: "Spend anomaly sensitivity",
    help: "How many standard deviations above this intent's token baseline counts as a spend anomaly. Lower = more sensitive.",
    min: 1, max: 5, step: 0.5,
  },
];

const LINE_COLORS = ["#a26bff", "#4aa8ff", "#2fd18c", "#ffc94d", "#ff5470"];

export function Policies() {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [history, setHistory] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async () => {
    const p = await api.policies();
    setPolicies(p.policies);
    setSelected((s) => s || p.policies[0]?.use_case || "");
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (selected) api.policyHistory(selected).then((h) => setHistory(h.adjustments));
  }, [selected, policies]);

  const policy = policies.find((p) => p.use_case === selected);

  const change = (key: keyof Policy, value: number) => {
    setPolicies((ps) =>
      ps.map((p) => (p.use_case === selected ? { ...p, [key]: value } : p))
    );
  };

  const save = async () => {
    if (!policy) return;
    setSaving(true);
    try {
      const patch: any = {};
      for (const s of SLIDERS) patch[s.key] = policy[s.key];
      await api.updatePolicy(policy.use_case, patch);
      setSaved("Saved. The next request through this use case uses the new thresholds.");
      await load();
      setTimeout(() => setSaved(null), 4000);
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    if (!policy) return;
    await api.resetPolicy(policy.use_case);
    await load();
    setSaved("Restored to the shipped defaults.");
    setTimeout(() => setSaved(null), 4000);
  };

  const chartData = buildChart(history);
  const fields = Array.from(new Set(history.map((h) => h.field_changed)));

  return (
    <div>
      <div className="page-head">
        <h1>Policy console</h1>
        <p>
          One checker, several risk appetites. Every threshold Ring 0 and Ring 1 use is read
          from here at request time, so moving a slider changes behaviour on the very next
          request — the same prompt can be allowed for an internal copilot and held for a
          regulated decision.
        </p>
      </div>

      <div className="row" style={{ marginBottom: 16 }}>
        {policies.map((p) => (
          <button
            key={p.use_case}
            className={`btn${selected === p.use_case ? " primary" : ""}`}
            onClick={() => setSelected(p.use_case)}
          >
            {p.label}
          </button>
        ))}
      </div>

      {policy && (
        <div className="grid split">
          <div>
            <Card title="Thresholds" hint={`${policy.risk_tolerance.replace("_", " ")} risk tolerance`}>
              <p className="dim" style={{ marginTop: 0 }}>{policy.description}</p>
              <div className="row tight" style={{ marginBottom: 12 }}>
                <span className="pill">jurisdiction: {policy.jurisdiction}</span>
                <span className="pill">latency budget: {policy.latency_budget_ms} ms</span>
              </div>

              {SLIDERS.map((s) => (
                <div className="slider-row" key={String(s.key)}>
                  <div className="name">
                    {s.label}
                    <small>{s.help}</small>
                  </div>
                  <input
                    type="range"
                    min={s.min}
                    max={s.max}
                    step={s.step}
                    value={Number(policy[s.key])}
                    onChange={(e) => change(s.key, Number(e.target.value))}
                  />
                  <div className="val">{Number(policy[s.key]).toFixed(2)}</div>
                </div>
              ))}

              <div className="row" style={{ marginTop: 14 }}>
                <button className="btn primary" onClick={save} disabled={saving}>
                  {saving ? "saving…" : "Save policy"}
                </button>
                <button className="btn ghost" onClick={reset}>
                  Restore defaults
                </button>
              </div>
              {saved && (
                <div className="banner good" style={{ marginTop: 10 }}>
                  <span>✓</span>
                  <div>{saved}</div>
                </div>
              )}
            </Card>

            <Card title="Data this policy will never let through" hint="deterministic, not tunable">
              <div className="chip-list">
                {policy.blocked_entity_types.map((t) => (
                  <span key={t} className="pill mono">{t}</span>
                ))}
              </div>
              <div className="tiny faint" style={{ marginTop: 8 }}>
                These are certainties, not probabilities: a card number that passes its Luhn
                checksum is a card number. The learning loop will not relax them however many
                times a reviewer disagrees — that is the difference between tuning a
                threshold and negotiating away a control.
              </div>
            </Card>
          </div>

          <div>
            <Card
              title="How these thresholds got here"
              hint="every change, automatic or manual"
            >
              {chartData.length === 0 ? (
                <p className="empty">
                  No changes yet. Override a few flagged items in the review queue and this
                  chart starts moving.
                </p>
              ) : (
                <ResponsiveContainer width="100%" height={210}>
                  <LineChart data={chartData} margin={{ top: 6, right: 8, left: -20, bottom: 0 }}>
                    <CartesianGrid stroke="#2a2142" vertical={false} />
                    <XAxis
                      dataKey="t"
                      tick={{ fill: "#8477a3", fontSize: 10 }}
                      tickFormatter={(v) => new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    />
                    <YAxis tick={{ fill: "#8477a3", fontSize: 10 }} domain={[0, "auto"]} />
                    <Tooltip
                      contentStyle={{ background: "#140f21", border: "1px solid #3b3059", borderRadius: 8, fontSize: 12 }}
                      labelFormatter={(v) => new Date(v).toLocaleString()}
                    />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    {fields.map((f, i) => (
                      <Line
                        key={f}
                        type="stepAfter"
                        dataKey={f}
                        stroke={LINE_COLORS[i % LINE_COLORS.length]}
                        strokeWidth={2}
                        dot={{ r: 3 }}
                        connectNulls
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              )}

              <div className="scroll-y" style={{ marginTop: 12, maxHeight: 340 }}>
                {[...history].reverse().map((a) => (
                  <div key={a.id} className="claim partial">
                    <b className="mono">
                      {a.field_changed}: {a.old_value} → {a.new_value}
                    </b>
                    <div className="tiny faint" style={{ marginTop: 3 }}>{a.reason}</div>
                    <div className="tiny faint">{new Date(a.created_at).toLocaleString()}</div>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

function buildChart(history: any[]) {
  const rows: any[] = [];
  const current: Record<string, number> = {};
  for (const a of history) {
    current[a.field_changed] = a.new_value;
    rows.push({ t: new Date(a.created_at).getTime(), ...current });
  }
  return rows;
}
