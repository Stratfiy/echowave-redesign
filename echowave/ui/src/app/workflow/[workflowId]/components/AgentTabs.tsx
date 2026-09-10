/**
 * One agent, one row of tabs.
 *
 * There were two rows. The agent-level strip carried Assistant, Logs, Tools,
 * Analysis and Advanced; the settings page then drew its own underneath with
 * Models, Calling, Analysis, Deploy and Advanced. So "Analysis" and "Advanced"
 * each appeared twice, a centimetre apart, going to different places — and the
 * outer "Advanced" opened the inner "Calling", which is not a thing anyone can
 * be expected to guess.
 *
 * Vapi has one strip and that is the whole of the fix. Every destination is
 * named exactly once, the name says where it goes, and nothing is nested
 * inside something with the same name.
 *
 * These navigate rather than swap panels. Each screen loads its own data, and
 * a tab that fetches when you open it is what you want here regardless of how
 * it is wired.
 *
 * **Four of them are the settings page on different tabs**, so the pathname
 * alone cannot tell them apart. Rather than read the query string during
 * render — which drags in a Suspense boundary for a highlight — the settings
 * page passes down which of its tabs is showing. The page that knows the
 * answer is the one that says.
 */

"use client";

import {
    BarChart3,
    Bot,
    Brain,
    FlaskConical,
    Rocket,
    ScrollText,
    Settings,
    Variable,
    Wrench,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

import type { TabId } from "../settings/tabs";

/**
 * Build it, then run it, then read what happened.
 *
 * `settingsTab` marks the four that are the settings page; the rest are their
 * own routes. Order is the order somebody does these in, not alphabetical and
 * not the order they happened to be built in.
 */
export const AGENT_TABS = [
    { key: "assistant", label: "Assistant", icon: Bot },
    { key: "models", label: "Models", icon: Brain, settingsTab: "models" },
    { key: "calling", label: "Calling", icon: Settings, settingsTab: "calling" },
    { key: "tools", label: "Tools", icon: Wrench },
    { key: "evals", label: "Evals", icon: FlaskConical },
    { key: "analysis", label: "Analysis", icon: BarChart3, settingsTab: "analysis" },
    { key: "logs", label: "Logs", icon: ScrollText },
    { key: "deploy", label: "Deploy", icon: Rocket, settingsTab: "deploy" },
    { key: "advanced", label: "Advanced", icon: Variable, settingsTab: "advanced" },
] as const;

type Tab = (typeof AGENT_TABS)[number];

function hrefFor(tab: Tab, workflowId: number): string {
    const base = `/workflow/${workflowId}`;
    if ("settingsTab" in tab) return `${base}/settings?tab=${tab.settingsTab}`;
    switch (tab.key) {
        case "logs":
            return `${base}/runs`;
        case "tools":
            return `${base}/tools`;
        case "evals":
            return `${base}/evals`;
        default:
            return base;
    }
}

export function AgentTabs({
    workflowId,
    settingsTab,
    dirtyTabs,
}: {
    workflowId: number;
    /** Which settings tab is showing, when the settings page is the one open. */
    settingsTab?: TabId;
    /** Settings tabs holding an edit nobody has saved. */
    dirtyTabs?: ReadonlySet<string>;
}) {
    const pathname = usePathname();
    const base = `/workflow/${workflowId}`;

    const isActive = (tab: Tab) => {
        if ("settingsTab" in tab) return settingsTab === tab.settingsTab;
        if (tab.key === "logs") return pathname.startsWith(`${base}/runs`);
        if (tab.key === "tools") return pathname.startsWith(`${base}/tools`);
        if (tab.key === "evals") return pathname.startsWith(`${base}/evals`);
        // The canvas, and only the canvas. `startsWith` would light it on
        // every tab, since every one of these lives under the same base.
        return pathname === base;
    };

    return (
        <nav
            aria-label="Agent"
            className="w-full overflow-x-auto border-b border-border px-6"
        >
            <ul className="flex min-w-max gap-1">
                {AGENT_TABS.map((tab) => {
                    const Icon = tab.icon;
                    const active = isActive(tab);
                    // A tab hiding an unsaved edit has to say so, or moving
                    // away from it looks like discarding the work.
                    const unsaved =
                        "settingsTab" in tab && dirtyTabs?.has(tab.settingsTab);
                    return (
                        <li key={tab.key}>
                            <Link
                                href={hrefFor(tab, workflowId)}
                                aria-current={active ? "page" : undefined}
                                className={cn(
                                    "-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm whitespace-nowrap transition-colors",
                                    active
                                        ? "border-primary font-medium text-foreground"
                                        : "border-transparent text-muted-foreground hover:text-foreground",
                                )}
                            >
                                <Icon className="h-3.5 w-3.5" />
                                {tab.label}
                                {unsaved && (
                                    <span
                                        className="h-1.5 w-1.5 rounded-full bg-orange-500"
                                        aria-label="Unsaved changes"
                                    />
                                )}
                            </Link>
                        </li>
                    );
                })}
            </ul>
        </nav>
    );
}
