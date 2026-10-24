import { OUTCOME_LABELS, recommendedOutcome } from "../lib/decision";
import type { Recommendation } from "../types";

const FLAG_TITLES: Record<string, string> = {
  date_outside_policy_period: "Loss date outside the policy period",
  inflated_estimate: "Estimate well above a typical repair",
  duplicate_claim: "Possible duplicate of an earlier claim",
};

export default function RecommendationPanel({ rec }: { rec: Recommendation }) {
  const outcome = recommendedOutcome(rec.outcome);
  return (
    <section className="card" aria-labelledby="rec-h">
      <div className="row between">
        <h2 id="rec-h">AI recommendation</h2>
        <span className={`badge outcome-${rec.outcome}`}>
          {outcome ? OUTCOME_LABELS[outcome] : rec.outcome}
        </span>
      </div>
      {rec.meta.forced_by_weak_evidence && (
        <p className="warn" role="note">
          The documents could not be read reliably, so this was escalated automatically. Check
          the source documents yourself.
        </p>
      )}
      <p>{rec.rationale}</p>

      {rec.fraud_flags.length > 0 && (
        <div className="flags" aria-label="Fraud and anomaly flags">
          <h3>Flags</h3>
          <ul>
            {rec.fraud_flags.map((f, i) => (
              <li key={i}>
                <strong>{FLAG_TITLES[f.type] ?? f.type}</strong>
                <div className="muted">{f.detail}</div>
              </li>
            ))}
          </ul>
          <p className="muted small">A flag asks you to look harder; it is not a finding.</p>
        </div>
      )}

      {(rec.meta.unchecked?.length ?? 0) > 0 && (
        <p className="warn" role="note">
          Could not run: {rec.meta.unchecked?.join(", ")}. A check that cannot run does not clear
          the claim.
        </p>
      )}

      <h3>Cited policy clauses</h3>
      {rec.cited_clauses.length === 0 && <p className="muted">No clauses cited.</p>}
      {rec.cited_clauses.map((c) => (
        <blockquote key={c.clause_id} className="clause">
          <div className="clause-head">
            <strong>{c.clause_id}</strong> <span className="muted">{c.section_path}</span>
          </div>
          {c.text}
        </blockquote>
      ))}
    </section>
  );
}
