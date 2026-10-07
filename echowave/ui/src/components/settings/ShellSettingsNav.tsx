"use client";

/**
 * The Settings shell's section list (screen 17).
 *
 * Desktop: a 208px grouped list beside a 640px form column, with Settings
 * search at its head. Phone: /settings is the grouped list itself, full
 * screen, and every section opens as its own page with Back -- no sideways
 * row of tabs to hunt through.
 *
 * Groups are Personal, Connections, Privacy and Advanced; the workspace's own
 * sections sit under a heading with the workspace's name, so nobody mistakes
 * the team's timezone for their own. Search matches everyday words ("mic",
 * "memory", "email", "dark mode") and only ever offers a section this person
 * can open.
 */

import { ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useState } from "react";

import { listMyOrganizationsApiV1OrganizationsMineGet } from "@/client/sdk.gen";
import type { UserOrganizationResponse } from "@/client/types.gen";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { cn } from "@/lib/utils";

import { activeShellSection, searchSettings, SHELL_FLAGS, SHELL_GROUPS, type ShellGroup, visibleShellSections } from "./sections";
import { isSettingsRoot } from "./SettingsNav";

function useWorkspaceName(): string | null {
    const { user, loading } = useAuth();
    const signedIn = !loading && Boolean(user);
    const [name, setName] = useState<string | null>(null);
    useEffect(() => {
        if (!signedIn) return;
        let cancelled = false;
        void (async () => {
            const result = await listMyOrganizationsApiV1OrganizationsMineGet();
            if (cancelled || result.error) return;
            const list = (result.data as UserOrganizationResponse[]) ?? [];
            const current = list.find((o) => o.is_selected) ?? list[0];
            setName(current?.name ?? null);
        })();
        return () => {
            cancelled = true;
        };
    }, [signedIn]);
    return name;
}

export function ShellSettingsNav() {
    const pathname = usePathname() ?? "";
    const router = useRouter();
    const root = isSettingsRoot(pathname);
    const roles = useAccessRoles();
    const workspaceName = useWorkspaceName();
    // One useFeature per switch, always in the same order.
    // eslint-disable-next-line react-hooks/rules-of-hooks -- a fixed, static list
    const on = SHELL_FLAGS.map((flag) => [flag, useFeature(flag)] as const);
    const key = on.map(([, value]) => (value ? "1" : "0")).join("");
    const sections = useMemo(
        () => visibleShellSections((feature) => on.some(([flag, value]) => flag === feature && value), roles.isOrganizationAdmin),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [key, roles.isOrganizationAdmin],
    );
    const current = activeShellSection(pathname, sections);
    const [query, setQuery] = useState("");
    const hits = useMemo(() => searchSettings(query, sections), [query, sections]);
    const searchId = useId();

    const heading = (group: ShellGroup) => (group === "Workspace" ? (workspaceName ?? "Your workspace") : group);

    return (
        <>
            {!root && (
                <Link
                    href="/settings"
                    className="flex min-h-11 items-center gap-1 px-4 pt-2 text-sm text-muted-foreground md:hidden"
                    data-testid="settings-back"
                >
                    <ChevronLeft className="h-4 w-4" aria-hidden="true" /> Settings
                </Link>
            )}
            <nav
                aria-label="Settings sections"
                className={cn("shrink-0 md:block md:w-52", root ? "block w-full" : "hidden")}
                data-testid="settings-shell-nav"
            >
                <div className="px-4 pt-4 md:px-3 md:pt-6">
                    <div className="relative mb-4 md:mb-3" role="search">
                        <label htmlFor={searchId} className="sr-only">
                            Search settings
                        </label>
                        <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                        <input
                            id={searchId}
                            type="search"
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            onKeyDown={(event) => {
                                if (event.key === "Escape") setQuery("");
                                if (event.key === "Enter" && hits[0]) {
                                    event.preventDefault();
                                    router.push(hits[0].href);
                                    setQuery("");
                                }
                            }}
                            placeholder="Search"
                            autoComplete="off"
                            className="h-11 w-full rounded-[10px] border border-border bg-background pl-9 pr-9 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring md:h-9 md:text-sm"
                            data-testid="settings-search"
                        />
                        {query && (
                            <button
                                type="button"
                                aria-label="Clear search"
                                onClick={() => setQuery("")}
                                className="absolute right-1 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center text-muted-foreground"
                            >
                                <X aria-hidden className="h-4 w-4" />
                            </button>
                        )}
                    </div>

                    {query ? (
                        <div aria-live="polite">
                            <p className="px-2.5 pb-1 text-xs text-muted-foreground">
                                {hits.length === 0 ? `Nothing in Settings matches “${query}”.` : `${hits.length} in Settings`}
                            </p>
                            <ul className="divide-y divide-[var(--line)] rounded-2xl bg-[var(--paper-2)] md:rounded-[10px]" data-testid="settings-search-results">
                                {hits.map((hit) => (
                                    <li key={hit.href}>
                                        <Link
                                            href={hit.href}
                                            onClick={() => setQuery("")}
                                            className="flex min-h-11 flex-col justify-center px-4 py-2 md:px-2.5"
                                        >
                                            <span className="text-[15px] md:text-sm">{hit.label}</span>
                                            <span className="text-xs text-muted-foreground">
                                                {heading(hit.section.group)} · {hit.section.title}
                                            </span>
                                        </Link>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    ) : (
                        SHELL_GROUPS.map((group) => {
                            const inGroup = sections.filter((section) => section.group === group);
                            if (inGroup.length === 0) return null;
                            return (
                                <div key={group} className="mb-4 md:mb-3" data-testid={`settings-group-${group}`}>
                                    <h2 className="truncate px-2.5 pb-1 text-xs text-muted-foreground">{heading(group)}</h2>
                                    <ul className="divide-y divide-[var(--line)] rounded-2xl bg-[var(--paper-2)] md:divide-y-0 md:rounded-none md:bg-transparent">
                                        {inGroup.map((section) => (
                                            <li key={section.id}>
                                                <Link
                                                    href={section.mobileHref ?? section.href}
                                                    className="flex min-h-11 items-center justify-between gap-3 px-4 py-2.5 text-[15px] md:hidden"
                                                >
                                                    <span className="min-w-0">
                                                        <span className="block">{section.title}</span>
                                                        <span className="block text-xs text-muted-foreground">{section.blurb}</span>
                                                    </span>
                                                    <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                                                </Link>
                                                <Link
                                                    href={section.href}
                                                    aria-current={current === section.id ? "page" : undefined}
                                                    className={cn(
                                                        "motion-m1 hidden truncate rounded-[10px] px-2.5 py-1.5 text-sm text-foreground transition-colors hover:bg-[var(--line)] md:block",
                                                        current === section.id && "bg-[var(--line)] font-medium",
                                                    )}
                                                >
                                                    {section.title}
                                                </Link>
                                            </li>
                                        ))}
                                    </ul>
                                </div>
                            );
                        })
                    )}
                </div>
            </nav>
        </>
    );
}

export default ShellSettingsNav;
