/**
 * Org 360 (ADMIN-2): the shapes the staff accounts screens read, the words
 * they show, and the small pure rules that turn one into the other.
 *
 * The words live here, in one place, so copy review reads one file rather
 * than five components.
 */

export type TrialStage = "not_on_trial" | "active" | "ending_soon" | "ended";

export type TrialView = {
    on_trial: boolean;
    active: boolean;
    stage: TrialStage;
    notice_stage: string | null;
    starts_at: string | null;
    ends_at: string | null;
    days_left: number | null;
    /** Staff have set the end date by hand. */
    override: boolean;
};

export type OrgHealth = {
    plan: string;
    plan_is_paid: boolean;
    trial_ends_at: string | null;
    trial: TrialView;
    agents_count: number;
    live_agents_count: number;
    channels: Record<string, number>;
    channels_linked: number;
    kyc_status: string;
    byok_keys_present: boolean;
    byok_providers: string[];
};

export type RecentFailure = {
    id: number;
    at: string | null;
    summary: string;
    workflow_id: number | null;
    workflow_name: string | null;
    workflow_run_id: number | null;
};

export const CHANNEL_LABELS: Record<string, string> = {
    whatsapp: "WhatsApp",
    telegram: "Telegram",
    slack: "Slack",
    teams: "Teams",
};

export const KYC_LABELS: Record<string, string> = {
    not_started: "Not started",
    submitted: "Submitted",
    under_review: "Under review",
    rejected: "Rejected by us",
    forwarded: "With the carrier",
    carrier_rejected: "Rejected by the carrier",
    carrier_approved: "Approved",
};

export const COPY = {
    healthTitle: "Account health",
    plan: "Plan",
    agents: "Agents",
    channels: "Linked apps",
    kyc: "Phone KYC",
    ownKeys: "Own keys",
    recentFailures: "Recent failures",
    noFailures: "Nothing has failed lately.",
    noChannels: "No apps linked",
    ownKeysNone: "Using Decibyl's keys",
    extendTrial: "Extend trial by 7 days",
    setEndDate: "Set end date",
    endTrialNow: "End trial now",
    pauseAccount: "Pause account",
    pauseExplainer:
        "Pausing ends the trial now: new calls, messages and routines stop, and nothing is deleted. It only works while the account is on the trial.",
    pauseUnavailable:
        "Pause works only for accounts on the trial. This account is not on the trial, so ending a trial would change nothing.",
    impersonateOwner: "Impersonate owner",
    impersonateNoOwner: "This account has nobody to sign in as.",
    impersonateExplainer:
        "Opens a new tab signed in as the owner for up to an hour. The start and the stop are both written to the audit log.",
    audit: "Audit log",
    // A1 (flag console) lands separately; the org page keeps a slot for it.
} as const;

export function planLabel(health: Pick<OrgHealth, "plan">): string {
    return health.plan.charAt(0).toUpperCase() + health.plan.slice(1);
}

/** "Trial · 5 days left", "Trial ended", "Business" -- one short line. */
export function trialSummary(health: Pick<OrgHealth, "plan" | "plan_is_paid" | "trial">): string {
    const trial = health.trial;
    if (!trial.on_trial) return planLabel(health);
    if (trial.stage === "ended") return "Trial ended";
    if (trial.days_left === null) return "Trial";
    if (trial.days_left === 0) return "Trial · ends today";
    return `Trial · ${trial.days_left} day${trial.days_left === 1 ? "" : "s"} left`;
}

export function trialTone(trial: TrialView): "critical" | "warning" | undefined {
    if (!trial.on_trial) return undefined;
    if (trial.stage === "ended") return "critical";
    if (trial.stage === "ending_soon") return "warning";
    return undefined;
}

/** "WhatsApp 2 · Slack 1", every app with at least one link, known apps
 *  first and anything new after them under its own name. */
export function channelsSummary(channels: Record<string, number>): string {
    const known = Object.keys(CHANNEL_LABELS);
    const names = [
        ...known.filter((name) => (channels[name] ?? 0) > 0),
        ...Object.keys(channels)
            .filter((name) => !known.includes(name) && channels[name] > 0)
            .sort(),
    ];
    if (names.length === 0) return COPY.noChannels;
    return names.map((name) => `${CHANNEL_LABELS[name] ?? name} ${channels[name]}`).join(" · ");
}

export function kycLabel(status: string): string {
    return KYC_LABELS[status] ?? status.replaceAll("_", " ");
}

/** Whether the interim pause (end the trial now) can do anything. */
export function canPause(trial: TrialView): boolean {
    return trial.on_trial && trial.active;
}
