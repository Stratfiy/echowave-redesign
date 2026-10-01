import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
    canPause,
    channelsSummary,
    kycLabel,
    type OrgHealth,
    trialSummary,
    trialTone,
    type TrialView,
} from "../orgHealth";
import { OrgHealthCard } from "../OrgHealthCard";

function trial(over: Partial<TrialView> = {}): TrialView {
    return {
        on_trial: true,
        active: true,
        stage: "active",
        notice_stage: null,
        starts_at: "2026-10-04T00:00:00+05:30",
        ends_at: "2026-10-18T00:00:00+05:30",
        days_left: 9,
        override: false,
        ...over,
    };
}

function health(over: Partial<OrgHealth> = {}): OrgHealth {
    return {
        plan: "trial",
        plan_is_paid: false,
        trial_ends_at: null,
        trial: trial(),
        agents_count: 3,
        live_agents_count: 1,
        channels: { whatsapp: 2, telegram: 0, slack: 1, teams: 0 },
        channels_linked: 3,
        kyc_status: "under_review",
        byok_keys_present: true,
        byok_providers: ["openai"],
        ...over,
    };
}

describe("the Org 360 rules", () => {
    it("reads the trial in one short line", () => {
        expect(trialSummary(health())).toBe("Trial · 9 days left");
        expect(trialSummary(health({ trial: trial({ days_left: 1 }) }))).toBe("Trial · 1 day left");
        expect(trialSummary(health({ trial: trial({ days_left: 0 }) }))).toBe("Trial · ends today");
        expect(trialSummary(health({ trial: trial({ stage: "ended", active: false }) }))).toBe(
            "Trial ended",
        );
        expect(
            trialSummary(
                health({
                    plan: "business",
                    plan_is_paid: true,
                    trial: trial({ on_trial: false, stage: "not_on_trial" }),
                }),
            ),
        ).toBe("Business");
    });

    it("marks an ending or ended trial", () => {
        expect(trialTone(trial({ stage: "ending_soon" }))).toBe("warning");
        expect(trialTone(trial({ stage: "ended" }))).toBe("critical");
        expect(trialTone(trial())).toBeUndefined();
        expect(trialTone(trial({ on_trial: false }))).toBeUndefined();
    });

    it("lists linked apps, including one it has no label for", () => {
        expect(channelsSummary({ whatsapp: 2, slack: 1, teams: 0 })).toBe("WhatsApp 2 · Slack 1");
        expect(channelsSummary({ whatsapp: 0, signal: 4 })).toBe("signal 4");
        expect(channelsSummary({ whatsapp: 0 })).toBe("No apps linked");
    });

    it("only pauses an account that is on an open trial", () => {
        expect(canPause(trial())).toBe(true);
        expect(canPause(trial({ active: false, stage: "ended" }))).toBe(false);
        expect(canPause(trial({ on_trial: false }))).toBe(false);
    });

    it("names KYC states in words, and an unknown one as itself", () => {
        expect(kycLabel("carrier_approved")).toBe("Approved");
        expect(kycLabel("something_new")).toBe("something new");
    });
});

describe("OrgHealthCard", () => {
    it("shows the health tiles and the last failures", () => {
        render(
            <OrgHealthCard
                health={health()}
                failures={[
                    {
                        id: 7,
                        at: "2026-10-01T10:00:00Z",
                        summary: "Morning report could not finish its run",
                        workflow_id: 3,
                        workflow_name: "Morning report",
                        workflow_run_id: 99,
                    },
                ]}
                actions={<button type="button">act</button>}
            />,
        );
        expect(screen.getByText("Trial · 9 days left")).toBeTruthy();
        expect(screen.getByText("WhatsApp 2 · Slack 1")).toBeTruthy();
        expect(screen.getByText("Under review")).toBeTruthy();
        expect(screen.getByText("Morning report could not finish its run")).toBeTruthy();
        expect(screen.getByRole("button", { name: "act" })).toBeTruthy();
        expect(screen.queryByText("Nothing has failed lately.")).toBeNull();
    });

    it("says so when nothing failed", () => {
        render(<OrgHealthCard health={health({ byok_keys_present: false, byok_providers: [] })} failures={[]} />);
        expect(screen.getByText("Nothing has failed lately.")).toBeTruthy();
        expect(screen.getByText("Using Decibyl's keys")).toBeTruthy();
    });

    it("never calls an agent a bot", () => {
        const { container } = render(<OrgHealthCard health={health()} failures={[]} />);
        expect(/\bbots?\b/i.test(container.textContent ?? "")).toBe(false);
    });
});
