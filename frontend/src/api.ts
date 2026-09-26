// Client for the TRACE web API (server.py). Every error message from the server is advisor-facing.

export interface Policy {
  id: string;
  name: string;
}

export interface PoliciesResponse {
  policies: Policy[];
  max_file_mb: number;
}

export interface Job {
  job_id: string;
  kind: "generate" | "override";
  stages: string[];
  stage: number;
  status: "running" | "done" | "failed";
  run_id: string | null;
  company_name: string;
  error: string | null;
}

export interface SlideRef {
  number: number;
  title: string;
}

export interface Preview {
  status: "ready" | "updating" | "blocked" | "unavailable";
  count: number;
  version?: string;
  message: string;
}

/** What the advisor needs to know. The gate decides; the item-by-item detail is in the audit report files. */
export interface Attention {
  blocked: boolean;
  messages: string[];
  can_remove_blocked: boolean;
  blocked_statements: number;
  selection_issue: boolean;
  review_note: string | null;
}

export interface ReviewView {
  run_id: string;
  company_name: string;
  created_at: string;
  final_status: "IN_PROGRESS" | "AWAITING_REVIEW" | "EXPORTED" | "REJECTED" | "FAILED";
  closing_note: string | null;
  notice: string | null;
  summary: {
    flag: "PASS" | "REVIEW_REQUIRED" | "FAIL";
    flag_label: string;
    confidence: number | null;
    export_allowed: boolean;
  };
  attention: Attention;
  slides: SlideRef[];
  selection: { compared: Policy[]; selected_id: string; by_advisor: boolean } | null;
  preview: Preview;
  downloads: { pptx: boolean; audit: boolean };
}

export type DownloadKind = "pptx" | "audit-json" | "audit-md" | "audit-docx";

export class ApiError extends Error {
  status: number;
  fields: Record<string, string[]>;

  constructor(status: number, message: string, fields: Record<string, string[]> = {}) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, "We couldn't reach TRACE. Check your connection and try again.");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, body.message ?? "Something went wrong. Please try again.", body.errors ?? {});
  }
  return body as T;
}

function post<T>(path: string, body: unknown = {}): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const run = (runId: string) => `/api/runs/${encodeURIComponent(runId)}`;

export const api = {
  policies: () => request<PoliciesResponse>("/api/policies"),
  createRun: (form: FormData) =>
    request<{ job_id: string; infos: string[] }>("/api/runs", { method: "POST", body: form }),
  job: (jobId: string) => request<Job>(`/api/jobs/${encodeURIComponent(jobId)}`),
  review: (runId: string) => request<ReviewView>(run(runId)),
  preview: (runId: string) => request<Preview>(`${run(runId)}/preview`),
  removeBlocked: (runId: string) => post<ReviewView>(`${run(runId)}/remove-blocked`),
  override: (runId: string, policyId: string, reason: string) =>
    post<{ job_id: string }>(`${run(runId)}/override`, { policy_id: policyId, reason }),
  approveDeck: (runId: string) => post<ReviewView>(`${run(runId)}/approve`),
  reject: (runId: string, reason: string) => post<ReviewView>(`${run(runId)}/reject`, { note: reason }),
  slideUrl: (runId: string, n: number, version = "") => `${run(runId)}/slides/${n}.png?v=${version}`,
  downloadUrl: (runId: string, kind: DownloadKind) => `${run(runId)}/download/${kind}`,
  logoUrl: "/api/brand/logo.png",
};
