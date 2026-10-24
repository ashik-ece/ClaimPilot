import type { Outcome, ReasonCode, Recommended } from "../types";

export const REASON_LABELS: Record<ReasonCode, string> = {
  misread_document: "AI misread a document",
  wrong_clause: "AI cited the wrong clause",
  missed_fraud_signal: "AI missed a fraud signal",
  false_fraud_flag: "Fraud flag was wrong",
  coverage_interpretation: "I read the coverage differently",
  other: "Other (explain in the note)",
};

export const OUTCOME_LABELS: Record<Outcome, string> = {
  APPROVED: "Approve",
  DENIED: "Deny",
  ESCALATED: "Escalate",
};

const AS_OUTCOME: Record<Recommended, Outcome> = {
  approve: "APPROVED",
  deny: "DENIED",
  escalate: "ESCALATED",
};

export function recommendedOutcome(rec: Recommended | undefined): Outcome | null {
  return rec ? AS_OUTCOME[rec] : null;
}

/** Overriding means choosing something other than what the AI recommended. */
export function isOverride(choice: Outcome | null, rec: Recommended | undefined): boolean {
  const recommended = recommendedOutcome(rec);
  return choice !== null && recommended !== null && choice !== recommended;
}

export interface DecisionDraft {
  choice: Outcome | null;
  reason: ReasonCode | "";
  note: string;
  confirmed: ReadonlySet<string>;
}

/**
 * What stops the adjuster submitting. Empty means the decision may be sent. Mirrors the server
 * rule (an override needs a reason code) and adds the console rule that fields the model read
 * only from a scan must be checked against the page first.
 */
export function blockers(
  draft: DecisionDraft,
  rec: Recommended | undefined,
  visionFields: readonly string[],
): string[] {
  const out: string[] = [];
  if (draft.choice === null) out.push("Choose an outcome.");
  if (isOverride(draft.choice, rec) && draft.reason === "") out.push("Pick a reason for overriding.");
  if (draft.reason === "other" && draft.note.trim() === "") out.push("Explain “other” in the note.");
  const unchecked = visionFields.filter((f) => !draft.confirmed.has(f));
  if (unchecked.length > 0)
    out.push(`Confirm against the scan: ${unchecked.map(fieldLabel).join(", ")}.`);
  return out;
}

/** Scan-read fields that drive the policy and fraud checks; a misread here changes the outcome. */
export const DECISIVE_FIELDS = ["policy_number", "incident_date", "reported_date", "total_amount"];

export function fieldsToConfirm(visionSourced: readonly string[] | undefined): string[] {
  return (visionSourced ?? []).filter((f) => DECISIVE_FIELDS.includes(f));
}

export function fieldLabel(name: string): string {
  const spaced = name.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
