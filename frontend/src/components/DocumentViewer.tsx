import { useEffect, useState } from "react";
import { api } from "../api";
import type { ClaimDocument } from "../types";

export default function DocumentViewer({
  claimId,
  documents,
}: {
  claimId: string;
  documents: ClaimDocument[];
}) {
  const [selected, setSelected] = useState(documents[0]?.id ?? null);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!selected) return;
    let objectUrl: string | null = null;
    let cancelled = false;
    setUrl(null);
    setError(null);
    api
      .documentBlob(claimId, selected)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Could not load file"));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [claimId, selected]);

  if (documents.length === 0) return <p className="muted">No documents uploaded.</p>;
  const current = documents.find((d) => d.id === selected);

  return (
    <div className="viewer">
      <div className="tabs" role="tablist" aria-label="Claim documents">
        {documents.map((d) => (
          <button
            key={d.id}
            role="tab"
            aria-selected={d.id === selected}
            className={d.id === selected ? "tab active" : "tab"}
            onClick={() => setSelected(d.id)}
          >
            {d.filename}
          </button>
        ))}
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {url ? (
        <iframe title={current?.filename ?? "Document"} src={url} />
      ) : (
        !error && <p className="muted">Loading&hellip;</p>
      )}
    </div>
  );
}
