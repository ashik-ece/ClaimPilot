import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import AuditTimeline from "../components/AuditTimeline";
import DecisionForm from "../components/DecisionForm";
import DocumentViewer from "../components/DocumentViewer";
import ExtractionPanel from "../components/ExtractionPanel";
import PolicyPanel from "../components/PolicyPanel";
import RecommendationPanel from "../components/RecommendationPanel";
import StatusBadge from "../components/StatusBadge";
import { OUTCOME_LABELS, REASON_LABELS, fieldsToConfirm } from "../lib/decision";
import { IdleTracker } from "../lib/idle";
import { money, when } from "../lib/format";
import type {
  AuditEvent,
  Claim,
  ClaimDocument,
  Decision,
  Extraction,
  PipelineJob,
  Recommendation,
  ReviewSession,
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

/** In manual mode the AI's output is not requested at all (the server refuses it too). */
async function loadBundle(id: string, manual: boolean): Promise<Bundle> {
  const [claim, documents, jobs, extraction, recommendation, decision, audit] = await Promise.all([
    api.getClaim(id),
    api.documents(id),
    api.pipeline(id),
    manual ? null : api.extraction(id),
    manual ? null : api.recommendation(id),
    api.decision(id),
    manual ? [] : api.audit(id),
  ]);
  return { claim, documents, jobs, extraction, recommendation, decision, audit };
}

/** Keyed by claim, so moving between claims never carries a review session across. */
export default function ClaimPage() {
  const { id = "" } = useParams();
  return <ClaimReview key={id} id={id} />;
}

function ClaimReview({ id }: { id: string }) {
  const [data, setData] = useState<Bundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [review, setReview] = useState<ReviewSession | null>(null);
  const reviewRef = useRef<ReviewSession | null>(null);

  const idle = useMemo(() => {
    let store: Storage | null = null;
    try {
      store = localStorage;
    } catch {
      /* blocked */
    }
    return new IdleTracker(`claimpilot.idle.${id}`, Date.now, store);
  }, [id]);

  useEffect(() => {
    const touch = () => idle.activity();
    const events = ["mousemove", "keydown", "scroll", "click", "touchstart"] as const;
    events.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    document.addEventListener("visibilitychange", touch);
    return () => {
      events.forEach((e) => window.removeEventListener(e, touch));
      document.removeEventListener("visibilitychange", touch);
    };
  }, [idle]);

  const refresh = useCallback(async () => {
    try {
      // The first visit to an undecided claim starts the review clock and fixes the mode.
      if (!reviewRef.current) {
        const claim = await api.getClaim(id);
        if (CAN_DECIDE.has(claim.status)) {
          reviewRef.current = await api.startReview(id);
          setReview(reviewRef.current);
        }
      }
      setData(await loadBundle(id, reviewRef.current?.mode === "manual"));
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
  const manual = review?.mode === "manual";

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
          {manual && (
            <p className="card" role="note">
              Manual review: use the documents and the policy. There is no AI panel for this claim.
            </p>
          )}
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
          {manual && <PolicyPanel policyId={claim.policy_id} />}
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
                reviewSessionId={review?.id}
                idleSeconds={() => idle.longestIdleSeconds()}
                onDecided={() => void refresh()}
              />
            )
          )}
          {!manual && <AuditTimeline events={audit} />}
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
