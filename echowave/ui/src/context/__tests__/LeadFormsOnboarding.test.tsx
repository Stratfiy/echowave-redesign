/**
 * With first_task_onboarding on, screen 02 is the door: the older three
 * questions (role, business, how you heard) must not open over Chat as a
 * second compulsory form. Off, a brand-new account still sees them.
 */
import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ flags: {} as Record<string, boolean>, count: vi.fn() }));
vi.mock("posthog-js", () => ({ default: { capture: vi.fn() } }));
vi.mock("@/client/sdk.gen", () => ({ getWorkflowCountApiV1WorkflowCountGet: state.count }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/context/OnboardingContext", () => ({
    useOnboarding: () => ({ loading: false, onboardingCompletedAt: null, onboardingSkipped: false, markOnboardingCompleted: vi.fn() }),
}));
vi.mock("@/context/AppConfigContext", () => ({ useAppConfig: () => ({ loading: false, config: {} }) }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(state.flags[name]) }));
vi.mock("@/components/lead-forms/OnboardingModal", () => ({
    OnboardingModal: ({ open }: { open: boolean }) => (open ? <div>door questions</div> : null),
}));
vi.mock("@/components/lead-forms/HireExpertModal", () => ({ HireExpertModal: () => null }));
vi.mock("@/components/lead-forms/EnterpriseModal", () => ({ EnterpriseModal: () => null }));

import { LeadFormsProvider } from "../LeadFormsContext";

beforeEach(() => {
    state.flags = {};
    state.count.mockReset();
    state.count.mockResolvedValue({ data: { total: 0, active: 0 } });
});

describe("the older door questions", () => {
    it("still open for a brand-new account with the flag off", async () => {
        render(<LeadFormsProvider>page</LeadFormsProvider>);
        expect(await screen.findByText("door questions")).toBeTruthy();
    });

    it("stay closed while first_task_onboarding is on", async () => {
        state.flags = { first_task_onboarding: true };
        render(<LeadFormsProvider>page</LeadFormsProvider>);
        await waitFor(() => expect(screen.getByText("page")).toBeTruthy());
        await new Promise((r) => setTimeout(r, 20));
        expect(screen.queryByText("door questions")).toBeNull();
        expect(state.count).not.toHaveBeenCalled();
    });
});
