import { render, screen } from "@testing-library/react";
import RecommendationPanel from "./RecommendationPanel";
import type { Recommendation } from "../types";

const base: Recommendation = {
  outcome: "deny",
  rationale: "The loss was intentional.",
  cited_clause_ids: ["STD-5.1"],
  cited_clauses: [
    { clause_id: "STD-5.1", section_path: "Exclusions", text: "We do not cover intentional damage." },
  ],
  fraud_flags: [],
  meta: {},
};

describe("RecommendationPanel", () => {
  it("quotes each cited clause in full", () => {
    render(<RecommendationPanel rec={base} />);
    expect(screen.getByText("We do not cover intentional damage.")).toBeInTheDocument();
    expect(screen.getByText("STD-5.1")).toBeInTheDocument();
    expect(screen.getByText("Deny")).toBeInTheDocument();
  });

  it("explains a flag without calling it a finding", () => {
    render(
      <RecommendationPanel
        rec={{ ...base, fraud_flags: [{ type: "duplicate_claim", detail: "Same loss as CLM-9", evidence: {} }] }}
      />,
    );
    expect(screen.getByText(/possible duplicate/i)).toBeInTheDocument();
    expect(screen.getByText(/not a finding/i)).toBeInTheDocument();
  });

  it("warns when a check could not run or evidence was weak", () => {
    render(
      <RecommendationPanel
        rec={{ ...base, outcome: "escalate", meta: { forced_by_weak_evidence: true, unchecked: ["duplicate_claim"] } }}
      />,
    );
    expect(screen.getByText(/could not be read reliably/i)).toBeInTheDocument();
    expect(screen.getByText(/could not run: duplicate_claim/i)).toBeInTheDocument();
  });
});
