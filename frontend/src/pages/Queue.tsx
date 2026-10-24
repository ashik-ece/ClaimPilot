import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import StatusBadge from "../components/StatusBadge";
import { money, when } from "../lib/format";
import type { Claim, ClaimStatus } from "../types";

const PAGE = 20;
const FILTERS: { value: ClaimStatus | ""; label: string }[] = [
  { value: "", label: "All" },
  { value: "AWAITING_ADJUDICATION", label: "Ready for review" },
  { value: "NEEDS_REVIEW", label: "Needs manual review" },
  { value: "RECEIVED", label: "Received" },
  { value: "APPROVED", label: "Approved" },
  { value: "DENIED", label: "Denied" },
  { value: "ESCALATED", label: "Escalated" },
];

export default function Queue() {
  const [filter, setFilter] = useState<ClaimStatus | "">("AWAITING_ADJUDICATION");
  const [offset, setOffset] = useState(0);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await api.listClaims({ status: filter, limit: PAGE, offset });
      setClaims(page.items);
      setTotal(page.total);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load claims");
    } finally {
      setLoading(false);
    }
  }, [filter, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <div className="row between">
        <h1>Claims</h1>
        <label className="inline">
          Show
          <select
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value as ClaimStatus | "");
              setOffset(0);
            }}
          >
            {FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <p role="alert" className="error">{error}</p>}
      <div className="card flush">
        <table>
          <thead>
            <tr>
              <th>Claimant</th>
              <th className="num">Amount</th>
              <th>Status</th>
              <th>Received</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((c) => (
              <tr key={c.id}>
                <td>
                  <Link to={`/claims/${c.id}`}>{c.claimant_name ?? "Unnamed claim"}</Link>
                </td>
                <td className="num">{money(c.amount)}</td>
                <td>
                  <StatusBadge status={c.status} />
                </td>
                <td className="muted">{when(c.created_at)}</td>
              </tr>
            ))}
            {!loading && claims.length === 0 && (
              <tr>
                <td colSpan={4} className="muted empty">
                  No claims here.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="row between">
        <span className="muted">
          {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE, total)}`} of {total}
        </span>
        <span className="row">
          <button disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
            Previous
          </button>
          <button disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
            Next
          </button>
        </span>
      </div>

      <NewClaim onDone={load} />
    </>
  );
}

function NewClaim({ onDone }: { onDone: () => void }) {
  const navigate = useNavigate();
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const claim = await api.createClaim({});
      for (const file of files) await api.uploadDocument(claim.id, file);
      await api.analyze(claim.id);
      onDone();
      navigate(`/claims/${claim.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>New claim</h2>
      <p className="muted">
        Upload the claim's PDFs. Analysis runs in the background and the claim appears under
        &ldquo;Ready for review&rdquo; when it finishes.
      </p>
      <input
        type="file"
        accept="application/pdf"
        multiple
        aria-label="Claim documents"
        onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
      />
      {error && <p role="alert" className="error">{error}</p>}
      <button className="primary" disabled={busy || files.length === 0}>
        {busy ? "Uploading…" : "Upload and analyse"}
      </button>
    </form>
  );
}
