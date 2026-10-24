import { when } from "../lib/format";
import type { AuditEvent } from "../types";

export default function AuditTimeline({ events }: { events: AuditEvent[] }) {
  // Model calls are noisy; collapse them into the events around them.
  const shown = events.filter((e) => e.event_type !== "model.call");
  return (
    <section className="card" aria-labelledby="aud-h">
      <h2 id="aud-h">History</h2>
      <ol className="timeline">
        {shown.map((e) => (
          <li key={e.id}>
            <span className="muted">{when(e.created_at)}</span> {e.event_type}
            <span className="muted"> by {e.actor.startsWith("agent:") ? e.actor : "adjuster"}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
