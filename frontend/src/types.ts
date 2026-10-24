export type ClaimStatus =
  | "RECEIVED"
  | "EXTRACTED"
  | "ANALYZED"
  | "AWAITING_ADJUDICATION"
  | "APPROVED"
  | "DENIED"
  | "ESCALATED"
  | "NEEDS_REVIEW";

export type Outcome = "APPROVED" | "DENIED" | "ESCALATED";
export type Recommended = "approve" | "deny" | "escalate";

export type ReasonCode =
  | "misread_document"
  | "wrong_clause"
  | "missed_fraud_signal"
  | "false_fraud_flag"
  | "coverage_interpretation"
  | "other";

export interface Claim {
  id: string;
  policy_id: string | null;
  claimant_name: string | null;
  claim_type: string;
  amount: string | null;
  status: ClaimStatus;
  created_at: string;
  updated_at: string;
}

export interface ClaimPage {
  items: Claim[];
  total: number;
  limit: number;
  offset: number;
}

export interface ClaimDocument {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
}

export interface Extraction {
  fields: Record<string, unknown>;
  meta: {
    needs_review?: boolean;
    issues?: string[];
    unverified_fields?: string[];
    vision_sourced?: string[];
    used_vision?: boolean;
  };
}

export interface CitedClause {
  clause_id: string;
  section_path: string;
  text: string;
}

export interface FraudFlag {
  type: string;
  detail: string;
  evidence: unknown;
}

export interface Recommendation {
  outcome: Recommended;
  rationale: string;
  cited_clause_ids: string[];
  cited_clauses: CitedClause[];
  fraud_flags: FraudFlag[];
  meta: {
    rules_outcome?: Recommended;
    rules_reasons?: string[];
    forced_by_weak_evidence?: boolean;
    source?: string;
    notes?: string[];
    unchecked?: string[];
  };
}

export interface PipelineJob {
  stage: string;
  status: string;
  attempts: number;
  last_error: string | null;
}

export interface Decision {
  outcome: Outcome;
  overridden: boolean;
  reason_code: ReasonCode | null;
  note: string | null;
  created_at: string;
}

export interface AuditEvent {
  id: number;
  actor: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}
