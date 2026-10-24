import { useEffect, useState } from "react";
import { api } from "../api";
import type { PolicyClause } from "../types";

/** The policy wording, for reviewing without the AI panel (the manual baseline). */
export default function PolicyPanel({ policyId }: { policyId: string | null }) {
  const [clauses, setClauses] = useState<PolicyClause[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!policyId) return;
    api
      .policyClauses(policyId)
      .then(setClauses)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load the policy"));
  }, [policyId]);

  return (
    <section className="card" aria-labelledby="pol-h">
      <h2 id="pol-h">Policy</h2>
      {!policyId && <p className="muted">The policy for this claim has not been identified.</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {clauses?.map((c) => (
        <div key={c.id} className="policy-clause">
          <strong>{c.clause_id}</strong> <span className="muted">{c.section_path}</span>
          <p>{c.text}</p>
        </div>
      ))}
    </section>
  );
}
