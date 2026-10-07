"use client";

/**
 * One group of settings: a heading, one sentence on what it changes, and
 * the controls. Built on the Card the Settings pages already use, with an
 * id so a search result or a link from Chat can land on it (handoff: every
 * detail has a stable deep link).
 */

import type { ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export function SettingsSection({
    id,
    title,
    description,
    scope,
    children,
    className,
}: {
    id?: string;
    title: string;
    description?: ReactNode;
    /** Whose setting this is: "Just you" or the workspace's name. */
    scope?: string;
    children: ReactNode;
    className?: string;
}) {
    const headingId = id ? `${id}-heading` : undefined;
    return (
        <Card id={id} aria-labelledby={headingId} className={cn("scroll-mt-16", className)} role="region">
            <CardHeader>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <CardTitle id={headingId}>{title}</CardTitle>
                    {scope && (
                        <span className="rounded-[var(--radius-pill)] border border-border px-2 py-0.5 text-xs text-muted-foreground">
                            {scope}
                        </span>
                    )}
                </div>
                {description && <CardDescription>{description}</CardDescription>}
            </CardHeader>
            <CardContent>{children}</CardContent>
        </Card>
    );
}

export default SettingsSection;
