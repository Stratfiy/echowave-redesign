/**
 * The Calls section inside Settings -> Voice and language: nothing while
 * both call switches are off; with them on, the honest setup state of "call
 * it for me", the booking policy (off until granted) and the bookings.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const flags = vi.hoisted(() => ({ on: new Set<string>() }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => flags.on.has(name) }));
vi.mock("@/client/sdk.gen", () => ({
    voiceReadinessApiV1VoiceReadinessGet: vi.fn(async () => ({
        data: { calls: { state: "needs_setup", reason: "No phone line is connected, so Decibyl cannot place a call.", next_step: null, notes: [] } },
    })),
    appointmentPolicyApiV1VoiceAppointmentsPolicyGet: vi.fn(async () => ({
        data: { booking: "off", duration_minutes: 30, lead_minutes: 60, horizon_days: 14, services: [], verification: "details", escalate_to: null, call_workflow_id: null, revision: 0, updated_at: null },
    })),
    getWorkflowsApiV1WorkflowFetchGet: vi.fn(async () => ({ data: [{ id: 7, name: "Front desk" }] })),
    upcomingAppointmentsApiV1VoiceAppointmentsGet: vi.fn(async () => ({ data: [] })),
    saveAppointmentPolicyApiV1VoiceAppointmentsPolicyPut: vi.fn(),
}));

import { VoiceCallSettings } from "../VoiceCallSettings";

beforeEach(() => flags.on.clear());

describe("Calls in Voice and language", () => {
    it("is absent while both call switches are off", () => {
        const { container } = render(<VoiceCallSettings />);
        expect(container.innerHTML).toBe("");
    });

    it("shows the setup state, the policy off until granted, and no bookings yet", async () => {
        flags.on.add("call_for_me");
        flags.on.add("call_appointment");
        render(<VoiceCallSettings />);
        expect(await screen.findByText(/No phone line is connected/)).toBeTruthy();
        expect(await screen.findByText("Nothing booked yet.")).toBeTruthy();
        expect(screen.getAllByRole("radio").map((r) => (r as HTMLInputElement).checked)).toEqual([true, false, false]);
        expect(screen.getByRole("option", { name: "Front desk" })).toBeTruthy();
    });
});
