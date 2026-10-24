import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

function Scale({ name, label, value, onChange }: {
  name: string;
  label: string;
  value: number;
  onChange: (n: number) => void;
}) {
  return (
    <fieldset>
      <legend>{label}</legend>
      <div className="row">
        <span className="muted small">1 = not at all</span>
        {[1, 2, 3, 4, 5].map((n) => (
          <label key={n} className="check">
            <input type="radio" name={name} checked={value === n} onChange={() => onChange(n)} />
            {n}
          </label>
        ))}
        <span className="muted small">5 = very</span>
      </div>
    </fieldset>
  );
}

export default function Feedback() {
  const [usefulness, setUsefulness] = useState(0);
  const [trust, setTrust] = useState(0);
  const [complaint, setComplaint] = useState("");
  const [missing, setMissing] = useState("");
  const [state, setState] = useState<"idle" | "busy" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setState("busy");
    setError(null);
    try {
      await api.feedback({
        usefulness,
        trust,
        top_complaint: complaint.trim() || undefined,
        top_missing: missing.trim() || undefined,
      });
      setState("sent");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send feedback");
      setState("idle");
    }
  }

  if (state === "sent")
    return (
      <p className="card" role="status">
        Thank you. <Link to="/">Back to the console</Link>
      </p>
    );

  return (
    <form className="card" onSubmit={submit}>
      <h1>Feedback</h1>
      <p className="muted">Please do not include names or other personal details.</p>
      <Scale name="useful" label="How useful was the AI assistant?" value={usefulness} onChange={setUsefulness} />
      <Scale name="trust" label="How much did you trust its recommendations?" value={trust} onChange={setTrust} />
      <label>
        What was your biggest complaint?
        <textarea rows={3} value={complaint} onChange={(e) => setComplaint(e.target.value)} />
      </label>
      <label>
        What is the most important thing missing?
        <textarea rows={3} value={missing} onChange={(e) => setMissing(e.target.value)} />
      </label>
      {error && <p role="alert" className="error">{error}</p>}
      <button className="primary" disabled={usefulness === 0 || trust === 0 || state === "busy"}>
        Send feedback
      </button>
    </form>
  );
}
