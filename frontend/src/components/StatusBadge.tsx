import type { ClaimStatus } from "../types";

const LABELS: Record<ClaimStatus, string> = {
  RECEIVED: "Received",
  EXTRACTED: "Reading documents",
  ANALYZED: "Analysing",
  AWAITING_ADJUDICATION: "Ready for review",
  APPROVED: "Approved",
  DENIED: "Denied",
  ESCALATED: "Escalated",
  NEEDS_REVIEW: "Needs manual review",
};

export default function StatusBadge({ status }: { status: ClaimStatus }) {
  return <span className={`badge status-${status.toLowerCase()}`}>{LABELS[status]}</span>;
}
