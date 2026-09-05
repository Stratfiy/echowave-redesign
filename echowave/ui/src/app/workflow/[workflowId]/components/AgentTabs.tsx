/**
 * One agent, five tabs — the shape Vapi uses, over the screens we already had.
 *
 * Everything here existed before: the editor, the run list, the settings page.
 * They were four unrelated URLs a customer had to know about, reached from a
 * back arrow and a `⋮` menu, so an agent was never one thing you were looking
 * at — it was several places you had to remember. Vapi's answer is a tab strip
 * under the title, and it is the right one.
 *
 * These navigate rather than swap panels. Rebuilding four routes into one
 * component would be a large change with nothing extra to show for it, and
 * each of those screens loads its own data anyway — a tab that fetches when
 * you open it is the behaviour you want here regardless of how it is wired.
 *
 * **Analysis and Advanced share a route.** Both are the settings page, on
 * different tabs of its own, so pathname alone cannot tell them apart. Rather
 * than read the query string during render — which drags in Suspense
 * boundaries for a highlight — the settings page passes down which of its tabs
 * is showing. The page that knows the answer is the one that says.
 */

"use client";

import { BarChart3, Bot, ScrollText, Settings2, Wrench } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

/** Which tab is lit when the settings page is the one being shown. */
export type SettingsTabGroup = "analysis" | "advanced";

const TABS = [
    { key: "assistant", label: "Assistant", icon: Bot },
    { key: "logs", label: "Logs", icon: ScrollText },
    { key: "tools", label: "Tools", icon: Wrench },
    { key: "analysis", label: "Analysis", icon: BarChart3 },
    { key: "advanced", label: "Advanced", icon: Settings2 },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function hrefFor(key: TabKey, workflowId: number): string {
    const base = `/workflow/${workflowId}`;
    switch (key) {
        case "assistant":
            return base;
        case "logs":
            return `${base}/runs`;
        case "tools":
            return `${base}/tools`;
        case "analysis":
            return `${base}/settings?tab=analysis`;
        case "advanced":
            return `${base}/settings?tab=calling`;
    }
}

export function AgentTabs({
    workflowId,
    /**
     * Set by the settings page only. Without it a settings URL would light
     * neither tab, because both of them point at it.
     */
    settingsGroup,
}: {
    workflowId: number;
    settingsGroup?: SettingsTabGroup;
}) {
    const pathname = usePathname() ?? "";
    const base = `/workflow/${workflowId}`;

    const active: TabKey = settingsGroup
        ? settingsGroup
        : pathname.startsWith(`${base}/runs`) || pathname.startsWith(`${base}/run/`)
          ? "logs"
          : pathname.startsWith(`${base}/tools`)
            ? "tools"
            : pathname.startsWith(`${base}/settings`)
              ? "advanced"
              : "assistant";

    return (
        <nav
            aria-label="Agent sections"
            // Scrolls on a phone rather than wrapping: a second row here would
            // push the agent itself below the fold on the screen people open
            // most.
            className="flex gap-1 overflow-x-auto border-b bg-background px-4 py-1.5"
        >
            {TABS.map((tab) => {
                const Icon = tab.icon;
                const isActive = active === tab.key;
                return (
                    <Link
                        key={tab.key}
                        href={hrefFor(tab.key, workflowId)}
                        aria-current={isActive ? "page" : undefined}
                        className={cn(
                            "flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                            isActive
                                ? "bg-muted font-medium text-foreground"
                                : "text-muted-foreground hover:text-foreground",
                        )}
                    >
                        <Icon className="h-3.5 w-3.5" />
                        {tab.label}
                    </Link>
                );
            })}
        </nav>
    );
}
