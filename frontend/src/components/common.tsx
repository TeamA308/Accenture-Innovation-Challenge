import React from "react";
import type { Action } from "../lib/api";

export const ACTION_LABEL: Record<string, string> = {
  allow: "allow",
  edit: "repair",
  flag: "flag",
  gate: "gate commit",
  block: "block",
};

export const ACTION_MEANING: Record<string, string> = {
  allow: "Delivered unchanged — every check passed inside this policy's thresholds.",
  edit: "Delivered with a correction attached. Only mechanical errors are repaired; the substance is never rewritten.",
  flag: "Delivered in full and queued for review. A probabilistic finding never blocks a reversible answer.",
  gate: "Text delivered, but the action it would trigger is held until a deep check or a human clears it.",
  block: "Withheld and redacted. A deterministic violation — personal data or a credential.",
};

export function VerdictBadge({ action, title }: { action?: string; title?: string }) {
  const a = (action || "allow") as Action;
  return (
    <span className={`badge ${a}`} title={title ?? ACTION_MEANING[a]}>
      <span className="dot" />
      {ACTION_LABEL[a] ?? a}
    </span>
  );
}

export function Ring1Badge({ status, reason }: { status: string; reason?: string }) {
  if (status === "pending")
    return (
      <span className="pill" title={reason}>
        <span className="spinner" /> ring 1 running
      </span>
    );
  if (status === "complete") return <span className="pill" title={reason}>ring 1 done</span>;
  if (status === "deferred")
    return <span className="pill" title={reason}>ring 1 deferred</span>;
  if (status === "failed") return <span className="pill bad" title={reason}>ring 1 failed</span>;
  return <span className="pill faint" title={reason}>ring 1 not needed</span>;
}

export function Meter({ value, tone }: { value: number; tone?: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const color =
    tone ??
    (value >= 0.75 ? "var(--allow)" : value >= 0.45 ? "var(--edit)" : "var(--block)");
  return (
    <div className="meter">
      <i style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

export function Stat({
  label,
  value,
  sub,
  small,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  small?: boolean;
}) {
  return (
    <div className="card stat">
      <span className="label">{label}</span>
      <span className={`value${small ? " small" : ""}`}>{value}</span>
      {sub && <span className="sub">{sub}</span>}
    </div>
  );
}

export function Card({
  title,
  hint,
  children,
  right,
}: {
  title?: string;
  hint?: string;
  children: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="card">
      {title && (
        <h3 className="card-title">
          {title}
          {hint && <span className="hint">{hint}</span>}
          {right && <span style={{ marginLeft: "auto" }}>{right}</span>}
        </h3>
      )}
      {children}
    </div>
  );
}

export function timeAgo(iso?: string): string {
  if (!iso) return "-";
  const then = new Date(iso).getTime();
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 60) return `${Math.floor(secs)}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export const money = (n: number) => `$${(n ?? 0).toFixed(n < 0.01 ? 5 : 2)}`;
export const pct = (n: number) => `${((n ?? 0) * 100).toFixed(1)}%`;

export function useCaseLabel(useCase: string) {
  return (
    {
      customer_facing: "Customer support",
      internal_copilot: "Internal copilot",
      decision_support_regulated: "Regulated decisions",
    } as Record<string, string>
  )[useCase] ?? useCase;
}
