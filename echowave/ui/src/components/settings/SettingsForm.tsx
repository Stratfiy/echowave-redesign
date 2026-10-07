"use client";

/**
 * The frame every Settings shell form shares (screens 17-19): honest loading
 * and failure, the conflict view with both versions, and the SaveBar with
 * Save and Discard sitting above the keyboard on a phone.
 */

import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";

import type { Profile } from "@/client/types.gen";
import { ErrorState } from "@/components/shell/ErrorState";
import { SaveBar } from "@/components/shell/SaveBar";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import type { Draft, ProfileField, useProfileForm } from "./useProfileForm";

type Form = ReturnType<typeof useProfileForm>;

function shown(value: unknown): string {
    if (value === null || value === undefined || value === "") return "Not set";
    if (typeof value === "boolean") return value ? "On" : "Off";
    return String(value);
}

/** Both versions, side by side, after somebody else saved first. */
export function ConflictNotice({
    labels,
    draft,
    theirs,
    onKeepMine,
    onUseSaved,
}: {
    labels: Partial<Record<ProfileField, string>>;
    draft: Draft;
    theirs: Profile;
    onKeepMine: () => void;
    onUseSaved: () => void;
}) {
    const differing = (Object.keys(labels) as ProfileField[]).filter(
        (field) => (draft[field] ?? null) !== (theirs[field] ?? null),
    );
    return (
        <section
            role="alert"
            aria-label="Changed somewhere else"
            className="mb-4 rounded-[var(--radius)] border border-[#705500]/40 bg-[#705500]/5 p-4"
            data-testid="settings-conflict"
        >
            <p className="flex items-center gap-1.5 text-sm font-medium text-[#705500] dark:text-amber-300">
                <AlertTriangle aria-hidden className="h-4 w-4" />
                These were changed somewhere else while you were editing.
            </p>
            <dl className="mt-3 grid gap-2 text-sm">
                {differing.map((field) => (
                    <div key={field} className="grid gap-1 sm:grid-cols-[10rem_1fr_1fr] sm:gap-3">
                        <dt className="text-muted-foreground">{labels[field]}</dt>
                        <dd className="min-w-0 break-words">
                            <span className="text-xs text-muted-foreground">Yours: </span>
                            {shown(draft[field])}
                        </dd>
                        <dd className="min-w-0 break-words">
                            <span className="text-xs text-muted-foreground">Saved now: </span>
                            {shown(theirs[field])}
                        </dd>
                    </div>
                ))}
            </dl>
            <div className="mt-3 flex flex-wrap gap-2">
                <Button type="button" className="motion-m1 min-h-11 md:min-h-9" onClick={onKeepMine}>
                    Keep mine
                </Button>
                <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={onUseSaved}>
                    Use what is saved
                </Button>
            </div>
        </section>
    );
}

export function SettingsFormFrame({
    form,
    labels,
    children,
}: {
    form: Form;
    labels: Partial<Record<ProfileField, string>>;
    children: ReactNode;
}) {
    if (form.phase === "loading") {
        return (
            <div className="space-y-3" aria-busy="true" data-testid="settings-loading">
                {[0, 1, 2].map((i) => (
                    <Skeleton key={i} className="h-20 w-full" />
                ))}
            </div>
        );
    }
    if (form.phase === "failed") {
        // A failed read is never shown as empty settings.
        return (
            <ErrorState
                title="Could not load your settings"
                description={form.message ?? "Nothing was changed."}
                onRetry={() => void form.reload()}
            />
        );
    }
    return (
        <div className="pb-24 md:pb-4">
            {form.state === "conflict" && form.theirs && (
                <ConflictNotice
                    labels={labels}
                    draft={form.draft}
                    theirs={form.theirs}
                    onKeepMine={form.keepMine}
                    onUseSaved={form.takeSaved}
                />
            )}
            <div className="flex flex-col gap-6">{children}</div>
            <SaveBar
                state={form.state}
                message={form.message}
                onSave={() => void form.save()}
                onDiscard={form.discard}
                className="fixed inset-x-0 bottom-0 md:sticky md:mt-6 md:rounded-[var(--radius)] md:border"
            />
        </div>
    );
}

/** One labelled control with one short helper line (screen 18). */
export function Field({
    id,
    label,
    help,
    error,
    children,
}: {
    id: string;
    label: string;
    help?: ReactNode;
    error?: string | null;
    children: ReactNode;
}) {
    return (
        <div id={id} className="scroll-mt-20">
            <label htmlFor={`${id}-input`} className="block text-sm font-medium">
                {label}
            </label>
            {help && <p className="mt-0.5 text-xs text-muted-foreground">{help}</p>}
            <div className="mt-2">{children}</div>
            {error && (
                <p role="alert" className="mt-1 text-xs text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}

export const INPUT =
    "h-11 w-full min-w-0 rounded-[8px] border border-[#7B8491]/60 bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring md:h-9 md:text-sm";
