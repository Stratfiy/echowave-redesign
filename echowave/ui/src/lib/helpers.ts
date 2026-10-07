import { useAppConfig } from "@/context/AppConfigContext";
import { type Feature, useFeature } from "@/lib/features";

/**
 * Launch stream `agents`: small shared pieces for the helper picker and the
 * pages it opens (saved reports, who owes me, trackers).
 */

export const AUTOMATIC = "automatic";

/** What each capability state reads as on a helper (screen 06). */
export const HELPER_STATE_LABEL: Record<string, string> = {
    available: "Ready",
    needs_setup: "Needs setup",
    disabled_by_policy: "Turned off by your workspace",
    unavailable: "Unavailable",
};

/** Connected-app toolkits, as people name them. Unknown ones show as sent. */
export const APP_NAMES: Record<string, string> = {
    gmail: "Gmail",
    outlook: "Outlook",
    googlecalendar: "Google Calendar",
    calendly: "Calendly",
    whatsapp: "WhatsApp",
    slack: "Slack",
};

/** A follow-up card's state, as the person reads its delivery. */
export const DELIVERY_LABEL: Record<string, string> = {
    awaiting_approval: "Waiting for your confirm",
    scheduled: "Confirmed, sending shortly",
    sending: "Sending",
    sent: "Sent",
    failed: "Not sent",
    cancelled: "Cancelled before sending",
    outcome_unknown: "We are checking whether this was delivered. Please do not send it again.",
    unknown: "Not known",
};

/**
 * Whether a flag is on, off, or not yet known. `useFeature` is false until
 * the flags arrive, which on a page reads as "not switched on" for a moment
 * before the page appears; this says "loading" instead.
 */
export function useFeatureState(name: Feature): "loading" | "on" | "off" {
    const { loading } = useAppConfig();
    const on = useFeature(name);
    if (on) return "on";
    return loading ? "loading" : "off";
}

/** A browser download of text the server returned, under its own name. */
export function downloadText(content: string, filename: string, type: string): void {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}
