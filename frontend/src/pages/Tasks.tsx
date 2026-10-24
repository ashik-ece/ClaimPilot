import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { PilotTask } from "../types";

export default function Tasks() {
  const [tasks, setTasks] = useState<PilotTask[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.tasks().then(setTasks).catch((e) => setError(e instanceof Error ? e.message : "Could not load tasks"));
  }, []);

  const done = tasks?.filter((t) => t.done).length ?? 0;
  const next = tasks?.find((t) => !t.done);

  return (
    <>
      <h1>My tasks</h1>
      {error && <p role="alert" className="error">{error}</p>}
      {tasks && tasks.length === 0 && <p className="muted">You have no assigned tasks.</p>}
      {tasks && tasks.length > 0 && (
        <>
          <p>
            {done} of {tasks.length} done. Work them in order; the clock for each claim starts when you
            first open it.
          </p>
          <div className="card flush">
            <table>
              <thead>
                <tr>
                  <th className="num">#</th>
                  <th>How to review</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => (
                  <tr key={t.claim_id}>
                    <td className="num">{t.position}</td>
                    <td>
                      {t.mode === "manual"
                        ? "Manual: documents and policy only, no AI panel"
                        : "With the AI assistant"}
                    </td>
                    <td>{t.done ? "Done" : t === next ? "Next" : "Waiting"}</td>
                    <td>
                      {!t.done && (
                        <Link to={`/claims/${t.claim_id}`}>{t === next ? "Start" : "Open"}</Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {done === tasks.length && (
            <p>
              All done. Please <Link to="/feedback">fill in the short feedback form</Link>.
            </p>
          )}
        </>
      )}
    </>
  );
}
