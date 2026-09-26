// Client for the TRACE web API (server.py). Every error message from the server is advisor-facing.

export type Tone = "good" | "caution" | "bad" | "neutral";

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

export interface EvidenceSource {
  title: string;
  url: string;
}

export interface Evidence {
  kind: "policy" | "marsh" | "company";
  document?: string;
  page?: number;
  section?: string;
  row?: string | null;
  column?: string | null;
  label?: string;
  text: string;
  quotes: string[];
  footnotes?: string[];
  sources?: EvidenceSource[];
}

export interface ClaimActions {
  approve: boolean;
  edit: boolean;
  remove: boolean;
  attest: boolean;
}

export interface ClaimView {
  id: string;
  text: string;
  role: string;
  status: string | null;
  status_label: string;
  tone: Tone;
  explanation: string;
  qualifier: string | null;
  removed: boolean;
  shown: boolean;
  material: boolean;
  advisor_action: "APPROVED" | "EDITED" | "REMOVED" | "ATTESTED" | null;
  advisor_note: string | null;
  evidence: Evidence[];
  actions: ClaimActions;
}

export interface SlideView {
  number: number;
  title: string;
  claims: ClaimView[];
}

export interface GateItem {
  id: string;
  message: string;
  claim_id: string | null;
  slide: number | null;
  blocking: boolean;
  acknowledged: boolean;
  selection: boolean;
}

export interface Preview {
  status: "ready" | "updating" | "blocked" | "unavailable";
  count: number;
  version?: string;
  message: string;
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
    counts: { status: string; label: string; tone: Tone; count: number }[];
    export_allowed: boolean;
  };
  blocking: GateItem[];
  review_items: GateItem[];
  slides: SlideView[];
  selection: { compared: Policy[]; selected_id: string; by_advisor: boolean } | null;
  preview: Preview;
  downloads: { pptx: boolean; audit: boolean };
}

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
  approveClaim: (runId: string, claimId: string, note = "") =>
    post<ReviewView>(`${run(runId)}/claims/${claimId}/approve`, { note }),
  editClaim: (runId: string, claimId: string, text: string, note = "") =>
    post<ReviewView>(`${run(runId)}/claims/${claimId}/edit`, { text, note }),
  removeClaim: (runId: string, claimId: string, note = "") =>
    post<ReviewView>(`${run(runId)}/claims/${claimId}/remove`, { note }),
  attestClaim: (runId: string, claimId: string, note: string) =>
    post<ReviewView>(`${run(runId)}/claims/${claimId}/attest`, { note }),
  acknowledge: (runId: string, itemId: string) =>
    post<ReviewView>(`${run(runId)}/items/${encodeURIComponent(itemId)}/acknowledge`, { note: "" }),
  override: (runId: string, policyId: string, reason: string) =>
    post<{ job_id: string }>(`${run(runId)}/override`, { policy_id: policyId, reason }),
  approveDeck: (runId: string) => post<ReviewView>(`${run(runId)}/approve`),
  reject: (runId: string, reason: string) => post<ReviewView>(`${run(runId)}/reject`, { note: reason }),
  slideUrl: (runId: string, n: number, version = "") => `${run(runId)}/slides/${n}.png?v=${version}`,
  downloadUrl: (runId: string, kind: "pptx" | "audit-json" | "audit-md") => `${run(runId)}/download/${kind}`,
  logoUrl: "/api/brand/logo.png",
};
