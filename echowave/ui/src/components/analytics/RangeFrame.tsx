"use client";

/**
 * The frame for a screen that reads a window of days: the page header with
 * its tab strip, and a 7 / 30 / 90 day picker beside the title.
 *
 * It was app/analytics/layout.tsx, which gave Analytics and Spend a header of
 * their own (a larger title, a different tab strip) unlike every other page.
 * The redesign (UI-0) moved Spend under Billing, where its tab already was,
 * and both screens onto the shared PageHeader.
 *
 * The range lives in the URL rather than in the page's state, so a link to
 * "last 90 days" is a link somebody can send.
 */

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { type ReactNode, Suspense } from "react";

import { PageBody, PageHeader, type PageTab } from "@/components/layout/PageHeader";
import { cn } from "@/lib/utils";

export const WINDOWS = [7, 30, 90] as const;

export function RangePicker() {
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const active = Number(searchParams.get("days")) || 30;

    return (
        <div className="flex items-center gap-1 rounded-lg border p-0.5" role="group" aria-label="Date range">
            {WINDOWS.map((days) => (
                <Link
                    key={days}
                    href={`${pathname}?days=${days}`}
                    aria-current={active === days ? "true" : undefined}
                    className={cn(
                        "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                        active === days ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground",
                    )}
                >
                    {days}d
                </Link>
            ))}
        </div>
    );
}

export function RangeFrame({
    title,
    description,
    tabs,
    children,
}: {
    title: string;
    description: ReactNode;
    tabs: PageTab[];
    children: ReactNode;
}) {
    return (
        <>
            <PageHeader
                title={title}
                description={description}
                tabs={tabs}
                actions={
                    // useSearchParams needs a Suspense boundary or the route
                    // opts out of static rendering at build time.
                    <Suspense fallback={<div className="h-8 w-28" />}>
                        <RangePicker />
                    </Suspense>
                }
            />
            <PageBody>
                <Suspense fallback={<div className="h-64" />}>{children}</Suspense>
            </PageBody>
        </>
    );
}
