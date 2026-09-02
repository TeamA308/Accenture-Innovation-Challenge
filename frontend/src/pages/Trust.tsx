import React, { useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../lib/api";
import { Card, Meter, money, Stat, useCaseLabel } from "../components/common";

/**
 * The answer to "how do you know your checker is any good?".
 *
 * Every override is a label. The audit sample supplies the misses. Both are
 * reported with their sample size, and the page says plainly when the sample is
 * too small to conclude anything.
 */
export function Trust() {
  const [report, setReport] = useState<any>(null);
  const [byPolicy, setByPolicy] = useState<any[]>([]);
  const [finops, setFinops] = useState<any>(null);
  const [latency, setLatency] = useState<any>(null);

  const load = () => {
    api.trust().then(setReport);
    api.trustByPolicy().then((r) => setByPolicy(r.policies));
    api.finops().then(setFinops);
    api.latency().then(setLatency);
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);

  if (!report) return <p className="empty">Loading…</p>;

  const cm = report.confusion_matrix;
  const ring0 = latency?.stages?.ring0 || {};
  const spendRows = (finops?.by_use_case_model || []).map((r: any) => ({
    name: `${useCaseLabel(r.use_case)}`,
    spend: Number(r.spend_usd),
    calls: r.calls,
  }));

  return (
    <div>
      <div className="page-head">
        <h1>Trust report</h1>
        <p>
          How well the checker is actually doing, measured rather than asserted. The ground
          truth is the reviewer: every override is a labelled example. Misses come from the
          audit sample — a slice of the deep-check budget spent on traffic we allowed,
          precisely so that false negatives can be counted instead of assumed to be zero.
        </p>
      </div>

      {!report.sample_is_sufficient && (
        <div className="banner" style={{ marginBottom: 16 }}>
          <span>ℹ</span>
          <div>{report.caveat}</div>
        </div>
      )}

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat
          label="Precision"
          value={report.precision != null ? report.precision.toFixed(2) : "—"}
          sub="of what we flagged, how much a human agreed was really a problem"
        />
        <Stat
          label="Recall"
          value={report.recall != null ? report.recall.toFixed(2) : "—"}
          sub="of the real problems, how many we caught"
        />
        <Stat
          label="False-positive rate"
          value={report.false_positive_rate != null ? report.false_positive_rate.toFixed(2) : "—"}
          sub="the alert-fatigue number: good answers we interrupted"
        />
        <Stat
          label="Time to a human verdict"
          value={
            report.mean_time_to_human_verdict_seconds != null
              ? `${Math.round(report.mean_time_to_human_verdict_seconds)}s`
              : "—"
          }
          sub="from the model answering to a reviewer deciding"
        />
      </div>

      <div className="grid split">
        <Card title="Confusion matrix" hint={`${report.labelled_sample} human-labelled decisions`}>
          <table className="data">
            <thead>
              <tr>
                <th></th>
                <th>A reviewer said it was harmful</th>
                <th>A reviewer said it was fine</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>We flagged or blocked it</th>
                <td>
                  <b className="good num">{cm.true_positive}</b>{" "}
                  <span className="tiny faint">caught it</span>
                </td>
                <td>
                  <b className="warn num">{cm.false_positive}</b>{" "}
                  <span className="tiny faint">false alarm</span>
                </td>
              </tr>
              <tr>
                <th>We allowed it</th>
                <td>
                  <b className="bad num">{cm.false_negative}</b>{" "}
                  <span className="tiny faint">missed it</span>
                </td>
                <td>
                  <b className="num">{cm.true_negative}</b>{" "}
                  <span className="tiny faint">correctly left alone</span>
                </td>
              </tr>
            </tbody>
          </table>

          <div className="banner info" style={{ marginTop: 14 }}>
            <span>ℹ</span>
            <div>
              <b>
                {report.audit_sample.responses_audited} allowed responses were deep-checked
                anyway, finding {report.audit_sample.misses_found} miss
                {report.audit_sample.misses_found === 1 ? "" : "es"}.
              </b>
              <div className="tiny" style={{ marginTop: 3 }}>
                {report.audit_sample.note}
              </div>
            </div>
          </div>
        </Card>

        <Card title="Traffic and flag rate">
          <div className="row tight" style={{ marginBottom: 10 }}>
            <b className="value num" style={{ fontSize: 22 }}>
              {(report.traffic.flag_rate * 100).toFixed(1)}%
            </b>
            <span className="faint tiny">
              of {report.traffic.total_responses.toLocaleString()} responses were sent for
              review
            </span>
          </div>
          {Object.entries(report.traffic.action_breakdown).map(([k, v]: any) => (
            <div key={k} style={{ marginBottom: 7 }}>
              <div className="row tight">
                <span className="tiny">{k}</span>
                <span className="spacer" />
                <span className="tiny faint num">{v}</span>
              </div>
              <Meter
                value={v / Math.max(1, report.traffic.total_responses)}
                tone={
                  { allow: "#2fd18c", edit: "#ffc94d", flag: "#ff9d3d", gate: "#4aa8ff", block: "#ff5470" }[
                    k as string
                  ] || "#8a2be2"
                }
              />
            </div>
          ))}
          <div className="tiny faint" style={{ marginTop: 8 }}>
            {report.threshold_adjustments_30d} threshold change
            {report.threshold_adjustments_30d === 1 ? "" : "s"} in the last 30 days.
          </div>
        </Card>
      </div>

      <Card title="Per policy" hint="each use case is tuned separately, and reported separately">
        <table className="data">
          <thead>
            <tr>
              <th>Use case</th>
              <th>Responses</th>
              <th>Flag rate</th>
              <th>Agreed service level</th>
              <th>Within it?</th>
              <th>Precision</th>
              <th>Labelled sample</th>
            </tr>
          </thead>
          <tbody>
            {byPolicy.map((p) => (
              <tr key={p.use_case}>
                <td>{p.label || useCaseLabel(p.use_case)}</td>
                <td className="num">{p.traffic.total_responses}</td>
                <td className="num">{(p.traffic.flag_rate * 100).toFixed(1)}%</td>
                <td className="num">{(p.flag_rate_slo * 100).toFixed(0)}%</td>
                <td>
                  <span className={`badge ${p.within_slo ? "allow" : "flag"}`}>
                    <span className="dot" />
                    {p.within_slo ? "yes" : "over"}
                  </span>
                </td>
                <td className="num">{p.precision != null ? p.precision.toFixed(2) : "—"}</td>
                <td className="num faint">{p.labelled_sample}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <div className="grid split">
        <Card title="Where the spend goes" hint="the cost lane that pays for the other two">
          {spendRows.length === 0 ? (
            <p className="empty">No traffic yet.</p>
          ) : (
            <ResponsiveContainer width="100%" height={190}>
              <BarChart data={spendRows} margin={{ top: 6, right: 8, left: -12, bottom: 0 }}>
                <CartesianGrid stroke="#2a2142" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: "#8477a3", fontSize: 10 }} />
                <YAxis tick={{ fill: "#8477a3", fontSize: 10 }} />
                <Tooltip
                  contentStyle={{ background: "#140f21", border: "1px solid #3b3059", borderRadius: 8, fontSize: 12 }}
                  formatter={(v: any) => money(Number(v))}
                />
                <Bar dataKey="spend" radius={[5, 5, 0, 0]}>
                  {spendRows.map((_: any, i: number) => (
                    <Cell key={i} fill={["#a26bff", "#4aa8ff", "#2fd18c"][i % 3]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card title="Ring 0 latency distribution" hint="measured on every request">
          <div className="grid cols-2" style={{ gap: 10 }}>
            {[
              ["median", ring0.p50_us],
              ["95th percentile", ring0.p95_us],
              ["99th percentile", ring0.p99_us],
              ["worst seen", ring0.max_us],
            ].map(([label, v]: any) => (
              <div key={label}>
                <div className="tiny faint">{label}</div>
                <div className="num" style={{ fontSize: 19, fontWeight: 650 }}>
                  {((v ?? 0) / 1000).toFixed(2)} ms
                </div>
              </div>
            ))}
          </div>
          <div className="tiny faint" style={{ marginTop: 10 }}>
            Over {(ring0.count ?? 0).toLocaleString()} runs. Ring 0 makes no second model
            call — it reads signals the first inference already produced, which is why it
            can afford to run on 100% of traffic.
          </div>
        </Card>
      </div>
    </div>
  );
}
