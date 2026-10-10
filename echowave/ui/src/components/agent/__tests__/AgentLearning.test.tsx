import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  summary: vi.fn(),
  setting: vi.fn(),
  feature: { on: true },
  toastError: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: () => api.feature.on }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: api.toastError } }));
vi.mock("@/client/sdk.gen", () => ({
  agentSummaryApiV1TrainingLoopAgentsWorkflowIdSummaryGet: api.summary,
  setSettingsApiV1TrainingLoopSettingsPut: api.setting,
}));

import { AgentLearning, learningLine } from "../AgentLearning";

const counts = (approved: number, rejected: number, use_feedback = true) => ({
  data: { approved_this_week: approved, rejected_this_week: rejected, use_feedback },
});

afterEach(() => {
  cleanup();
  api.feature.on = true;
  Object.values(api).forEach((fn) => typeof fn === "function" && "mockReset" in fn && fn.mockReset());
});

describe("the Learning line", () => {
  it("says how many suggestions were approved and rejected this week", async () => {
    api.summary.mockResolvedValue(counts(5, 2));
    render(<AgentLearning workflowId={7} />);
    expect((await screen.findByTestId("learning-line")).textContent).toBe("5 suggestions approved, 2 rejected this week");
    expect(api.summary.mock.calls[0][0]).toEqual({ path: { workflow_id: 7 } });
  });

  it("reads naturally for one and for none", () => {
    expect(learningLine(1, 0)).toBe("1 suggestion approved, 0 rejected this week");
    expect(learningLine(0, 0)).toBe("No suggestions answered yet this week");
  });

  it("shows no prices", async () => {
    api.summary.mockResolvedValue(counts(5, 2));
    render(<AgentLearning workflowId={7} />);
    const section = await screen.findByTestId("agent-learning");
    expect(section.textContent).not.toMatch(/₹|\$|rupee|price|cost/i);
  });

  it("is not there while the feature is off, and asks for nothing", () => {
    api.feature.on = false;
    render(<AgentLearning workflowId={7} />);
    expect(screen.queryByTestId("agent-learning")).toBeNull();
    expect(api.summary).not.toHaveBeenCalled();
  });

  it("shows nothing when the counts cannot be read", async () => {
    api.summary.mockResolvedValue({ error: { detail: "nope" } });
    render(<AgentLearning workflowId={7} />);
    await waitFor(() => expect(api.summary).toHaveBeenCalled());
    expect(screen.queryByTestId("agent-learning")).toBeNull();
  });
});

describe("the consent switch", () => {
  it("shows the workspace's setting and turns it off", async () => {
    api.summary.mockResolvedValue(counts(0, 0, true));
    api.setting.mockResolvedValue({ data: { use_feedback: false } });
    render(<AgentLearning workflowId={7} />);
    const toggle = await screen.findByRole("switch", { name: "Use my feedback to improve my agents" });
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(toggle);
    // Off is a choice, never a default: nothing is sent until one is picked.
    expect(api.setting).not.toHaveBeenCalled();
    expect(screen.getByTestId("learning-choice")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stop collecting" }));
    await waitFor(() =>
      expect(api.setting).toHaveBeenCalledWith({ body: { use_feedback: false, delete_past: false } }),
    );
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("false"));
    expect(screen.queryByTestId("learning-choice")).toBeNull();
  });

  it("archives what was kept on Stop and delete", async () => {
    api.summary.mockResolvedValue(counts(0, 0, true));
    api.setting.mockResolvedValue({ data: { use_feedback: false, archived: 4 } });
    render(<AgentLearning workflowId={7} />);
    fireEvent.click(await screen.findByRole("switch"));
    fireEvent.click(screen.getByRole("button", { name: "Stop and delete" }));
    await waitFor(() =>
      expect(api.setting).toHaveBeenCalledWith({ body: { use_feedback: false, delete_past: true } }),
    );
    expect((await screen.findByTestId("learning-off")).textContent).toContain("4 kept earlier are archived");
  });

  it("can be cancelled without changing anything", async () => {
    api.summary.mockResolvedValue(counts(0, 0, true));
    render(<AgentLearning workflowId={7} />);
    const toggle = await screen.findByRole("switch");
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.setting).not.toHaveBeenCalled();
    expect(toggle.getAttribute("aria-checked")).toBe("true");
  });

  it("turns back on in one click", async () => {
    api.summary.mockResolvedValue(counts(0, 0, false));
    api.setting.mockResolvedValue({ data: { use_feedback: true, archived: 0 } });
    render(<AgentLearning workflowId={7} />);
    fireEvent.click(await screen.findByRole("switch"));
    await waitFor(() => expect(api.setting).toHaveBeenCalledWith({ body: { use_feedback: true } }));
  });

  it("says what each way of turning it off does", async () => {
    api.summary.mockResolvedValue(counts(0, 0));
    render(<AgentLearning workflowId={7} />);
    expect((await screen.findByTestId("agent-learning")).textContent).toContain("never shared");
    fireEvent.click(screen.getByRole("switch"));
    const text = screen.getByTestId("learning-choice").textContent ?? "";
    expect(text).toContain("keeps what was kept");
    expect(text).toContain("archives what was kept");
    expect(text).toContain("law requires");
  });

  it("stays as it was, and says why, when someone who cannot change it tries", async () => {
    api.summary.mockResolvedValue(counts(0, 0, true));
    api.setting.mockResolvedValue({ error: { detail: "Access denied. Admin role required in this organization." } });
    render(<AgentLearning workflowId={7} />);
    const toggle = await screen.findByRole("switch");
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Stop collecting" }));
    await waitFor(() => expect(api.toastError).toHaveBeenCalled());
    expect(api.toastError.mock.calls[0][0]).toContain("Admin role required");
    expect(toggle.getAttribute("aria-checked")).toBe("true");
  });
});
