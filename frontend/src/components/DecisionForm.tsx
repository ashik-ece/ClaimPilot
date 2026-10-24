import { useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import {
  OUTCOME_LABELS,
  REASON_LABELS,
  blockers,
  fieldLabel,
  isOverride,
  recommendedOutcome,
} from "../lib/decision";
import type { DecisionDraft } from "../lib/decision";
import type { Decision, Outcome, ReasonCode, Recommended } from "../types";

const OUTCOMES: Outcome[] = ["APPROVED", "DENIED", "ESCALATED"];

export default function DecisionForm({
  claimId,
  recommendation,
  visionFields,
  reviewSessionId,
  idleSeconds,
  onDecided,
}: {
  claimId: string;
  recommendation: Recommended | undefined;
  visionFields: string[];
  reviewSessionId?: string;
  idleSeconds?: () => number;
  onDecided: (d: Decision) => void;
}) {
  const [choice, setChoice] = useState<Outcome | null>(null);
  const [reason, setReason] = useState<ReasonCode | "">("");
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const draft: DecisionDraft = { choice, reason, note, confirmed };
  const problems = blockers(draft, recommendation, visionFields);
  const override = isOverride(choice, recommendation);
  const recommended = recommendedOutcome(recommendation);

  function toggle(field: string) {
    const next = new Set(confirmed);
    if (next.has(field)) next.delete(field);
    else next.add(field);
    setConfirmed(next);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (problems.length > 0 || choice === null) return;
    setBusy(true);
    setError(null);
    try {
      onDecided(
        await api.decide(claimId, {
          outcome: choice,
          reason_code: override && reason !== "" ? reason : undefined,
          note: note.trim() || undefined,
          confirmed_fields: visionFields.filter((f) => confirmed.has(f)),
          review_session_id: reviewSessionId,
          longest_idle_seconds: idleSeconds?.(),
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record the decision");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit} aria-labelledby="dec-h">
      <h2 id="dec-h">Your decision</h2>

      {visionFields.length > 0 && (
        <fieldset>
          <legend>Check these against the scanned page</legend>
          <p className="muted small">
            The model read these from an image. A misread digit can look valid, so only you can
            catch it.
          </p>
          {visionFields.map((f) => (
            <label key={f} className="check">
              <input type="checkbox" checked={confirmed.has(f)} onChange={() => toggle(f)} />
              {fieldLabel(f)} matches the document
            </label>
          ))}
        </fieldset>
      )}

      <fieldset>
        <legend>Outcome</legend>
        {OUTCOMES.map((o) => (
          <label key={o} className="check">
            <input type="radio" name="outcome" checked={choice === o} onChange={() => setChoice(o)} />
            {OUTCOME_LABELS[o]}
            {o === recommended && <span className="tag">AI recommends</span>}
          </label>
        ))}
      </fieldset>

      {override && (
        <label>
          Why are you overriding the AI?
          <select value={reason} onChange={(e) => setReason(e.target.value as ReasonCode | "")}>
            <option value="">Choose a reason&hellip;</option>
            {(Object.keys(REASON_LABELS) as ReasonCode[]).map((r) => (
              <option key={r} value={r}>
                {REASON_LABELS[r]}
              </option>
            ))}
          </select>
        </label>
      )}

      <label>
        Note (optional)
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} />
      </label>

      {error && <p role="alert" className="error">{error}</p>}
      {problems.length > 0 && (
        <ul className="problems" aria-label="Before you can submit">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <button className="primary" disabled={busy || problems.length > 0}>
        {busy ? "Saving…" : override ? "Override and record decision" : "Record decision"}
      </button>
    </form>
  );
}
