"use client";

/**
 * The staff console's shell (design "StaffShell"; screen 29 layout): a
 * 224 px rail with the eight destinations, a persistent header with the
 * environment, timezone, period and freshness, and 24 px gutters. Below
 * 1024 px the rail becomes a drawer. Only the destinations the person's
 * roles allow are links; the rest are not drawn at all.
 *
 * The backend enforces every route; this decides what is shown, and refuses
 * a page the person's role does not include with a plain sentence rather
 * than a screen of 403s.
 */

import { Menu, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { type LoadState, useStaffData } from "@/lib/staff/data";
import { ago } from "@/lib/staff/format";
import { activeDestination, canOpen, destinationHref, DESTINATIONS, linkNeedsSetup, type Me } from "@/lib/staff/nav";
import { cn } from "@/lib/utils";

export const PERIODS = [7, 28, 90] as const;
export type Period = (typeof PERIODS)[number];

type Freshness = { state: LoadState; at: Date | null } | null;

type StaffConsole = {
    me: Me;
    days: Period;
    setDays: (days: Period) => void;
    /** A page reports its data's freshness to the header. */
    report: (freshness: Freshness) => void;
    can: (capability: string) => boolean;
};

const Ctx = createContext<StaffConsole | null>(null);

export function useStaffConsole(): StaffConsole {
    const value = useContext(Ctx);
    if (!value) throw new Error("useStaffConsole outside StaffShell");
    return value;
}

/** For a page: report freshness to the header while mounted. */
export function useReportFreshness(state: LoadState, at: Date | null) {
    const { report } = useStaffConsole();
    useEffect(() => {
        report({ state, at });
    }, [report, state, at]);
    useEffect(() => () => report(null), [report]);
}

function timezoneLabel(): string {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
        return "UTC";
    }
}

function Rail({ me, pathname, onNavigate }: { me: Me; pathname: string; onNavigate?: () => void }) {
    const allowed = new Set(me.destinations.filter((d) => d.allowed).map((d) => d.key));
    const owner = me.roles.includes("owner");
    const current = activeDestination(pathname);
    return (
        <nav aria-label="Staff console" className="flex flex-col gap-1 p-3 text-sm">
            {DESTINATIONS.filter((d) => allowed.has(d.key)).map((d) => {
                const active = d.key === current;
                const children = d.children.filter((c) => (!c.legacy || owner) && (!c.capability || me.capabilities.includes(c.capability)));
                return (
                    <div key={d.key}>
                        <Link
                            href={destinationHref(me, d)}
                            onClick={onNavigate}
                            aria-current={active ? "page" : undefined}
                            className={cn(
                                "motion-m1 flex min-h-11 items-center rounded-md px-3 font-medium hover:bg-muted lg:min-h-9",
                                active ? "bg-muted text-foreground" : "text-muted-foreground",
                            )}
                        >
                            {d.label}
                        </Link>
                        {active && children.length > 1 && (
                            <ul className="mb-1 ml-3 border-l border-border pl-2">
                                {children.map((c) => (
                                    <li key={c.href}>
                                        {linkNeedsSetup(me, c) ? (
                                            <span className="flex min-h-11 items-center justify-between gap-2 px-2 text-xs text-muted-foreground lg:min-h-8">
                                                {c.label}
                                                <span className="rounded border border-border px-1.5 py-0.5 text-[11px]">Needs setup</span>
                                            </span>
                                        ) : (
                                            <Link
                                                href={c.href}
                                                onClick={onNavigate}
                                                aria-current={pathname === c.href ? "page" : undefined}
                                                className={cn(
                                                    "motion-m1 flex min-h-11 items-center rounded px-2 text-xs hover:bg-muted lg:min-h-8",
                                                    pathname === c.href ? "font-medium text-foreground" : "text-muted-foreground",
                                                )}
                                            >
                                                {c.label}
                                                {c.legacy && <span className="sr-only"> (existing screen)</span>}
                                            </Link>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                );
            })}
            <Link
                href="/overview"
                onClick={onNavigate}
                className="motion-m1 mt-4 flex min-h-11 items-center rounded-md border-t border-border px-3 pt-2 text-xs text-muted-foreground hover:bg-muted lg:min-h-9"
            >
                Back to Decibyl
            </Link>
        </nav>
    );
}

function FreshnessLine({ freshness }: { freshness: Freshness }) {
    const [, tick] = useState(0);
    useEffect(() => {
        const timer = window.setInterval(() => tick((n) => n + 1), 15_000);
        return () => window.clearInterval(timer);
    }, []);
    if (!freshness) return null;
    const label =
        freshness.state === "loading"
            ? "Loading…"
            : freshness.state === "stale"
              ? `Stale: last refreshed ${ago(freshness.at)}`
              : freshness.state === "failed"
                ? "Could not load"
                : freshness.state === "needs_setup"
                  ? "Needs setup"
                  : `Refreshed ${ago(freshness.at)}`;
    return (
        <span
            role="status"
            className={cn("text-xs", freshness.state === "stale" || freshness.state === "failed" ? "text-[#705500] dark:text-amber-300" : "text-muted-foreground")}
        >
            {label}
        </span>
    );
}

export function StaffShell({ children }: { children: ReactNode }) {
    const pathname = usePathname() ?? "/superadmin";
    const meQuery = useStaffData<Me>("/api/v1/admin/staff/me");
    const [days, setDays] = useState<Period>(28);
    const [freshness, setFreshness] = useState<Freshness>(null);
    const [drawer, setDrawer] = useState(false);
    const report = useCallback((f: Freshness) => setFreshness(f), []);
    const me = meQuery.data;
    const value = useMemo<StaffConsole | null>(
        () =>
            me
                ? {
                      me,
                      days,
                      setDays,
                      report,
                      can: (capability: string) => me.capabilities.includes(capability),
                  }
                : null,
        [me, days, report],
    );

    if (meQuery.state === "loading") return null;
    if (!me || !value) {
        return (
            <Refusal
                title={meQuery.state === "needs_setup" ? "The staff console is not switched on here" : "This page is not available"}
                detail={meQuery.state === "needs_setup" ? "Turn on staff_console to use it." : (meQuery.error ?? "Your account does not have access to this area.")}
            />
        );
    }
    const production = me.environment.toLowerCase() === "production";
    return (
        <Ctx.Provider value={value}>
            <div className="flex min-h-screen w-full max-w-full overflow-x-hidden" data-testid="staff-shell">
                <aside className="hidden w-56 shrink-0 border-r border-border lg:block" aria-label="Destinations">
                    <Rail me={me} pathname={pathname} />
                </aside>
                <div className="flex min-w-0 flex-1 flex-col">
                    <header className="z-10 flex lg:sticky lg:top-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-border bg-background px-4 py-2 lg:px-6">
                        <Sheet open={drawer} onOpenChange={setDrawer}>
                            <SheetTrigger asChild>
                                <Button variant="ghost" size="icon" className="min-h-11 min-w-11 lg:hidden" aria-label="Open staff navigation">
                                    <Menu aria-hidden />
                                </Button>
                            </SheetTrigger>
                            <SheetContent side="left" className="w-72 p-0">
                                <SheetTitle className="px-4 pt-4">Staff console</SheetTitle>
                                <SheetDescription className="sr-only">Destinations you can open</SheetDescription>
                                <Rail me={me} pathname={pathname} onNavigate={() => setDrawer(false)} />
                            </SheetContent>
                        </Sheet>
                        <span
                            className={cn(
                                "rounded border px-2 py-0.5 text-xs font-medium",
                                production ? "border-[#772322]/40 text-[#772322] dark:text-red-300" : "border-border text-muted-foreground",
                            )}
                            data-testid="staff-environment"
                        >
                            {me.environment}
                        </span>
                        <span className="text-xs text-muted-foreground">{timezoneLabel()}</span>
                        <label className="flex items-center gap-1 text-xs text-muted-foreground">
                            <span>Period</span>
                            <select
                                value={days}
                                onChange={(event) => setDays(Number(event.target.value) as Period)}
                                className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-8 md:text-xs"
                            >
                                {PERIODS.map((p) => (
                                    <option key={p} value={p}>
                                        Last {p} days
                                    </option>
                                ))}
                            </select>
                        </label>
                        <span className="ml-auto">
                            <FreshnessLine freshness={freshness} />
                        </span>
                    </header>
                    <main className="ph-no-capture min-w-0 flex-1 px-4 py-4 lg:px-6 lg:py-6" data-ph-no-capture>
                        {canOpen(me, pathname) ? (
                            children
                        ) : (
                            <Refusal title="Your role does not include this page" detail={`You have: ${me.roles.join(", ") || "no console role"}. An owner can grant more under Controls and audit.`} />
                        )}
                    </main>
                </div>
            </div>
        </Ctx.Provider>
    );
}

export function Refusal({ title, detail }: { title: string; detail: string }) {
    return (
        <div className="flex min-h-[50vh] items-center justify-center p-6" role="alert">
            <div className="max-w-md text-center">
                <ShieldAlert aria-hidden className="mx-auto mb-4 h-10 w-10 text-muted-foreground" />
                <h1 className="text-lg font-semibold">{title}</h1>
                <p className="mt-2 text-sm text-muted-foreground">{detail}</p>
                <Button asChild className="mt-6 min-h-11">
                    <Link href="/overview">Back to Decibyl</Link>
                </Button>
            </div>
        </div>
    );
}
