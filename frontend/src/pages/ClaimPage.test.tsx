import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { api } from "../api";
import ClaimPage from "./ClaimPage";

vi.mock("../api", () => ({
  api: {
    getClaim: vi.fn(),
    documents: vi.fn(),
    pipeline: vi.fn(),
    extraction: vi.fn(),
    recommendation: vi.fn(),
    decision: vi.fn(),
    audit: vi.fn(),
    startReview: vi.fn(),
    policyClauses: vi.fn(),
    decide: vi.fn(),
  },
}));
const m = vi.mocked(api);

const claim = {
  id: "c1",
  policy_id: "p1",
  claimant_name: "Jane Doe",
  claim_type: "auto_collision",
  amount: "5000.00",
  status: "AWAITING_ADJUDICATION" as const,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function open() {
  render(
    <MemoryRouter initialEntries={["/claims/c1"]}>
      <Routes>
        <Route path="/claims/:id" element={<ClaimPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  m.getClaim.mockResolvedValue(claim);
  m.documents.mockResolvedValue([]);
  m.pipeline.mockResolvedValue([]);
  m.decision.mockResolvedValue(null);
  m.policyClauses.mockResolvedValue([
    { id: "1", clause_id: "STD-2.1", section_path: "Coverage", text: "We cover collision loss." },
  ]);
  m.extraction.mockResolvedValue({ fields: {}, meta: {} });
  m.recommendation.mockResolvedValue({
    outcome: "approve",
    rationale: "Covered under STD-2.1.",
    cited_clause_ids: [],
    cited_clauses: [],
    fraud_flags: [],
    meta: {},
  });
  m.audit.mockResolvedValue([]);
});

describe("ClaimPage review modes", () => {
  it("manual mode never requests the AI's output and shows the policy instead", async () => {
    m.startReview.mockResolvedValue({ id: "s1", claim_id: "c1", mode: "manual", started_at: "" });
    open();
    expect(await screen.findByText(/Manual review/)).toBeInTheDocument();
    expect(await screen.findByText("We cover collision loss.")).toBeInTheDocument();
    expect(m.extraction).not.toHaveBeenCalled();
    expect(m.recommendation).not.toHaveBeenCalled();
    expect(m.audit).not.toHaveBeenCalled();
    expect(screen.queryByText("AI recommendation")).not.toBeInTheDocument();
    expect(screen.queryByText(/AI recommends/)).not.toBeInTheDocument();
  });

  it("assisted mode shows the recommendation and starts the review clock once", async () => {
    m.startReview.mockResolvedValue({ id: "s1", claim_id: "c1", mode: "assisted", started_at: "" });
    open();
    expect(await screen.findByText("AI recommendation")).toBeInTheDocument();
    expect(m.startReview).toHaveBeenCalledTimes(1);
    expect(m.startReview).toHaveBeenCalledWith("c1");
  });

  it("does not start a review for a claim that is already decided", async () => {
    m.getClaim.mockResolvedValue({ ...claim, status: "APPROVED" });
    m.decision.mockResolvedValue({
      outcome: "APPROVED",
      overridden: false,
      reason_code: null,
      note: null,
      created_at: "2026-01-02T00:00:00Z",
    });
    open();
    expect(await screen.findByText("Decision recorded")).toBeInTheDocument();
    expect(m.startReview).not.toHaveBeenCalled();
  });
});
