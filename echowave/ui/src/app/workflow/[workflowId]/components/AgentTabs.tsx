"use client";

import { BarChart3, Bot, ChevronDown, ClipboardCheck, MessagesSquare, ScrollText, Share2, Variable, Wrench, Zap } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

import type { TabId } from "../settings/tabs";

/** All existing destinations remain available; messaging is separate from setup. */
export const AGENT_TABS = [
    { key: "assistant", label: "Edit", icon: Bot, group: "primary" },
    { key: "logs", label: "Activity", icon: ScrollText, group: "primary" },
    { key: "tools", label: "Tools", icon: Wrench, group: "setup" },
    { key: "triggers", label: "Triggers", icon: Zap, group: "setup" },
    { key: "analysis", label: "Quality", icon: ClipboardCheck, settingsTab: "analysis", group: "setup" },
    { key: "advanced", label: "Advanced", icon: Variable, settingsTab: "advanced", group: "setup" },
    { key: "share", label: "Share", icon: Share2, settingsTab: "share", group: "setup" },
    { key: "analytics", label: "Analytics", icon: BarChart3, group: "reporting" },
    { key: "thread", label: "Message", icon: MessagesSquare, group: "message" },
] as const;

type Tab = (typeof AGENT_TABS)[number];

function hrefFor(tab: Tab, workflowId: number): string {
    const base = `/workflow/${workflowId}`;
    if ("settingsTab" in tab) return `${base}/settings?tab=${tab.settingsTab}`;
    if (tab.key === "assistant") return base;
    if (tab.key === "logs") return `${base}/runs`;
    return `${base}/${tab.key}`;
}

function UnsavedIndicator() {
    return <span className="h-1.5 w-1.5 rounded-full bg-orange-500" aria-label="Unsaved changes" />;
}

export function AgentTabs({ workflowId, settingsTab, dirtyTabs }: {
    workflowId: number;
    /** Settings routes share a pathname; the page supplies its active section. */
    settingsTab?: TabId;
    dirtyTabs?: ReadonlySet<string>;
}) {
    const pathname = usePathname();
    const base = `/workflow/${workflowId}`;
    const isActive = (tab: Tab) => {
        if ("settingsTab" in tab) return pathname === `${base}/settings` && settingsTab === tab.settingsTab;
        const href = hrefFor(tab, workflowId);
        return pathname === href || (tab.key !== "assistant" && pathname.startsWith(`${href}/`));
    };
    const menuTabs = AGENT_TABS.filter((tab) => tab.group === "setup" || tab.group === "reporting");
    const activeSetup = menuTabs.find(isActive);
    const setupDirty = menuTabs.some((tab) => "settingsTab" in tab && dirtyTabs?.has(tab.settingsTab));
    const linkClass = (active: boolean) => cn(
        "-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:outline-offset-2",
        active ? "border-teal-600 font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
    );
    const renderLink = (tab: Tab, inMenu = false) => {
        const Icon = tab.icon;
        const link = <Link href={hrefFor(tab, workflowId)} aria-current={isActive(tab) ? "page" : undefined} className={inMenu ? cn(isActive(tab) && "bg-accent font-medium") : linkClass(isActive(tab))}>
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />{tab.label}
            {"settingsTab" in tab && dirtyTabs?.has(tab.settingsTab) && <UnsavedIndicator />}
        </Link>;
        return inMenu ? <DropdownMenuItem key={tab.key} asChild>{link}</DropdownMenuItem> : <div key={tab.key}>{link}</div>;
    };

    return (
        <nav aria-label="Agent" className="flex w-full flex-wrap items-center justify-between gap-x-3 border-b border-border px-4 sm:px-6">
            <div className="flex items-center gap-1">
                {AGENT_TABS.filter((tab) => tab.group === "primary").map((tab) => renderLink(tab))}
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <button type="button" className={linkClass(Boolean(activeSetup))}>
                            {activeSetup ? `Setup · ${activeSetup.label}` : "Setup"}
                            {setupDirty && <UnsavedIndicator />}
                            <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                        </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start" className="w-52">
                        <DropdownMenuLabel>Agent setup</DropdownMenuLabel>
                        {menuTabs.filter((tab) => tab.group === "setup").map((tab) => renderLink(tab, true))}
                        <DropdownMenuSeparator />
                        <DropdownMenuLabel>Reporting</DropdownMenuLabel>
                        {menuTabs.filter((tab) => tab.group === "reporting").map((tab) => renderLink(tab, true))}
                    </DropdownMenuContent>
                </DropdownMenu>
            </div>
            <Link href={`${base}/thread`} title="Message agent" aria-current={pathname === `${base}/thread` || pathname.startsWith(`${base}/thread/`) ? "page" : undefined} className="my-1 inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-accent focus-visible:outline-2 focus-visible:outline-offset-2">
                <MessagesSquare className="h-3.5 w-3.5" aria-hidden="true" /><span className="sr-only sm:not-sr-only">Message</span>
            </Link>
        </nav>
    );
}
