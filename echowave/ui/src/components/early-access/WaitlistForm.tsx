"use client";

/**
 * The waitlist form (screen 01).
 *
 * Submits once: the button is disabled from the first press until the
 * server answers, and the server keeps one row per address anyway. On a
 * failure every value stays where it was. The result replaces the form in
 * the same region, so on a phone the answer is where the thumb already is.
 */

import { CheckCircle2, Loader2 } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useEffect, useId, useState } from "react";

import {
    earlyAccessLanguagesApiV1PublicEarlyAccessLanguagesGet,
    joinWaitlistApiV1PublicEarlyAccessWaitlistPost,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromResult } from "@/lib/apiError";

import { EARLY_ACCESS_COPY as COPY } from "./copy";

export type LanguageOption = { code: string; native: string; english: string; voice: boolean };

/** Shown until the server's list arrives, so the field is never empty. */
export const FALLBACK_LANGUAGES: LanguageOption[] = [{ code: "en", native: "English", english: "English", voice: true }];

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function languageLabel(language: LanguageOption): string {
    return language.native === language.english ? language.native : `${language.native} · ${language.english}`;
}

type Result = "waitlisted" | "already_on_list" | "already_registered" | "invited";

export function WaitlistForm({ renewal = false, initialEmail = "" }: { renewal?: boolean; initialEmail?: string }) {
    const [languages, setLanguages] = useState<LanguageOption[]>(FALLBACK_LANGUAGES);
    const [values, setValues] = useState({
        email: initialEmail,
        name: "",
        language: "en",
        firstTask: "",
        phone: "",
        occupation: "",
    });
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [emailError, setEmailError] = useState<string | null>(null);
    const [result, setResult] = useState<Result | null>(null);
    const ids = { email: useId(), name: useId(), language: useId(), task: useId(), phone: useId(), occupation: useId(), emailHint: useId() };

    useEffect(() => {
        let cancelled = false;
        void (async () => {
            try {
                const response = await earlyAccessLanguagesApiV1PublicEarlyAccessLanguagesGet();
                if (!cancelled && !response.error && response.data?.length) setLanguages(response.data);
            } catch {
                // English stays; the form still works.
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const update = (key: keyof typeof values, value: string) => setValues((was) => ({ ...was, [key]: value }));

    const submit = async (event: FormEvent) => {
        event.preventDefault();
        if (submitting) return;
        if (!EMAIL.test(values.email.trim())) {
            setEmailError(COPY.invalidEmail);
            return;
        }
        setEmailError(null);
        setError(null);
        setSubmitting(true);
        try {
            const response = await joinWaitlistApiV1PublicEarlyAccessWaitlistPost({
                body: {
                    email: values.email.trim(),
                    name: values.name.trim() || null,
                    language: values.language,
                    first_task: values.firstTask.trim() || null,
                    phone: values.phone.trim() || null,
                    occupation: values.occupation.trim() || null,
                    renewal,
                },
            });
            if (response.error || !response.data) {
                setError(detailFromResult(response, COPY.failed));
                return;
            }
            const { state, created } = response.data;
            setResult(
                state === "already_registered"
                    ? "already_registered"
                    : state === "invited"
                      ? "invited"
                      : created
                        ? "waitlisted"
                        : "already_on_list",
            );
        } catch {
            setError(COPY.failed);
        } finally {
            setSubmitting(false);
        }
    };

    if (result) {
        const said =
            result === "waitlisted"
                ? COPY.waitlisted
                : result === "already_on_list"
                  ? COPY.alreadyOnList
                  : result === "invited"
                    ? COPY.invited
                    : COPY.alreadyRegistered;
        return (
            <div role="status" className="motion-m2-enter rounded-[var(--radius)] border border-border p-5" data-testid="waitlist-result" data-result={result}>
                <p className="flex items-center gap-2 text-base font-semibold">
                    <CheckCircle2 aria-hidden className="h-5 w-5 text-[#075A39]" />
                    {said.title}
                </p>
                <p className="mt-2 text-base leading-[26px] text-muted-foreground">{said.body}</p>
                {result === "already_registered" && (
                    <Button asChild className="motion-m1 mt-4 min-h-11">
                        <Link href="/auth/login">{COPY.signIn}</Link>
                    </Button>
                )}
            </div>
        );
    }

    return (
        <form onSubmit={submit} noValidate className="flex flex-col gap-4" data-testid="waitlist-form" aria-busy={submitting}>
            <div className="flex flex-col gap-1.5">
                <Label htmlFor={ids.email}>{COPY.email}</Label>
                <Input
                    id={ids.email}
                    type="email"
                    inputMode="email"
                    autoComplete="email"
                    required
                    value={values.email}
                    aria-invalid={!!emailError}
                    aria-describedby={ids.emailHint}
                    onChange={(event) => update("email", event.target.value)}
                    className="min-h-11 text-base"
                />
                {/* The same line's height either way, so the form does not
                    jump when the hint becomes an error. */}
                <p id={ids.emailHint} className={emailError ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>
                    {emailError ?? COPY.emailHint}
                </p>
            </div>
            <div className="flex flex-col gap-1.5">
                <Label htmlFor={ids.name}>{COPY.name}</Label>
                <Input
                    id={ids.name}
                    autoComplete="name"
                    maxLength={120}
                    value={values.name}
                    onChange={(event) => update("name", event.target.value)}
                    className="min-h-11 text-base"
                />
            </div>
            <div className="flex flex-col gap-1.5">
                <Label htmlFor={ids.language}>{COPY.language}</Label>
                <select
                    id={ids.language}
                    value={values.language}
                    onChange={(event) => update("language", event.target.value)}
                    className="min-h-11 w-full rounded-[var(--radius-control)] border border-input bg-background px-3 text-base leading-[1.6]"
                >
                    {languages.map((language) => (
                        <option key={language.code} value={language.code}>
                            {languageLabel(language)}
                        </option>
                    ))}
                </select>
            </div>
            <div className="flex flex-col gap-1.5">
                <Label htmlFor={ids.task}>{COPY.firstTask}</Label>
                <textarea
                    id={ids.task}
                    rows={3}
                    value={values.firstTask}
                    placeholder={COPY.firstTaskPlaceholder}
                    onChange={(event) => update("firstTask", event.target.value)}
                    className="w-full rounded-[var(--radius-control)] border border-input bg-background px-3 py-2 text-base leading-[1.6]"
                />
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="flex flex-col gap-1.5">
                    <Label htmlFor={ids.phone}>{COPY.phone}</Label>
                    <Input
                        id={ids.phone}
                        type="tel"
                        inputMode="tel"
                        autoComplete="tel"
                        value={values.phone}
                        onChange={(event) => update("phone", event.target.value)}
                        className="min-h-11 text-base"
                    />
                </div>
                <div className="flex flex-col gap-1.5">
                    <Label htmlFor={ids.occupation}>{COPY.occupation}</Label>
                    <Input
                        id={ids.occupation}
                        autoComplete="organization-title"
                        value={values.occupation}
                        onChange={(event) => update("occupation", event.target.value)}
                        className="min-h-11 text-base"
                    />
                </div>
            </div>
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
            <Button type="submit" className="motion-m1 min-h-11 w-full text-base" disabled={submitting}>
                {submitting && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                {submitting ? COPY.submitting : COPY.submit}
            </Button>
        </form>
    );
}

export default WaitlistForm;
