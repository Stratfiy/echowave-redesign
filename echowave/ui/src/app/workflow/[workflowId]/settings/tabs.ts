import { FileDown, Share2, Variable } from "lucide-react";

/**
 * Three tabs over the sections that have no tile to live behind.
 *
 * This screen was five tabs: Models, Calling, Analysis, Deploy, Advanced.
 * Models and most of Calling are gone from here, not deleted: the pencil on
 * each tile of the Assistant tab now opens the slot's own settings, the way
 * Vapi does it, so the transcriber's turn-taking is edited beside the
 * transcriber and the voice's speed beside the voice. What is left is what
 * no single slot owns — how a call ends, what is kept, how it is judged,
 * and how it reaches people outside this account.
 *
 * ``sections`` is what each tab contains, and it is load-bearing twice: it
 * decides what renders, and it carries the unsaved-changes dot up from a
 * section to the tab hiding it. A tab that hides an unsaved edit without
 * saying so is how someone loses work.
 */
export const TABS = [
    {
        id: "analysis",
        // Renamed, id kept: links out there carry ?tab=analysis, and this is
        // the same screen it always was. "Analysis" sat one tab along from
        // the bot's "Analytics", which is two words for two unrelated things
        // that nobody can tell apart at a glance. This one sets how calls are
        // judged and what is kept; Analytics reports what happened.
        label: "Quality",
        icon: FileDown,
        // "outcomes" beside "qa": one says how the call was handled,
        // the other what it achieved, and they come apart constantly.
        // "evals" is the way to its own screen.
        sections: ["qa", "outcomes", "recordings", "report", "evals"],
    },
    {
        id: "advanced",
        label: "Advanced",
        icon: Variable,
        // "general" is the call itself: name, fallbacks, limits, recording.
        sections: ["general", "voicemail", "variables", "identity"],
    },
    {
        id: "share",
        label: "Share",
        icon: Share2,
        // The link to text somebody, and the widget for a website.
        sections: ["share", "deployment"],
    },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export const DEFAULT_TAB: TabId = "advanced";

export function isTabId(value: string | null): value is TabId {
    return TABS.some((tab) => tab.id === value);
}
