"use client";

/**
 * Screen 02: language, a confirmed timezone, then one task into Chat.
 *
 * Two short groups and a composer, nothing compulsory beyond reaching Chat:
 * no integrations checklist, no KYC, no model key. Memory and the daily
 * brief stay off until the person chooses them. The answers are the
 * person's own (`/shell/onboarding`), never the workspace's defaults.
 *
 * Starting the task saves and completes in one request, then opens Chat
 * with the task sent (`/overview?ask=`). A save that fails keeps every
 * answer and the task, and says the change was not saved.
 */

import { ArrowRight, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import {
    getOnboardingApiV1ShellOnboardingGet,
    saveOnboardingApiV1ShellOnboardingPut,
    skipOnboardingApiV1ShellOnboardingSkipPost,
} from "@/client/sdk.gen";
import type { LanguageOption } from "@/components/early-access/WaitlistForm";
import { ErrorState } from "@/components/shell/ErrorState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

import { LanguagePicker } from "./LanguagePicker";
import { detectedTimezone, TimezoneConfirm } from "./TimezoneConfirm";

export const SAVE_FAILED = "Your change was not saved. Try again.";

/** The browser's language, if it is one we offer; English otherwise. */
export function preferredLanguage(languages: readonly LanguageOption[], navigatorLanguage?: string): string {
    const code = (navigatorLanguage ?? (typeof navigator !== "undefined" ? navigator.language : "en")).toLowerCase();
    const base = code.split("-")[0];
    // Odia is "or" in browsers and "od" in Sarvam's list.
    const alias = base === "or" ? "od" : base;
    return languages.some((language) => language.code === alias) ? alias : "en";
}

/** Where Chat opens with the first task already asked. */
export function chatWithTask(task: string): string {
    const trimmed = task.trim();
    return trimmed ? `/overview?ask=${encodeURIComponent(trimmed)}` : "/overview";
}

type Phase = "loading" | "ready" | "failed";

export function FirstTaskOnboarding() {
    const router = useRouter();
    const { user, loading: authLoading } = useAuth();
    const fetched = useRef(false);
    const [phase, setPhase] = useState<Phase>("loading");
    const [languages, setLanguages] = useState<LanguageOption[]>([]);
    const [language, setLanguage] = useState("en");
    const [timezone, setTimezone] = useState(detectedTimezone);
    const [confirmed, setConfirmed] = useState(false);
    const [name, setName] = useState("");
    const [task, setTask] = useState("");
    const [saving, setSaving] = useState<"start" | "skip" | null>(null);
    const [error, setError] = useState<string | null>(null);
    const ids = { name: useId(), task: useId(), tzHint: useId() };

    const load = async () => {
        setPhase("loading");
        const response = await getOnboardingApiV1ShellOnboardingGet();
        if (response.error || !response.data) {
            // Off for this workspace: Chat is where they belong.
            if ((response as { response?: Response }).response?.status === 404) {
                router.replace("/overview");
                return;
            }
            setPhase("failed");
            return;
        }
        const data = response.data;
        if (data.completed) {
            router.replace("/overview");
            return;
        }
        setLanguages(data.languages);
        setLanguage(data.language ?? preferredLanguage(data.languages));
        if (data.timezone) setTimezone(data.timezone);
        setConfirmed(!!data.timezone_confirmed);
        setName(data.preferred_name ?? "");
        setPhase("ready");
    };

    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [authLoading, user]);

    const start = async (event: FormEvent) => {
        event.preventDefault();
        if (saving) return;
        if (!confirmed) {
            setError("Confirm your timezone before continuing.");
            return;
        }
        setError(null);
        setSaving("start");
        try {
            const response = await saveOnboardingApiV1ShellOnboardingPut({
                body: { language, timezone, timezone_confirmed: true, preferred_name: name.trim() || null, complete: true },
            });
            if (response.error) {
                setError(detailFromResult(response, SAVE_FAILED));
                return;
            }
            router.replace(chatWithTask(task));
        } catch {
            setError(SAVE_FAILED);
        } finally {
            setSaving(null);
        }
    };

    const skip = async () => {
        if (saving) return;
        setSaving("skip");
        setError(null);
        try {
            const response = await skipOnboardingApiV1ShellOnboardingSkipPost();
            if (response.error) {
                setError(detailFromResult(response, SAVE_FAILED));
                return;
            }
            router.replace("/overview");
        } catch {
            setError(SAVE_FAILED);
        } finally {
            setSaving(null);
        }
    };

    if (phase === "loading") {
        return (
            <p role="status" className="flex items-center gap-2 text-base text-muted-foreground">
                <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" /> Getting things ready…
            </p>
        );
    }
    if (phase === "failed") {
        return <ErrorState title="Could not open your setup" description="Nothing was changed." onRetry={() => void load()} />;
    }

    return (
        <form onSubmit={start} className="flex flex-col gap-8" data-testid="first-task-onboarding" aria-busy={!!saving}>
            <section aria-labelledby="onboarding-language" className="flex flex-col gap-3">
                <h2 id="onboarding-language" className="text-base font-semibold">
                    Your language
                </h2>
                <LanguagePicker languages={languages} value={language} onChange={setLanguage} />
            </section>

            <section aria-labelledby="onboarding-timezone" className="flex flex-col gap-3">
                <h2 id="onboarding-timezone" className="text-base font-semibold">
                    Your timezone
                </h2>
                <p id={ids.tzHint} className="text-sm text-muted-foreground">
                    We found this from your device. Reminders and your daily summary use it.
                </p>
                <TimezoneConfirm
                    value={timezone}
                    confirmed={confirmed}
                    onChange={(zone, isConfirmed) => {
                        setTimezone(zone);
                        setConfirmed(isConfirmed);
                    }}
                />
            </section>

            <section className="flex flex-col gap-1.5">
                <Label htmlFor={ids.name}>What should Decibyl call you? (optional)</Label>
                <Input
                    id={ids.name}
                    autoComplete="given-name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    className="min-h-11 text-base"
                />
            </section>

            <section aria-labelledby="onboarding-task" className="flex flex-col gap-2">
                <h2 id="onboarding-task" className="text-base font-semibold">
                    Your first task
                </h2>
                <Label htmlFor={ids.task} className="text-sm font-normal text-muted-foreground">
                    Ask for anything. If it needs an app, Decibyl will ask then, not before.
                </Label>
                <textarea
                    id={ids.task}
                    rows={3}
                    value={task}
                    onChange={(event) => setTask(event.target.value)}
                    placeholder="For example: help me plan today"
                    className="w-full rounded-[var(--radius-control)] border border-input bg-background px-3 py-2 text-base leading-[1.6]"
                />
                <p className="text-xs text-muted-foreground">Memory and the daily summary stay off until you turn them on.</p>
            </section>

            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}

            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-between">
                <Button type="button" variant="ghost" className="motion-m1 min-h-11" disabled={!!saving} onClick={() => void skip()}>
                    {saving === "skip" ? "Opening Chat…" : "Skip for now"}
                </Button>
                <Button type="submit" className="motion-m1 min-h-11 text-base" disabled={!!saving}>
                    {saving === "start" && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                    {task.trim() ? "Start in Chat" : "Go to Chat"}
                    <ArrowRight aria-hidden />
                </Button>
            </div>
        </form>
    );
}

export default FirstTaskOnboarding;
