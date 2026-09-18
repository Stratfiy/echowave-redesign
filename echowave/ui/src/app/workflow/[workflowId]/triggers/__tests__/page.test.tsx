/**
 * Seen live: an Email trigger's "Work it out" asked which email service the
 * webhook comes from, because the compiler was never told it was compiling
 * an email. The page now sends the source it is showing.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const compile = vi.hoisted(() => vi.fn());
const list = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    compileTriggerApiV1WorkflowsWorkflowIdTriggersCompilePost: compile,
    createTriggerApiV1WorkflowsWorkflowIdTriggersPost: vi.fn(),
    deleteTriggerApiV1WorkflowsWorkflowIdTriggersTriggerIdDelete: vi.fn(),
    getWorkflowApiV1WorkflowFetchWorkflowIdGet: vi.fn().mockResolvedValue({ data: { name: "Front desk" } }),
    listTriggersApiV1WorkflowsWorkflowIdTriggersGet: list,
    rotateSecretApiV1WorkflowsWorkflowIdTriggersTriggerIdRotateSecretPost: vi.fn(),
    setActiveApiV1WorkflowsWorkflowIdTriggersTriggerIdActivePost: vi.fn(),
    testTriggerApiV1WorkflowsWorkflowIdTriggersTriggerIdTestPost: vi.fn(),
}));
vi.mock("next/navigation", () => ({
    useParams: () => ({ workflowId: "7" }),
    useRouter: () => ({ push: vi.fn() }),
    usePathname: () => "/workflow/7/triggers",
    useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/components/workflow/BotNotices", () => ({ BotNotices: () => <div /> }));
vi.mock("@/components/workflow/EventWebhookPanel", () => ({ EventWebhookPanel: () => <div /> }));
vi.mock("@/components/workflow/RoutinesPanel", () => ({ RoutinesPanel: () => <div /> }));

import TriggersPage from "../page";

beforeEach(() => {
    vi.clearAllMocks();
    list.mockResolvedValue({ data: { triggers: [] } });
    compile.mockResolvedValue({
        data: { name: "Appointment mail", instruction: "Reply.", fields: [], filter: [], questions: [], ready: true },
    });
});

describe("working out a trigger", () => {
    it("tells the compiler it is an email when the Email kind is chosen", async () => {
        render(<TriggersPage />);
        await waitFor(() => expect(screen.getByRole("button", { name: "Email" })).toBeTruthy());
        fireEvent.click(screen.getByRole("button", { name: "Email" }));
        const box = document.querySelector("textarea") as HTMLTextAreaElement;
        fireEvent.change(box, { target: { value: "When a customer emails about an appointment, reply with our hours" } });
        fireEvent.click(screen.getByRole("button", { name: /Work it out/i }));
        await waitFor(() => expect(compile).toHaveBeenCalled());
        expect(compile.mock.calls[0][0].body.source).toBe("email");
    });

    it("defaults to a webhook", async () => {
        render(<TriggersPage />);
        await waitFor(() => expect(screen.getByRole("button", { name: /Work it out/i })).toBeTruthy());
        const box = document.querySelector("textarea") as HTMLTextAreaElement;
        fireEvent.change(box, { target: { value: "When a Shopify order comes in, check stock" } });
        fireEvent.click(screen.getByRole("button", { name: /Work it out/i }));
        await waitFor(() => expect(compile).toHaveBeenCalled());
        expect(compile.mock.calls[0][0].body.source).toBe("webhook");
    });
});
