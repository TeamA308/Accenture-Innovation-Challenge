import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, streamGenerate, type Policy, type ResponseSummary } from "../lib/api";
import { useVerdictStream } from "../lib/ws";
import {
  Card, Meter, money, Ring1Badge, Stat, timeAgo, useCaseLabel, VerdictBadge,
} from "../components/common";

interface Props {
  onOpen: (id: string) => void;
  onCounts: (c: { queue: number }) => void;
}

const ACTION_COLORS: Record<string, string> = {
  allow: "#2fd18c", edit: "#ffc94d", flag: "#ff9d3d", gate: "#4aa8ff", block: "#ff5470",
};

export function Dashboard({ onOpen, onCounts }: Props) {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [providers, setProviders] = useState<any[]>([]);
  const [demo, setDemo] = useState<any[]>([]);
  const [rows, setRows] = useState<ResponseSummary[]>([]);
  const [overview, setOverview] = useState<any>(null);
  const [series, setSeries] = useState<any[]>([]);
  const [fresh, setFresh] = useState<Set<string>>(new Set());

  const [prompt, setPrompt] = useState("");
  const [useCase, setUseCase] = useState("customer_facing");
  const [provider, setProvider] = useState("mock");
  const [model, setModel] = useState("controlplane-sim-1");
  const [docs, setDocs] = useState<string[]>([]);
  const [docNames, setDocNames] = useState<string[]>([]);
  const [reversible, setReversible] = useState(true);
  const [downstream, setDownstream] = useState("draft");
  const [sessionId, setSessionId] = useState(() => `demo-${Date.now()}`);

  const [streaming, setStreaming] = useState(false);
  const [answer, setAnswer] = useState("");
  const [halted, setHalted] = useState<string[] | null>(null);
  const [verdict, setVerdict] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const abort = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    const [r, o, t] = await Promise.all([
      api.responses(40), api.overview(), api.timeseries(24),
    ]);
    setRows(r.items);
    setOverview(o);
    setSeries(t.buckets);
    const flagged = ["flag", "edit", "gate", "block"];
    onCounts({
      queue: r.items.filter((x) => flagged.includes(x.final_action) && !x.reviewed).length,
    });
  }, [onCounts]);

  useEffect(() => {
    api.policies().then((p) => setPolicies(p.policies));
    api.providers().then((p) => setProviders(p.providers));
    api.demoPrompts().then((d) => setDemo(d.prompts || []));
    refresh();
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [refresh]);

  const markFresh = (id: string) => {
    setFresh((s) => new Set(s).add(id));
    setTimeout(() => setFresh((s) => {
      const n = new Set(s); n.delete(id); return n;
    }), 2000);
  };

  const connected = useVerdictStream((e) => {
    if (e.type === "response_created" && e.response) {
      setRows((r) => [e.response, ...r.filter((x) => x.id !== e.response.id)].slice(0, 40));
      markFresh(e.response.id);
      refresh();
    }
    if (e.type === "ring1_complete" && e.response) {
      setRows((r) => r.map((x) => (x.id === e.response.id ? e.response : x)));
      markFresh(e.response.id);
      setVerdict((v: any) =>
        v && v.response_id === e.response_id ? { ...v, ring1: e.ring1, response: e.response } : v
      );
      refresh();
    }
    if (e.type === "override") refresh();
  });

  const send = async () => {
    if (!prompt.trim() || streaming) return;
    setStreaming(true);
    setAnswer("");
    setHalted(null);
    setVerdict(null);
    abort.current = new AbortController();
    try {
      await streamGenerate(
        {
          prompt, use_case: useCase, model_provider: provider, model_name: model,
          context_docs: docs, is_reversible: reversible, downstream_action: downstream,
          session_id: sessionId,
        },
        (e) => {
          if (e.type === "token") setAnswer((a) => a + e.text);
          if (e.type === "stream_halted") setHalted(e.entity_types);
          if (e.type === "verdict") {
            setVerdict(e);
            setAnswer(e.display_text);
          }
        },
        abort.current.signal
      );
    } catch (err) {
      setVerdict({ error: String(err) });
    } finally {
      setStreaming(false);
      refresh();
    }
  };

  const applyDemo = (id: string) => {
    const d = demo.find((x) => x.id === id);
    if (!d) return;
    setPrompt(d.prompt);
    setUseCase(d.use_case);
    setDocs(d.context_docs || []);
    setDocNames(d.docs || []);
    setReversible(d.is_reversible !== false);
    setDownstream(d.downstream_action || "draft");
  };

  const run = async (fn: () => Promise<any>, label: string) => {
    setBusy(label);
    try { await fn(); await refresh(); } finally { setBusy(""); }
  };

  const providerModels = useMemo(
    () => providers.find((p) => p.provider === provider)?.models || [],
    [providers, provider]
  );
  useEffect(() => {
    if (providerModels.length && !providerModels.includes(model)) setModel(providerModels[0]);
  }, [providerModels]); // eslint-disable-line

  const ring0 = overview?.latency?.ring0 || {};
  const fin = overview?.finops || {};
  const ov = overview?.oversight || {};

  return (
    <div>
      <div className="page-head">
        <h1>Live oversight</h1>
        <p>
          Send a prompt and watch the answer stream while the checks run beside it. The
          answer never waits for a verdict — only an irreversible action does.
        </p>
      </div>

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat
          label="Responses checked"
          value={(overview?.responses_checked ?? 0).toLocaleString()}
          sub={`100% carry a verdict · ${money(overview?.spend_usd ?? 0)} spent`}
        />
        <Stat
          label="Ring 0 median latency"
          value={`${((ring0.p50_us ?? 0) / 1000).toFixed(2)} ms`}
          sub={`p95 ${((ring0.p95_us ?? 0) / 1000).toFixed(2)} ms · p99 ${((ring0.p99_us ?? 0) / 1000).toFixed(2)} ms over ${(ring0.count ?? 0).toLocaleString()} runs`}
        />
        <Stat
          label="Sent to deep review"
          value={`${((overview?.deep_check_rate ?? 0) * 100).toFixed(1)}%`}
          sub={`oversight cost ${ov.ring1_spend_pct_of_inference ?? 0}% of inference spend`}
        />
        <Stat
          label="Recoverable spend"
          value={money(fin.recoverable_usd_sampled ?? 0)}
          sub={`${fin.over_modelled_calls ?? 0} over-modelled · ${fin.retry_loops_detected ?? 0} retry loops`}
        />
      </div>

      <div className="grid split">
        {/* ------------------------------------------------------ composer */}
        <div>
          <Card
            title="Send a request through the control plane"
            right={
              <span className={`conn${connected ? " live" : ""}`}>
                <i /> {connected ? "live" : "reconnecting"}
              </span>
            }
          >
            <label className="field">
              <span>Try a demo prompt</span>
              <select value="" onChange={(e) => applyDemo(e.target.value)}>
                <option value="">— pick one, or type your own below —</option>
                {demo.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Prompt</span>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Ask the assistant something…"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
                }}
              />
            </label>

            <div className="grid cols-2" style={{ gap: 12 }}>
              <label className="field">
                <span>Use case (which policy applies)</span>
                <select value={useCase} onChange={(e) => setUseCase(e.target.value)}>
                  {policies.map((p) => (
                    <option key={p.use_case} value={p.use_case}>
                      {p.label} · {p.risk_tolerance.replace("_", " ")} risk
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Downstream action</span>
                <select
                  value={downstream}
                  onChange={(e) => {
                    setDownstream(e.target.value);
                    setReversible(e.target.value === "draft");
                  }}
                >
                  <option value="draft">draft a human reads (reversible)</option>
                  <option value="email_send">send an email (irreversible)</option>
                  <option value="payment">release a payment (irreversible)</option>
                  <option value="db_write">write to a record (irreversible)</option>
                  <option value="api_commit">commit a decision (irreversible)</option>
                </select>
              </label>
            </div>

            <div className="grid cols-2" style={{ gap: 12 }}>
              <label className="field">
                <span>Model provider</span>
                <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                  {providers.map((p) => (
                    <option key={p.provider} value={p.provider} disabled={!p.ready}>
                      {p.provider}
                      {p.ready ? "" : " (no API key set)"}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Model</span>
                <select value={model} onChange={(e) => setModel(e.target.value)}>
                  {providerModels.map((m: string) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="row" style={{ marginBottom: 10 }}>
              <span className="tiny faint">Source documents:</span>
              {docNames.length ? (
                docNames.map((d) => (
                  <span key={d} className="pill mono">
                    {d}
                  </span>
                ))
              ) : (
                <span className="pill">none — answer will be ungroundable</span>
              )}
              {docs.length > 0 && (
                <button className="btn sm ghost" onClick={() => { setDocs([]); setDocNames([]); }}>
                  clear
                </button>
              )}
            </div>

            <div className="row">
              <button className="btn primary" onClick={send} disabled={streaming || !prompt.trim()}>
                {streaming ? <><span className="spinner" /> streaming…</> : "Send"}
              </button>
              <span className="tiny faint">Ctrl/⌘ + Enter</span>
              <span className="spacer" />
              <button
                className="btn sm ghost"
                onClick={() => setSessionId(`demo-${Date.now()}`)}
                title="Conversation risk accumulates across turns of one session. This starts a fresh one."
              >
                new session
              </button>
            </div>
          </Card>

          {(answer || streaming) && (
            <Card title="Answer">
              {halted && (
                <div className="banner bad" style={{ marginBottom: 10 }}>
                  <span>■</span>
                  <div>
                    <b>Stream stopped mid-generation.</b> Ring 0 found {halted.join(", ")} in
                    the text as it was being written, so generation was cut off before the
                    rest of the record could be produced.
                  </div>
                </div>
              )}
              <div className={`answer${halted ? " blocked" : ""}`}>
                {answer}
                {streaming && <span className="caret" />}
              </div>

              {verdict && !verdict.error && (
                <>
                  <div className="row" style={{ marginTop: 12 }}>
                    <VerdictBadge action={verdict.action} />
                    <Ring1Badge status={verdict.ring1_status} reason={verdict.ring1_reason} />
                    {verdict.gate_state === "gated" && (
                      <span className="badge gate">
                        <span className="dot" /> commit held
                      </span>
                    )}
                    <span className="pill mono">
                      confidence {verdict.confidence?.toFixed(2)}
                    </span>
                    <span className="pill mono">
                      ring 0 {(verdict.ring0_latency_us / 1000).toFixed(2)} ms
                    </span>
                    <span className="spacer" />
                    <button className="btn sm" onClick={() => onOpen(verdict.response_id)}>
                      Open evidence →
                    </button>
                  </div>

                  <div style={{ marginTop: 10 }}>
                    {(verdict.reasons || []).slice(0, 6).map((r: string, i: number) => (
                      <div className="reason" key={i}>
                        {r}
                      </div>
                    ))}
                  </div>

                  {verdict.ring1 && (
                    <div
                      className={`banner ${verdict.ring1.escalate ? "bad" : "good"}`}
                      style={{ marginTop: 10 }}
                    >
                      <span>{verdict.ring1.escalate ? "▲" : "✓"}</span>
                      <div>
                        <b>
                          Ring 1 {verdict.ring1.verdict} in {verdict.ring1.latency_ms} ms
                          {verdict.ring1.cached ? " (cache hit)" : ""}
                        </b>
                        {(verdict.ring1.findings || []).map((f: string, i: number) => (
                          <div key={i} className="tiny" style={{ marginTop: 3 }}>
                            {f}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
              {verdict?.error && <div className="banner bad">{verdict.error}</div>}
            </Card>
          )}
        </div>

        {/* --------------------------------------------------------- charts */}
        <div>
          <Card title="Traffic by verdict" hint="last 24 hours">
            {series.length === 0 ? (
              <p className="empty">No traffic yet.</p>
            ) : (
              <ResponsiveContainer width="100%" height={168}>
                <AreaChart data={series} margin={{ top: 4, right: 6, left: -22, bottom: 0 }}>
                  <CartesianGrid stroke="#2a2142" vertical={false} />
                  <XAxis
                    dataKey="t"
                    tick={{ fill: "#8477a3", fontSize: 10 }}
                    tickFormatter={(v) => new Date(v).getHours() + "h"}
                  />
                  <YAxis tick={{ fill: "#8477a3", fontSize: 10 }} />
                  <Tooltip
                    contentStyle={{ background: "#140f21", border: "1px solid #3b3059", borderRadius: 8, fontSize: 12 }}
                  />
                  {["allow", "edit", "flag", "gate", "block"].map((k) => (
                    <Area
                      key={k}
                      type="monotone"
                      dataKey={k}
                      stackId="1"
                      stroke={ACTION_COLORS[k]}
                      fill={ACTION_COLORS[k]}
                      fillOpacity={0.26}
                    />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            )}
            <div className="row tight" style={{ marginTop: 8 }}>
              {Object.entries(overview?.by_action || {}).map(([k, v]) => (
                <span key={k} className="pill">
                  <span
                    style={{
                      width: 7, height: 7, borderRadius: 4,
                      background: ACTION_COLORS[k] || "#666", display: "inline-block",
                    }}
                  />
                  {k} {String(v)}
                </span>
              ))}
            </div>
          </Card>

          <Card title="Oversight budget" hint="Ring 1 is spend-capped in code">
            {Object.entries(ov.budget_by_use_case || {}).length === 0 ? (
              <p className="empty">No traffic yet.</p>
            ) : (
              Object.entries(ov.budget_by_use_case || {}).map(([uc, s]: any) => (
                <div key={uc} style={{ marginBottom: 11 }}>
                  <div className="row tight">
                    <b className="tiny">{useCaseLabel(uc)}</b>
                    <span className="spacer" />
                    <span className="tiny faint num">
                      {s.ring1_runs}/{s.window_requests} deep-checked ({(s.ring1_rate * 100).toFixed(1)}%)
                    </span>
                  </div>
                  <Meter
                    value={Math.min(1, s.ring1_spend_pct / 3)}
                    tone={s.ring1_spend_pct > 3 ? "var(--block)" : "var(--violet-400)"}
                  />
                  <div className="tiny faint">
                    {s.ring1_spend_pct}% of inference spend
                    {s.deferred_for_budget > 0 && ` · ${s.deferred_for_budget} deferred at the cap`}
                  </div>
                </div>
              ))
            )}
            <div className="tiny faint" style={{ marginTop: 6 }}>
              Cache: {ov.cache?.entries ?? 0} results · queue depth {ov.queue_depth ?? 0} ·{" "}
              {ov.dashboards_connected ?? 0} dashboard(s) connected
            </div>
          </Card>

          <Card title="Fleet controls">
            <div className="row">
              <button className="btn sm" disabled={!!busy} onClick={() => run(() => api.simulate(120), "sim")}>
                {busy === "sim" ? <><span className="spinner" /> running</> : "Replay 120 requests"}
              </button>
              <button className="btn sm danger" disabled={!!busy} onClick={() => run(api.reset, "reset")}>
                {busy === "reset" ? "clearing…" : "Clear traffic"}
              </button>
            </div>
            <div className="tiny faint" style={{ marginTop: 7 }}>
              The replay pushes synthetic traffic through the real pipeline — the same
              detectors, scorer and policies — so the latency and flag rates above are
              measured rather than mocked. Clearing traffic leaves policies untouched.
            </div>
          </Card>
        </div>
      </div>

      {/* --------------------------------------------------------- feed */}
      <Card title="Recent responses" hint="updates live over a WebSocket — no polling">
        <div className="scroll-y">
          <table className="data">
            <thead>
              <tr>
                <th>When</th>
                <th>Use case</th>
                <th>Prompt</th>
                <th>Verdict</th>
                <th>Confidence</th>
                <th>Ring 0</th>
                <th>Ring 1</th>
                <th>Commit</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="empty">
                    Nothing yet — send a prompt above, or replay some traffic.
                  </td>
                </tr>
              )}
              {rows.map((r) => (
                <tr
                  key={r.id}
                  className={`clickable${fresh.has(r.id) ? " fresh" : ""}`}
                  onClick={() => onOpen(r.id)}
                >
                  <td className="faint tiny">{timeAgo(r.created_at)}</td>
                  <td className="tiny">{useCaseLabel(r.use_case)}</td>
                  <td className="truncate dim">{r.prompt}</td>
                  <td>
                    <VerdictBadge action={r.final_action} />
                  </td>
                  <td className="num">{r.confidence.toFixed(2)}</td>
                  <td className="num mono">{(r.ring0_latency_us / 1000).toFixed(2)}ms</td>
                  <td>
                    <Ring1Badge status={r.ring1_status} reason={r.ring1_reason} />
                  </td>
                  <td className="tiny">
                    {r.gate_state === "gated" ? (
                      <span className="badge gate">
                        <span className="dot" /> held
                      </span>
                    ) : (
                      <span className="faint">{r.gate_state}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
