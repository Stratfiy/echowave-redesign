/**
 * The first-agent journey, end to end, with the network faked.
 *
 * What matters here is the funnel: that each step reports itself to PostHog
 * with the properties the dashboard reads, that the answers typed on the name
 * step reach the create request, and that the flow lands on a screen with the
 * agent's next steps on it. The voice tester itself is replaced by a button —
 * it needs a microphone and a WebSocket, and neither is what this test is
 * about.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FirstAgentJourney } from "../FirstAgentJourney";

const push = vi.fn();
const capture = vi.hoisted(() => vi.fn());
const api = vi.hoisted(() => ({
    get: vi.fn(),
    post: vi.fn(),
    createRun: vi.fn(),
    listNumbers: vi.fn(),
    initiateCall: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => "/start" }));
vi.mock("posthog-js", () => ({ default: { capture } }));
vi.mock("@/lib/auth", () => ({
    useAuth: () => ({ user: { id: 1, displayName: "Nithish K" }, getAccessToken: () => Promise.resolve("token") }),
}));
vi.mock("@/client/client.gen", () => ({ client: { get: api.get, post: api.post } }));
vi.mock("@/client/sdk.gen", () => ({
    createWorkflowRunApiV1WorkflowWorkflowIdRunsPost: api.createRun,
    listNumbersApiV1VerifiedNumbersGet: api.listNumbers,
    initiateCallApiV1TelephonyInitiateCallPost: api.initiateCall,
}));
vi.mock("@/app/workflow/[workflowId]/components/workflow-tester/EmbeddedVoiceTester", () => ({
    EmbeddedVoiceTester: ({ onCompleted }: { onCompleted: () => void }) => (
        <button type="button" onClick={onCompleted}>
            hang up
        </button>
    ),
}));

const TEMPLATES = [
    {
        id: "clinic_appointment",
        name: "Clinic front desk",
        vertical: "Healthcare — clinics",
        direction: "inbound",
        summary: "Answers the clinic's phone.",
        languages: ["English", "Hindi"],
        variables: [{ name: "clinic_name", asks_for: "Name of the clinic" }],
        greeting: "Namaste, {{clinic_name}}.",
    },
    {
        id: "ecom_cod_confirmation",
        name: "COD order confirmation",
        vertical: "E-commerce — D2C",
        direction: "outbound",
        summary: "Confirms the order.",
        languages: ["English"],
        variables: [],
        greeting: "Hello.",
    },
];

beforeEach(() => {
    sessionStorage.clear();
    capture.mockClear();
    push.mockClear();
    api.get.mockResolvedValue({ data: { templates: TEMPLATES } });
    api.post.mockResolvedValue({ data: { id: 42 } });
    api.createRun.mockResolvedValue({ data: { id: 7 } });
    api.listNumbers.mockResolvedValue({ data: [] });
    api.initiateCall.mockResolvedValue({ data: { workflow_run_id: 9 } });
});

const events = () => capture.mock.calls.map(([name]) => name as string);

describe("first-agent journey", () => {
    it("goes template → name → hear → ready, reporting every step", async () => {
        render(<FirstAgentJourney />);
        expect(await screen.findByText("Clinic front desk")).toBeTruthy();
        expect(screen.getByText("Hi Nithish, let's build your first agent.")).toBeTruthy();
        expect(events()).toContain("first_agent_started");

        // Nothing chosen yet: continue is off.
        const continueButton = screen.getByRole("button", { name: /continue/i });
        expect((continueButton as HTMLButtonElement).disabled).toBe(true);

        fireEvent.click(screen.getByRole("radio", { name: /Clinic front desk/ }));
        fireEvent.click(screen.getByRole("button", { name: "female" }));
        fireEvent.click(screen.getByRole("button", { name: /continue/i }));

        // Name step asks the template's own question and shows its greeting.
        const clinic = await screen.findByLabelText("Name of the clinic");
        fireEvent.change(clinic, { target: { value: "City Clinic" } });
        fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "Asha" } });
        expect((screen.getByLabelText("Opening line") as HTMLTextAreaElement).value).toBe(
            "Namaste, {{clinic_name}}.",
        );
        fireEvent.click(screen.getByRole("button", { name: /continue/i }));

        await waitFor(() => expect(api.post).toHaveBeenCalled());
        expect(api.post.mock.calls[0][0]).toMatchObject({
            url: "/api/v1/agent-templates/clinic_appointment/create",
            body: {
                voice_gender: "female",
                agent_name: "Asha",
                variables: { clinic_name: "City Clinic" },
                source: "first_agent",
            },
        });
        // An untouched greeting is not sent: the template's own is kept.
        expect(api.post.mock.calls[0][0].body.greeting).toBeUndefined();

        // Hear step: browser call by default.
        expect(await screen.findByText("Now hear it.")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: /start test call/i }));
        expect(await screen.findByText("Asha is on the line.")).toBeTruthy();
        expect(api.createRun).toHaveBeenCalledWith(
            expect.objectContaining({ path: { workflow_id: 42 } }),
        );

        fireEvent.click(screen.getByRole("button", { name: "hang up" }));
        expect(await screen.findByText("Asha took its first call.")).toBeTruthy();
        expect(screen.getByRole("link", { name: /get a phone number/i }).getAttribute("href")).toBe("/numbers");
        expect(screen.getByRole("link", { name: /recording/i }).getAttribute("href")).toBe("/workflow/42/run/7");

        expect(events()).toEqual(
            expect.arrayContaining([
                "first_agent_template_picked",
                "first_agent_voice_picked",
                "first_agent_created",
                "web_call_initiated",
                "first_agent_test_started",
                "first_agent_test_completed",
            ]),
        );
        const created = capture.mock.calls.find(([name]) => name === "first_agent_created")?.[1];
        expect(created).toMatchObject({ workflow_id: 42, template_id: "clinic_appointment", renamed: true, variables_answered: 1 });
    });

    it("offers verification when no number is verified, and rings one when there is", async () => {
        sessionStorage.setItem(
            "decibyl.firstAgent",
            JSON.stringify({ step: "hear", templateId: "clinic_appointment", voice: null, agentName: "Asha", workflowId: 42, runId: null }),
        );
        api.listNumbers.mockResolvedValue({ data: [{ phone_number: "+919999999999", status: "verified" }, { phone_number: "+918888888888", status: "pending" }] });
        render(<FirstAgentJourney />);
        expect(await screen.findByText("Now hear it.")).toBeTruthy();
        fireEvent.click(screen.getByRole("radio", { name: /call my phone/i }));
        // Only the verified one is offered.
        expect(screen.queryByText(/8888888888/)).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: /call me/i }));
        await waitFor(() => expect(api.initiateCall).toHaveBeenCalled());
        expect(api.initiateCall.mock.calls[0][0].body).toEqual({ workflow_id: 42, phone_number: "+919999999999" });
        expect(await screen.findByText("Ringing +919999999999")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: /i took the call/i }));
        expect(await screen.findByText("Asha took its first call.")).toBeTruthy();
        expect(screen.getByRole("link", { name: /recording/i }).getAttribute("href")).toBe("/workflow/42/run/9");
    });

    it("links to verification when nothing is verified", async () => {
        sessionStorage.setItem(
            "decibyl.firstAgent",
            JSON.stringify({ step: "hear", templateId: "clinic_appointment", voice: null, agentName: "", workflowId: 42, runId: null }),
        );
        render(<FirstAgentJourney />);
        expect(await screen.findByText("Now hear it.")).toBeTruthy();
        fireEvent.click(screen.getByRole("radio", { name: /call my phone/i }));
        expect(screen.getByRole("link", { name: /verify my number/i }).getAttribute("href")).toBe("/verified-numbers?next=/start");
    });

    it("reports a create failure and stays on the name step", async () => {
        api.post.mockResolvedValue({ error: { detail: "No organization selected" } });
        render(<FirstAgentJourney />);
        fireEvent.click(await screen.findByRole("radio", { name: /COD order/ }));
        fireEvent.click(screen.getByRole("button", { name: /continue/i }));
        fireEvent.click(await screen.findByRole("button", { name: /continue/i }));
        expect(await screen.findByRole("alert")).toBeTruthy();
        expect(events()).toContain("first_agent_create_failed");
        expect(screen.getByLabelText("Agent name")).toBeTruthy();
    });

    it("sends people who skip to the wizard", async () => {
        render(<FirstAgentJourney />);
        await screen.findByText("Clinic front desk");
        fireEvent.click(screen.getByRole("button", { name: /start from scratch/i }));
        expect(push).toHaveBeenCalledWith("/workflow/create");
        expect(events()).toContain("first_agent_scratch_chosen");
    });
});
