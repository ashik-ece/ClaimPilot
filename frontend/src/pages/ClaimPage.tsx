import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import AuditTimeline from "../components/AuditTimeline";
import DecisionForm from "../components/DecisionForm";
import DocumentViewer from "../components/DocumentViewer";
import ExtractionPanel from "../components/ExtractionPanel";
import RecommendationPanel from "../components/RecommendationPanel";
import StatusBadge from "../components/StatusBadge";
import { OUTCOME_LABELS, REASON_LABELS, fieldsToConfirm } from "../lib/decision";
import { money, when } from "../lib/format";
import type {
  AuditEvent,
  Claim,
  ClaimDocument,
  Decision,
  Extraction,
  PipelineJob,
  Recommendation,
} from "../types";

const IN_PROGRESS = new Set(["RECEIVED", "EXTRACTED", "ANALYZED"]);
const CAN_DECIDE = new Set(["AWAITING_ADJUDICATION", "NEEDS_REVIEW"]);
const POLL_MS = 3000;

interface Bundle {
  claim: Claim;
  documents: ClaimDocument[];
  jobs: PipelineJob[];
  extraction: Extraction | null;
  recommendation: Recommendation | null;
  decision: Decision | null;
  audit: AuditEvent[];
}

async function loadBundle(id: string): Promise<Bundle> {
  const [claim, documents, jobs, extraction, recommendation, decision, audit] = await Promise.all([
    api.getClaim(id),
    api.documents(id),
    api.pipeline(id),
    api.extraction(id),
    api.recommendation(id),
    api.decision(id),
    api.audit(id),
  ]);
  return { claim, documents, jobs, extraction, recommendation, decision, audit };
}

export default function ClaimPage() {
  const { id = "" } = useParams();
  const [data, setData] = useState<Bundle | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await loadBundle(id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the claim");
    }
  }, [id]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Analysis runs in the background; keep checking until the claim settles.
  const status = data?.claim.status;
  const jobsPending = data?.jobs.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    if (!status || !(IN_PROGRESS.has(status) && (jobsPending || status !== "RECEIVED"))) return;
    const timer = setTimeout(() => void refresh(), POLL_MS);
    return () => clearTimeout(timer);
  }, [status, jobsPending, data, refresh]);

  if (error && !data) return <p role="alert" className="error">{error}</p>;
  if (!data) return <p className="muted">Loading&hellip;</p>;

  const { claim, documents, jobs, extraction, recommendation, decision, audit } = data;
  const failedJob = jobs.find((j) => j.status === "failed");

  return (
    <>
      <p>
        <Link to="/">&larr; All claims</Link>
      </p>
      <div className="row between">
        <h1>{claim.claimant_name ?? "Unnamed claim"}</h1>
        <span className="row">
          <strong>{money(claim.amount)}</strong>
          <StatusBadge status={claim.status} />
        </span>
      </div>
      {error && <p role="alert" className="error">{error}</p>}

      <div className="split">
        <div>
          <DocumentViewer key={claim.id} claimId={claim.id} documents={documents} />
        </div>
        <div className="stack">
          {IN_PROGRESS.has(claim.status) && (
            <p className="card" role="status">
              {jobs.length === 0
                ? "Analysis has not been started."
                : "Analysis is running. This page updates by itself."}
            </p>
          )}
          {claim.status === "NEEDS_REVIEW" && (
            <p className="warn card" role="note">
              Automatic analysis could not finish
              {failedJob?.last_error ? ` (${failedJob.last_error})` : ""}. Review the documents and
              decide without a recommendation.
            </p>
          )}
          {recommendation && <RecommendationPanel rec={recommendation} />}
          {extraction && <ExtractionPanel extraction={extraction} />}

          {decision ? (
            <DecisionSummary decision={decision} />
          ) : (
            CAN_DECIDE.has(claim.status) && (
              <DecisionForm
                claimId={claim.id}
                recommendation={recommendation?.outcome}
                visionFields={fieldsToConfirm(extraction?.meta.vision_sourced)}
                onDecided={() => void refresh()}
              />
            )
          )}
          <AuditTimeline events={audit} />
        </div>
      </div>
    </>
  );
}

function DecisionSummary({ decision }: { decision: Decision }) {
  return (
    <section className="card" aria-labelledby="done-h">
      <h2 id="done-h">Decision recorded</h2>
      <p>
        <strong>{OUTCOME_LABELS[decision.outcome]}</strong> on {when(decision.created_at)}
        {decision.overridden && decision.reason_code
          ? `, overriding the AI: ${REASON_LABELS[decision.reason_code]}`
          : ""}
        .
      </p>
      {decision.note && <p className="muted">{decision.note}</p>}
    </section>
  );
}
