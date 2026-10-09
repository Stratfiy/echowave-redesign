/**
 * Words on the care screens (launch stream `care`).
 *
 * Plain, short, second person. Functional words only: what a button does,
 * what happened, what to do next. No positioning, price or plan line is
 * written here -- those are the founder's (AGENTS.md, "No new plan, price or
 * positioning string without asking").
 */

/**
 * PLACEHOLDER (founder): the one line under "Care" that says what it is for.
 * Positioning copy, so it is left for the founder to write; while null the
 * hub shows no line at all rather than an invented one. Listed in the PR.
 */
export const CARE_POSITIONING_LINE: string | null = null;

export const PART_TITLES = {
    care_scam_check: "Is this a scam?",
    care_tech_help: "Help with my phone",
    care_medicine_calls: "My medicine reminders",
    care_family_circle: "My family",
    family_view: "People I look after",
} as const;

export const PART_HINTS = {
    care_scam_check: "Paste a message, or tell me about a call.",
    care_tech_help: "One step at a time, in plain words.",
    care_medicine_calls: "Decibyl rings you when it is time.",
    care_family_circle: "Choose who sees what.",
    family_view: "What your family shared with you.",
} as const;

export const NEVER_ASKS = "Decibyl will never ask you for an OTP, PIN or password.";

export const SHARE_LABELS: Record<string, string> = {
    medicine_alerts: "Tell them when a medicine call is missed or not answered",
    medicine_schedule: "Let them see my medicine reminders and today's calls",
    scam_checks: "Let them see when I checked something for a scam (not what it said)",
    help_requests: "Tell them when I get stuck with phone help",
};

export const DOSE_WORDS: Record<string, string> = {
    calling: "Calling now",
    reminded: "Time for it now",
    taken: "Taken",
    not_taken: "Not taken",
    not_answered: "Not answered",
    unclear: "Answered, not sure",
    failed: "Could not call",
    cancelled: "Stopped before the call",
};

export const MEDICINE_STATE_WORDS: Record<string, string> = {
    awaiting_approval: "Waiting for your OK",
    active: "On",
    paused: "Paused",
    declined: "Not started",
};

export const MEMBER_STATE_WORDS: Record<string, string> = {
    proposed: "Waiting for your OK",
    invited: "Invited, not joined yet",
    active: "Joined",
    revoked: "Removed",
};
