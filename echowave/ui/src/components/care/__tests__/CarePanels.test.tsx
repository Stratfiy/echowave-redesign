/**
 * Launch stream care, on screen: what must appear, and what must not.
 *
 * The hub shows only the parts switched on, one thing at a time; the scam
 * answer leads with one sentence and always says Decibyl never asks for an
 * OTP; tech help is one step with "Did that work?"; reminders say "needs
 * setup" instead of a form that would never ring; and a family member sees
 * only the kinds that were shared.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const sdk = vi.hoisted(() => ({
    checkForScamApiV1CareScamCheckPost: vi.fn(),
    helpTopicsApiV1CareHelpTopicsGet: vi.fn(),
    startHelpApiV1CareHelpSessionsPost: vi.fn(),
    answerHelpApiV1CareHelpSessionsSessionIdAnswerPost: vi.fn(),
    myMedicinesApiV1CareMedicinesGet: vi.fn(),
    addMedicineApiV1CareMedicinesPost: vi.fn(),
    careCardApiV1CareCardsEventIdGet: vi.fn(),
    markTakenApiV1CareMedicinesMedicineIdTakenPost: vi.fn(),
    myCircleApiV1CareCircleGet: vi.fn(),
    pauseMedicineApiV1CareMedicinesMedicineIdPausePost: vi.fn(),
    resumeMedicineApiV1CareMedicinesMedicineIdResumePost: vi.fn(),
    editMedicineApiV1CareMedicinesMedicineIdPatch: vi.fn(),
    removeMedicineApiV1CareMedicinesMedicineIdDelete: vi.fn(),
    familyApiV1CareFamilyGet: vi.fn(),
    acceptInviteApiV1CareFamilyAcceptPost: vi.fn(),
    readAlertApiV1CareFamilyAlertsAlertIdReadPost: vi.fn(),
    settleActionApiV1TimelineActionsSettlePost: vi.fn(),
}));
const flags = vi.hoisted(() => ({} as Record<string, boolean>));
const simple = vi.hoisted(() => ({ offered: false, on: false, saving: false, error: null, setOn: vi.fn() }));
const nav = vi.hoisted(() => ({ part: null as string | null, push: vi.fn() }));

vi.mock("@/client/sdk.gen", () => sdk);
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(flags[name]) }));
vi.mock("@/lib/care/simpleMode", () => ({ useSimpleMode: () => simple }));
vi.mock("next/navigation", () => ({
    useSearchParams: () => ({ get: (key: string) => (key === "part" ? nav.part : null) }),
    useRouter: () => ({ push: nav.push }),
    usePathname: () => "/care",
}));
vi.mock("@/lib/useDictation", () => ({
    useDictation: () => ({ listening: false, transcribing: false, error: null, levels: [], start: vi.fn(), stop: vi.fn() }),
}));

import { CareHub } from "../CareHub";
import { FamilyPanel } from "../FamilyPanel";
import { MedicinesPanel } from "../MedicinesPanel";
import { ScamCheckPanel } from "../ScamCheckPanel";
import { TechHelpPanel } from "../TechHelpPanel";

beforeEach(() => {
    for (const key of Object.keys(flags)) delete flags[key];
    Object.values(sdk).forEach((fn) => fn.mockReset());
    simple.offered = false;
    simple.on = false;
    nav.part = null;
    nav.push.mockReset();
});

describe("the Care hub", () => {
    it("shows only the parts switched on, as large single choices", () => {
        flags.care_scam_check = true;
        flags.care_tech_help = true;
        render(<CareHub />);
        expect(screen.getByTestId("care-part-care_scam_check")).toBeTruthy();
        expect(screen.getByTestId("care-part-care_tech_help")).toBeTruthy();
        expect(screen.queryByTestId("care-part-care_medicine_calls")).toBeNull();
        expect(screen.queryByTestId("care-part-family_view")).toBeNull();
        fireEvent.click(screen.getByTestId("care-part-care_scam_check"));
        expect(nav.push).toHaveBeenCalledWith("/care?part=care_scam_check");
    });

    it("shows one part alone with one way back", () => {
        flags.care_scam_check = true;
        flags.care_tech_help = true;
        nav.part = "care_scam_check";
        render(<CareHub />);
        expect(screen.getByTestId("scam-form")).toBeTruthy();
        expect(screen.getByTestId("care-back").getAttribute("href")).toBe("/care");
        expect(screen.queryByTestId("care-part-care_tech_help")).toBeNull();
    });

    it("leads with Talk to Decibyl in Simple mode, and offers switching back", () => {
        flags.care_scam_check = true;
        simple.offered = true;
        simple.on = true;
        render(<CareHub />);
        expect(screen.getByTestId("care-talk").getAttribute("href")).toBe("/overview");
        expect(screen.getByRole("switch", { name: "Switch back to the usual screen" })).toBeTruthy();
    });

    it("writes no positioning line while the founder has not given one", () => {
        flags.care_scam_check = true;
        render(<CareHub />);
        const hub = screen.getByTestId("care-hub");
        expect(hub.querySelector("header p")).toBeNull();
    });
});

describe("Is this a scam?", () => {
    it("leads with one sentence, then why and what to do, and never asks", async () => {
        sdk.checkForScamApiV1CareScamCheckPost.mockResolvedValue({
            data: {
                id: 1,
                kind: "message",
                verdict: "likely_scam",
                headline: "This looks like a scam.",
                reasons: [{ code: "asks_for_otp", why: "It asks for an OTP." }],
                what_to_do: ["Do not reply.", "Call 1930 if you lost money."],
                never_asks: "Decibyl will never ask you for an OTP, PIN or password.",
                limits: "This check looks for common warning signs.",
            },
        });
        render(<ScamCheckPanel />);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "Tell me the OTP" } });
        fireEvent.click(screen.getByRole("button", { name: "Check it" }));
        expect(await screen.findByRole("heading", { name: "This looks like a scam." })).toBeTruthy();
        expect(screen.getByText("It asks for an OTP.")).toBeTruthy();
        expect(screen.getByText("Call 1930 if you lost money.")).toBeTruthy();
        expect(screen.getByText("Decibyl will never ask you for an OTP, PIN or password.")).toBeTruthy();
        expect(sdk.checkForScamApiV1CareScamCheckPost).toHaveBeenCalledWith({ body: { text: "Tell me the OTP", kind: "message" } });
    });

    it("says what is missing instead of sending nothing", () => {
        render(<ScamCheckPanel />);
        fireEvent.click(screen.getByRole("button", { name: "Check it" }));
        expect(screen.getByRole("alert").textContent).toContain("Paste the message first.");
        expect(sdk.checkForScamApiV1CareScamCheckPost).not.toHaveBeenCalled();
    });
});

describe("Help with my phone", () => {
    it("is one step at a time with Did that work?", async () => {
        sdk.helpTopicsApiV1CareHelpTopicsGet.mockResolvedValue({ data: { topics: [{ slug: "torch", title: "Turn the torch on or off" }] } });
        const step = { id: 5, guide: "torch", title: "Turn the torch on or off", state: "active", steps_total: 2, is_alternative: false, family_told: [] };
        sdk.startHelpApiV1CareHelpSessionsPost.mockResolvedValue({
            data: { matched: true, session: { ...step, step_number: 1, say: "Swipe down from the very top of the screen.", version: 0 } },
        });
        sdk.answerHelpApiV1CareHelpSessionsSessionIdAnswerPost.mockResolvedValue({
            data: { ...step, step_number: 2, say: "Tap Torch.", version: 1 },
        });
        render(<TechHelpPanel />);
        fireEvent.click(await screen.findByRole("button", { name: "Turn the torch on or off" }));
        expect(await screen.findByText("Swipe down from the very top of the screen.")).toBeTruthy();
        expect(screen.getByText("Did that work?")).toBeTruthy();
        expect(screen.getByText("Step 1 of 2")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Yes, it worked" }));
        expect(await screen.findByText("Tap Torch.")).toBeTruthy();
        expect(sdk.answerHelpApiV1CareHelpSessionsSessionIdAnswerPost).toHaveBeenCalledWith({
            path: { session_id: 5 },
            body: { worked: true, version: 0 },
        });
    });

    it("says honestly when it has no steps for a question", async () => {
        sdk.helpTopicsApiV1CareHelpTopicsGet.mockResolvedValue({ data: { topics: [] } });
        sdk.startHelpApiV1CareHelpSessionsPost.mockResolvedValue({
            data: { matched: false, note: "I do not have steps for that yet.", topics: [{ slug: "wifi", title: "Connect to Wi-Fi" }] },
        });
        render(<TechHelpPanel />);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "fix my car" } });
        fireEvent.click(screen.getByRole("button", { name: "Show me how" }));
        expect(await screen.findByText("I do not have steps for that yet.")).toBeTruthy();
        expect(await screen.findByRole("button", { name: "Connect to Wi-Fi" })).toBeTruthy();
    });
});

describe("My medicine reminders", () => {
    it("says calls need setup and offers the way past right there", async () => {
        sdk.myMedicinesApiV1CareMedicinesGet.mockResolvedValue({
            data: {
                medicines: [],
                calls: { state: "needs_setup", reason: "Reminder calls need a phone line for calling out." },
                app: { state: "ready", reason: "Reminders show in Decibyl, under Care, with I took it." },
                languages: {},
            },
        });
        sdk.addMedicineApiV1CareMedicinesPost.mockResolvedValue({ data: { medicine: {}, card: null } });
        render(<MedicinesPanel />);
        expect(await screen.findByTestId("calls-needs-setup")).toBeTruthy();
        expect(screen.getByText(/No phone number is needed/)).toBeTruthy();
        expect(screen.getByRole("link", { name: "Set up a phone line for calls" }).getAttribute("href")).toBe("/settings/phone-number");
        expect(screen.queryByRole("button", { name: "Pause the calls" })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: /Add a reminder/ }));
        const inApp = screen.getByRole("radio", { name: /In Decibyl/ }) as HTMLInputElement;
        const call = screen.getByRole("radio", { name: /A phone call/ }) as HTMLInputElement;
        expect(inApp.checked).toBe(true);
        expect(call.disabled).toBe(true);
        expect(screen.queryByPlaceholderText("98765 43210")).toBeNull();
        fireEvent.change(screen.getByPlaceholderText("For example: BP tablet after breakfast"), { target: { value: "BP tablet" } });
        fireEvent.click(screen.getByRole("button", { name: "Review before it starts" }));
        await waitFor(() => expect(sdk.addMedicineApiV1CareMedicinesPost).toHaveBeenCalled());
        const body = sdk.addMedicineApiV1CareMedicinesPost.mock.calls[0][0].body;
        expect(body.channel).toBe("app");
        expect(body.phone).toBeNull();
    });

    it("says test mode, lists the reminder with today's calls, and offers I took it", async () => {
        sdk.myMedicinesApiV1CareMedicinesGet.mockResolvedValue({
            data: {
                medicines: [
                    {
                        id: 3,
                        label: "BP tablet",
                        times: ["08:00"],
                        timezone: "Asia/Kolkata",
                        language: "ta-IN",
                        language_name: "Tamil",
                        channel: "call",
                        phone_masked: "the number ending 3210",
                        alert_member_ids: [],
                        state: "active",
                        card_event_id: 9,
                        doses: [{ id: 1, due_at: "2026-10-07T02:30:00Z", state: "not_answered", reason: null, alerted: true }],
                    },
                ],
                calls: { state: "test_mode", reason: "Test mode: calls are simulated and nobody is rung." },
                app: { state: "ready", reason: "Reminders show in Decibyl." },
                languages: { "ta-IN": "Tamil" },
            },
        });
        render(<MedicinesPanel />);
        expect(await screen.findByTestId("calls-test-mode")).toBeTruthy();
        expect(screen.getByText("BP tablet")).toBeTruthy();
        expect(screen.getByText(/the number ending 3210/)).toBeTruthy();
        expect(screen.getByText("Not answered")).toBeTruthy();
        expect(screen.getByRole("button", { name: "I took it" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Pause the calls" })).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: /Add a reminder/ }));
        expect(screen.getByText(/never gives advice about doses/)).toBeTruthy();
    });
});

function pausedReminder(state = "paused") {
    return {
        id: 4,
        label: "BP Tablet",
        times: ["08:00"],
        timezone: "Asia/Kolkata",
        language: "hi-IN",
        language_name: "Hindi",
        channel: "call",
        phone_masked: "the number ending 1878",
        alert_member_ids: [],
        state,
        card_event_id: null,
        doses: [],
    };
}

function listing(state = "paused") {
    return {
        data: {
            medicines: [pausedReminder(state)],
            calls: { state: "ready", reason: "" },
            app: { state: "ready", reason: "Reminders show in Decibyl." },
            languages: { "hi-IN": "Hindi", "ta-IN": "Tamil" },
        },
    };
}

describe("Changing a reminder", () => {
    it("edits the name, times and language, and never the number", async () => {
        sdk.myMedicinesApiV1CareMedicinesGet.mockResolvedValue(listing());
        sdk.editMedicineApiV1CareMedicinesMedicineIdPatch.mockResolvedValue({
            data: { medicine: { ...pausedReminder(), times: ["09:30"] }, card: null },
        });
        render(<MedicinesPanel />);
        fireEvent.click(await screen.findByRole("button", { name: /Edit/ }));
        const form = screen.getByTestId("medicine-edit");
        expect(form.querySelector('input[type="tel"]')).toBeNull();
        fireEvent.change(screen.getByLabelText("Time 1"), { target: { value: "09:30" } });
        fireEvent.change(screen.getByDisplayValue("Hindi"), { target: { value: "ta-IN" } });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        await waitFor(() => expect(sdk.editMedicineApiV1CareMedicinesMedicineIdPatch).toHaveBeenCalled());
        expect(sdk.editMedicineApiV1CareMedicinesMedicineIdPatch.mock.calls[0][0]).toEqual({
            path: { medicine_id: 4 },
            body: { label: "BP Tablet", times: ["09:30"], language: "ta-IN" },
        });
    });

    it("says a running reminder stops until the new details are confirmed", async () => {
        sdk.myMedicinesApiV1CareMedicinesGet.mockResolvedValue(listing("active"));
        render(<MedicinesPanel />);
        fireEvent.click(await screen.findByRole("button", { name: /Edit/ }));
        expect(screen.getByText(/calls stop until you confirm the new details/)).toBeTruthy();
        expect(screen.getByRole("button", { name: "Save and review" })).toBeTruthy();
    });

    it("shows the error instead of closing when the change is refused", async () => {
        sdk.myMedicinesApiV1CareMedicinesGet.mockResolvedValue(listing());
        sdk.editMedicineApiV1CareMedicinesMedicineIdPatch.mockResolvedValue({
            error: { detail: "Decibyl only reminds; it cannot decide how much to take." },
        });
        render(<MedicinesPanel />);
        fireEvent.click(await screen.findByRole("button", { name: /Edit/ }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(await screen.findByText(/cannot decide how much to take/)).toBeTruthy();
        expect(screen.getByTestId("medicine-edit")).toBeTruthy();
    });

    it("removes only after asking", async () => {
        sdk.myMedicinesApiV1CareMedicinesGet.mockResolvedValue(listing());
        sdk.removeMedicineApiV1CareMedicinesMedicineIdDelete.mockResolvedValue({ data: undefined });
        render(<MedicinesPanel />);
        fireEvent.click(await screen.findByRole("button", { name: /Remove/ }));
        expect(sdk.removeMedicineApiV1CareMedicinesMedicineIdDelete).not.toHaveBeenCalled();
        expect(screen.getByText(/Remove BP Tablet\?/)).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Yes, remove" }));
        await waitFor(() =>
            expect(sdk.removeMedicineApiV1CareMedicinesMedicineIdDelete).toHaveBeenCalledWith({ path: { medicine_id: 4 } }),
        );
    });
});

describe("People I look after", () => {
    it("shows only what was shared", async () => {
        sdk.familyApiV1CareFamilyGet.mockResolvedValue({
            data: {
                people: [
                    {
                        member_id: 2,
                        person: "Amma",
                        shares: ["medicine_alerts"],
                        alerts: [{ id: 7, kind: "dose_missed", title: "Amma did not answer the 08:00 call for BP tablet.", at: "2026-10-07T02:50:00Z", read: false }],
                        medicines: null,
                        scam_checks: null,
                    },
                ],
            },
        });
        render(<FamilyPanel />);
        expect(await screen.findByText("Amma did not answer the 08:00 call for BP tablet.")).toBeTruthy();
        expect(screen.queryByText("Medicine reminders")).toBeNull();
        expect(screen.queryByText("Scam checks")).toBeNull();
    });

    it("joins with a code and says whose circle", async () => {
        sdk.familyApiV1CareFamilyGet.mockResolvedValue({ data: { people: [] } });
        sdk.acceptInviteApiV1CareFamilyAcceptPost.mockResolvedValue({ data: { person: "Amma", shares: ["medicine_alerts"] } });
        render(<FamilyPanel />);
        fireEvent.change(await screen.findByPlaceholderText("ABCD-EFGH"), { target: { value: "abcd-efgh" } });
        fireEvent.click(screen.getByRole("button", { name: "Join" }));
        expect(await screen.findByText("You joined Amma's family circle.")).toBeTruthy();
        expect(sdk.acceptInviteApiV1CareFamilyAcceptPost).toHaveBeenCalledWith({ body: { code: "ABCD-EFGH" } });
        await waitFor(() => expect(sdk.familyApiV1CareFamilyGet).toHaveBeenCalledTimes(2));
    });
});

describe("dose words", () => {
    it("says every state a dose can be in, the unconfirmed call included", async () => {
        const { DOSE_WORDS } = await import("../copy");
        // services/care/calls.py: every state care_dose_calls.state takes.
        for (const state of [
            "calling",
            "reminded",
            "taken",
            "not_taken",
            "not_answered",
            "unclear",
            "failed",
            "cancelled",
            "unknown",
        ]) {
            expect(DOSE_WORDS[state], state).toBeTruthy();
        }
        // An unconfirmed call is not "Not answered".
        expect(DOSE_WORDS.unknown).toBe("Called, not confirmed yet");
    });
});
