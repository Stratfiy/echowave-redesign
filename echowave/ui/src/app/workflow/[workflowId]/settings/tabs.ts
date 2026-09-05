import { Brain, FileDown, Rocket, Settings, Variable } from "lucide-react";

/**
 * Five tabs over nine sections.
 *
 * This screen was one column of nine cards with a sticky "on this page" list
 * beside it, which is a table of contents for a document rather than a shape
 * for a settings screen: everything was equally prominent and nothing was
 * grouped by the job it belongs to. Both products we are measured against use
 * tabs here — Bolna has seven, Vapi five.
 *
 * Five, not the seven sketched earlier, because seven would have meant two
 * tabs holding nothing. The prompt and the model row now live on the editor,
 * so what is left on this screen is configuration, and it falls into exactly
 * these groups.
 *
 * ``sections`` is what each tab contains, and it is load-bearing twice: it
 * decides what renders, and it carries the unsaved-changes dot up from a
 * section to the tab hiding it. A tab that hides an unsaved edit without
 * saying so is how someone loses work.
 */
export const TABS = [
    {
        id: "models",
        label: "Models",
        icon: Brain,
        sections: ["models", "dictionary"],
    },
    {
        id: "calling",
        label: "Calling",
        icon: Settings,
        sections: ["general", "voicemail"],
    },
    {
        id: "analysis",
        label: "Analysis",
        icon: FileDown,
        sections: ["qa", "recordings", "report"],
    },
    {
        id: "deploy",
        label: "Deploy",
        icon: Rocket,
        sections: ["deployment"],
    },
    {
        id: "advanced",
        label: "Advanced",
        icon: Variable,
        sections: ["variables", "identity"],
    },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export const DEFAULT_TAB: TabId = "models";

export function isTabId(value: string | null): value is TabId {
    return TABS.some((tab) => tab.id === value);
}
