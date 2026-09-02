// Thin API client. Everything is same-origin when the built app is served by
// the backend; in dev the Vite proxy forwards /v1 and /ws to port 8000.

export type Action = "allow" | "edit" | "flag" | "gate" | "block";

export interface ResponseSummary {
  id: string;
  session_id: string;
  turn_index: number;
  created_at: string;
  use_case: string;
  prompt: string;
  response_text: string;
  model_provider: string;
  model_name: string;
  tokens_used: number;
  cost_usd: number;
  latency_ms: number;
  ring0_latency_us: number;
  confidence: number;
  action: Action;
  final_action: Action;
  action_reasons: string[];
  ring1_status: "pending" | "complete" | "skipped" | "deferred" | "failed";
  ring1_reason: string;
  gate_state: "open" | "gated" | "released" | "withheld";
  is_reversible: boolean;
  downstream_action: string;
  reviewed: boolean;
}

export interface ResponseDetail extends ResponseSummary {
  raw_response_text: string;
  redacted_text: string | null;
  context_docs: string[];
  ring0_signals: any;
  ring1_result: any;
  ring1_latency_ms: number;
  ring1_cost_usd: number;
  overrides: any[];
  threshold_adjustments: any[];
  conversation: any;
  action_explanation: string;
}

export interface Policy {
  id: string;
  use_case: string;
  label: string;
  description: string;
  jurisdiction: string;
  risk_tolerance: string;
  latency_budget_ms: number;
  ring1_sample_rate: number;
  ring1_spend_cap_pct: number;
  pii_block_threshold: number;
  grounding_flag_threshold: number;
  uncertainty_flag_threshold: number;
  cost_anomaly_z: number;
  confidence_block_threshold: number;
  flag_rate_slo: number;
  blocked_entity_types: string[];
  updated_at: string;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} on ${path}`);
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<any>("/health"),
  providers: () => req<any>("/v1/providers"),

  policies: () => req<{ policies: Policy[]; action_explanations: Record<string, string> }>("/v1/policies"),
  updatePolicy: (useCase: string, patch: Partial<Policy>) =>
    req<any>(`/v1/policies/${useCase}`, { method: "PUT", body: JSON.stringify(patch) }),
  resetPolicy: (useCase: string) =>
    req<any>(`/v1/policies/${useCase}/reset`, { method: "POST" }),
  policyHistory: (useCase: string) =>
    req<{ adjustments: any[] }>(`/v1/policies/${useCase}/history`),

  responses: (limit = 60) =>
    req<{ items: ResponseSummary[] }>(`/v1/responses?limit=${limit}`),
  responseDetail: (id: string) => req<ResponseDetail>(`/v1/responses/${id}`),
  exportEvidence: (id: string) => req<{ text: string }>(`/v1/responses/${id}/export`),

  reviewQueue: () => req<{ count: number; items: ResponseDetail[] }>("/v1/review/queue"),
  reviewStats: () => req<any>("/v1/review/stats"),
  override: (id: string, body: any) =>
    req<any>(`/v1/review/${id}/override`, { method: "POST", body: JSON.stringify(body) }),

  overview: () => req<any>("/v1/metrics/overview"),
  timeseries: (hours = 24) => req<any>(`/v1/metrics/timeseries?hours=${hours}`),
  trust: () => req<any>("/v1/metrics/trust"),
  trustByPolicy: () => req<any>("/v1/metrics/trust/by-policy"),
  finops: () => req<any>("/v1/metrics/finops"),
  latency: () => req<any>("/v1/metrics/latency"),

  demoPrompts: () => req<any>("/v1/demo/prompts"),
  documents: () => req<{ documents: { name: string; text: string }[] }>("/v1/demo/documents"),
  simulate: (count: number) =>
    req<any>("/v1/demo/simulate", { method: "POST", body: JSON.stringify({ count }) }),
  reset: () => req<any>("/v1/demo/reset", { method: "POST" }),
};

export interface StreamEvent {
  type: string;
  [k: string]: any;
}

/** POST /v1/generate and walk the Server-Sent Events stream it returns. */
export async function streamGenerate(
  body: any,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch("/v1/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.body) throw new Error("no response stream");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      try {
        onEvent(JSON.parse(line.slice(5).trim()));
      } catch {
        /* a partial frame; the next chunk completes it */
      }
    }
  }
}
