"use client";

/**
 * The account's own numbers.
 *
 * Everything here already existed for staff and for nobody else: spend
 * composition, per-model token usage, context growth, call shape. A customer
 * on a prepaid balance could see a table of runs and a single token total,
 * which answers "what happened" and none of "why is my bill that number".
 *
 * Three tabs, in the order the questions get asked: how are my calls going,
 * what are they consuming, and what is that costing me.
 */

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { cn } from "@/lib/utils";

const TABS = [
    { href: "/analytics", label: "Calls", exact: true },
    { href: "/analytics/tokens", label: "Tokens" },
    { href: "/analytics/spend", label: "Spend" },
];

const WINDOWS = [7, 30, 90] as const;

/**
 * The range lives in the URL rather than in each page's state.
 *
 * Two reasons: switching tabs keeps the window you were looking at — comparing
 * spend against the calls that caused it is the whole point of having them side
 * by side — and a link to "last 90 days" is a link someone can send.
 */
function RangePicker() {
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const active = Number(searchParams.get("days")) || 30;

    return (
        <div className="flex items-center gap-1 rounded-lg border p-0.5">
            {WINDOWS.map((days) => (
                <Link
                    key={days}
                    href={`${pathname}?days=${days}`}
                    className={cn(
                        "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                        active === days
                            ? "bg-accent text-foreground"
                            : "text-muted-foreground hover:text-foreground",
                    )}
                >
                    {days}d
                </Link>
            ))}
        </div>
    );
}

function Tabs() {
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const days = searchParams.get("days");
    const query = days ? `?days=${days}` : "";

    return (
        <nav className="flex gap-1">
            {TABS.map((tab) => {
                const active = tab.exact
                    ? pathname === tab.href
                    : pathname.startsWith(tab.href);
                return (
                    <Link
                        key={tab.href}
                        href={`${tab.href}${query}`}
                        className={cn(
                            "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                            active
                                ? "bg-accent text-foreground"
                                : "text-muted-foreground hover:text-foreground",
                        )}
                    >
                        {tab.label}
                    </Link>
                );
            })}
        </nav>
    );
}

export default function AnalyticsLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    return (
        <div className="container mx-auto space-y-6 p-6">
            <div>
                <h1 className="mb-1 text-3xl font-bold">Analytics</h1>
                <p className="text-muted-foreground">
                    What your agents did, what they consumed, and what it cost.
                </p>
            </div>

            {/* useSearchParams needs a Suspense boundary or the whole route
                opts out of static rendering at build time. */}
            <Suspense fallback={<div className="h-9" />}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <Tabs />
                    <RangePicker />
                </div>
            </Suspense>

            <Suspense fallback={<div className="h-64" />}>{children}</Suspense>
        </div>
    );
}
