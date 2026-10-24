import { render, screen } from "@testing-library/react";
import ExtractionPanel from "./ExtractionPanel";

describe("ExtractionPanel", () => {
  it("marks fields that were read from a scan and unverified ones", () => {
    render(
      <ExtractionPanel
        extraction={{
          fields: { policy_number: "POL-STD-123456", total_amount: "7050.00", incident_date: null },
          meta: { vision_sourced: ["policy_number"], unverified_fields: ["total_amount"] },
        }}
      />,
    );
    expect(screen.getByText("read from scan")).toBeInTheDocument();
    expect(screen.getByText("unverified")).toBeInTheDocument();
    expect(screen.getByText("$7,050.00")).toBeInTheDocument();
  });

  it("shows why extraction needs review", () => {
    render(
      <ExtractionPanel
        extraction={{ fields: {}, meta: { needs_review: true, issues: ["policy_number is missing"] } }}
      />,
    );
    expect(screen.getByRole("note")).toHaveTextContent("policy_number is missing");
  });
});
