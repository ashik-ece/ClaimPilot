import type {
  AuditEvent,
  Claim,
  ClaimDocument,
  ClaimPage,
  ClaimStatus,
  Decision,
  Extraction,
  Outcome,
  PilotTask,
  PipelineJob,
  PolicyClause,
  ReasonCode,
  Recommendation,
  ReviewSession,
} from "./types";

const BASE = "/api";
const TOKEN_KEY = "claimpilot.token";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

// Storage can throw (private windows, blocked site data); the app must work without it.
export function loadToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function saveToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* in-memory token only */
  }
}

let token: string | null = loadToken();
let onUnauthorized: () => void = () => {};

export function setToken(value: string | null): void {
  token = value;
  saveToken(value);
}

export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join("; ");
  } catch {
    /* not JSON */
  }
  return `${res.status} ${res.statusText}`;
}

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (res.status === 401 && token) {
    setToken(null);
    onUnauthorized();
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return res;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  return (await send(path, init)).json() as Promise<T>;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return json<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/** A missing result is a normal state (not analysed yet), so 404 becomes null. */
async function optional<T>(path: string): Promise<T | null> {
  try {
    return await json<T>(path);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export const api = {
  async login(email: string, password: string): Promise<void> {
    const res = await fetch(`${BASE}/auth/login`, {
      method: "POST",
      body: new URLSearchParams({ username: email, password }),
    });
    if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
    setToken((await res.json()).access_token);
  },
  logout(): void {
    setToken(null);
  },
  hasToken: () => token !== null,

  listClaims(params: { status?: ClaimStatus | ""; limit: number; offset: number }) {
    const q = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    if (params.status) q.set("status", params.status);
    return json<ClaimPage>(`/claims?${q}`);
  },
  getClaim: (id: string) => json<Claim>(`/claims/${id}`),
  createClaim: (body: { claimant_name?: string }) => post<Claim>("/claims", body),
  async uploadDocument(id: string, file: File): Promise<ClaimDocument> {
    const form = new FormData();
    form.set("file", file);
    return (await send(`/claims/${id}/documents`, { method: "POST", body: form })).json();
  },
  analyze: (id: string) => post<{ status: string }>(`/claims/${id}/analyze`),

  documents: (id: string) => json<ClaimDocument[]>(`/claims/${id}/documents`),
  async documentBlob(id: string, documentId: string): Promise<Blob> {
    return (await send(`/claims/${id}/documents/${documentId}/content`)).blob();
  },
  pipeline: (id: string) => json<PipelineJob[]>(`/claims/${id}/pipeline`),
  extraction: (id: string) => optional<Extraction>(`/claims/${id}/extraction`),
  recommendation: (id: string) => optional<Recommendation>(`/claims/${id}/recommendation`),
  decision: (id: string) => optional<Decision>(`/claims/${id}/decision`),
  audit: (id: string) => json<AuditEvent[]>(`/claims/${id}/audit`),

  startReview: (id: string) => post<ReviewSession>(`/claims/${id}/review-sessions`),
  tasks: () => json<PilotTask[]>("/pilot/tasks"),
  policyClauses: (policyId: string) => json<PolicyClause[]>(`/policies/${policyId}/clauses`),
  feedback: (body: {
    usefulness: number;
    trust: number;
    top_complaint?: string;
    top_missing?: string;
  }) => post<{ status: string }>("/pilot/feedback", body),

  decide: (
    id: string,
    body: {
      outcome: Outcome;
      reason_code?: ReasonCode;
      note?: string;
      confirmed_fields: string[];
      review_session_id?: string;
      longest_idle_seconds?: number;
    },
  ) => post<Decision>(`/claims/${id}/decisions`, body),
};
