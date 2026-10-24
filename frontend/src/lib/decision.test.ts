import { blockers, fieldsToConfirm, isOverride, recommendedOutcome } from "./decision";
import type { DecisionDraft } from "./decision";

const draft = (over: Partial<DecisionDraft> = {}): DecisionDraft => ({
  choice: "APPROVED",
  reason: "",
  note: "",
  confirmed: new Set(),
  ...over,
});

describe("decision rules", () => {
  it("maps recommendations onto outcomes", () => {
    expect(recommendedOutcome("approve")).toBe("APPROVED");
    expect(recommendedOutcome("deny")).toBe("DENIED");
    expect(recommendedOutcome("escalate")).toBe("ESCALATED");
    expect(recommendedOutcome(undefined)).toBeNull();
  });

  it("is an override only when the choice differs from a recommendation", () => {
    expect(isOverride("DENIED", "approve")).toBe(true);
    expect(isOverride("APPROVED", "approve")).toBe(false);
    expect(isOverride("DENIED", undefined)).toBe(false);
    expect(isOverride(null, "approve")).toBe(false);
  });

  it("accepting the recommendation needs no reason", () => {
    expect(blockers(draft(), "approve", [])).toEqual([]);
  });

  it("requires an outcome", () => {
    expect(blockers(draft({ choice: null }), "approve", [])).toContain("Choose an outcome.");
  });

  it("requires a reason to override, matching the server", () => {
    expect(blockers(draft({ choice: "DENIED" }), "approve", [])).toContain(
      "Pick a reason for overriding.",
    );
    expect(blockers(draft({ choice: "DENIED", reason: "wrong_clause" }), "approve", [])).toEqual([]);
  });

  it("needs no reason when there was no recommendation", () => {
    expect(blockers(draft({ choice: "DENIED" }), undefined, [])).toEqual([]);
  });

  it("requires a note when the reason is 'other'", () => {
    const d = draft({ choice: "DENIED", reason: "other" });
    expect(blockers(d, "approve", [])).toContain("Explain “other” in the note.");
    expect(blockers({ ...d, note: "Policy rider applies" }, "approve", [])).toEqual([]);
  });

  it("blocks until every scan-read field is confirmed", () => {
    const fields = ["policy_number", "incident_date"];
    expect(blockers(draft(), "approve", fields)[0]).toMatch(/Policy number, Incident date/);
    const some = draft({ confirmed: new Set(["policy_number"]) });
    expect(blockers(some, "approve", fields)[0]).toMatch(/Incident date/);
    expect(blockers(draft({ confirmed: new Set(fields) }), "approve", fields)).toEqual([]);
  });
});

describe("fieldsToConfirm", () => {
  it("only asks about scan-read fields that drive the checks", () => {
    expect(fieldsToConfirm(["claimant_name", "policy_number", "total_amount", "vehicle_year"])).toEqual([
      "policy_number",
      "total_amount",
    ]);
    expect(fieldsToConfirm(undefined)).toEqual([]);
  });
});
