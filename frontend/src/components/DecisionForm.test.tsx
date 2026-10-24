import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { api } from "../api";
import DecisionForm from "./DecisionForm";

vi.mock("../api", () => ({ api: { decide: vi.fn() } }));
const decide = vi.mocked(api.decide);

function setup(visionFields: string[] = []) {
  const onDecided = vi.fn();
  render(
    <DecisionForm
      claimId="c1"
      recommendation="approve"
      visionFields={visionFields}
      onDecided={onDecided}
    />,
  );
  return { onDecided, user: userEvent.setup() };
}

const submit = () => screen.getByRole("button", { name: /record decision|override/i });

beforeEach(() => {
  decide.mockReset();
  decide.mockResolvedValue({} as never);
});

describe("DecisionForm", () => {
  it("cannot be submitted until an outcome is chosen", () => {
    setup();
    expect(submit()).toBeDisabled();
  });

  it("accepts the recommendation without a reason", async () => {
    const { user, onDecided } = setup();
    await user.click(screen.getByLabelText(/approve/i));
    expect(screen.queryByLabelText(/overriding/i)).not.toBeInTheDocument();
    await user.click(submit());
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    expect(decide).toHaveBeenCalledWith("c1", {
      outcome: "APPROVED",
      reason_code: undefined,
      note: undefined,
      confirmed_fields: [],
    });
  });

  it("makes an override carry a reason code", async () => {
    const { user } = setup();
    await user.click(screen.getByLabelText(/^deny/i));
    expect(submit()).toBeDisabled();
    await user.selectOptions(screen.getByLabelText(/overriding/i), "wrong_clause");
    expect(submit()).toBeEnabled();
    await user.click(submit());
    await waitFor(() => expect(decide).toHaveBeenCalled());
    expect(decide.mock.calls[0][1]).toMatchObject({ outcome: "DENIED", reason_code: "wrong_clause" });
  });

  it("holds the decision until scan-read fields are confirmed, and reports them", async () => {
    const { user } = setup(["policy_number"]);
    await user.click(screen.getByLabelText(/approve/i));
    expect(submit()).toBeDisabled();
    await user.click(screen.getByLabelText(/policy number matches/i));
    expect(submit()).toBeEnabled();
    await user.click(submit());
    await waitFor(() => expect(decide).toHaveBeenCalled());
    expect(decide.mock.calls[0][1].confirmed_fields).toEqual(["policy_number"]);
  });

  it("shows a server rejection instead of swallowing it", async () => {
    decide.mockRejectedValue(new Error("Claim is already decided"));
    const { user, onDecided } = setup();
    await user.click(screen.getByLabelText(/approve/i));
    await user.click(submit());
    expect(await screen.findByRole("alert")).toHaveTextContent("already decided");
    expect(onDecided).not.toHaveBeenCalled();
  });
});
