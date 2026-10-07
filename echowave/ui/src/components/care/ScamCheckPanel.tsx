"use client";

/**
 * Is this a scam? (launch stream `care`).
 *
 * Paste or say the message, or what the caller said; one button. The answer
 * leads with one plain sentence, then why, then what to do -- and always
 * says that Decibyl never asks for an OTP, PIN or password, and that a list
 * of warning signs can miss a new trick. Nothing asks the person for
 * anything secret, and the words are not kept.
 */

import { AlertTriangle, CheckCircle2, Loader2, ShieldAlert } from "lucide-react";
import { useState } from "react";

import { checkForScamApiV1CareScamCheckPost } from "@/client/sdk.gen";
import type { ScamAnswer } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { cn } from "@/lib/utils";

import { NEVER_ASKS } from "./copy";
import { SpeakButton } from "./SpeakButton";

const TONE: Record<string, { box: string; icon: typeof ShieldAlert }> = {
    likely_scam: {
        box: "border-red-300 bg-red-50 text-red-950 dark:border-red-900 dark:bg-red-950/40 dark:text-red-50",
        icon: ShieldAlert,
    },
    be_careful: {
        box: "border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-50",
        icon: AlertTriangle,
    },
    no_signs_found: {
        box: "border-border bg-muted/40",
        icon: CheckCircle2,
    },
};

export function ScamCheckPanel() {
    const [kind, setKind] = useState<"message" | "call">("message");
    const [text, setText] = useState("");
    const [checking, setChecking] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [answer, setAnswer] = useState<ScamAnswer | null>(null);

    const check = async () => {
        if (!text.trim()) {
            setError(kind === "message" ? "Paste the message first." : "Say what the caller said first.");
            return;
        }
        setChecking(true);
        setError(null);
        const response = await checkForScamApiV1CareScamCheckPost({ body: { text, kind } });
        setChecking(false);
        if (response.error || !response.data) {
            setError(detailFromResult(response, "The check did not run. Try again."));
            return;
        }
        setAnswer(response.data);
    };

    if (answer) {
        const tone = TONE[answer.verdict] ?? TONE.no_signs_found;
        const Icon = tone.icon;
        return (
            <section aria-labelledby="scam-answer" className="flex flex-col gap-4" data-testid="scam-answer">
                <div role="status" className={cn("flex items-start gap-3 rounded-xl border p-4", tone.box)}>
                    <Icon aria-hidden className="mt-0.5 h-6 w-6 shrink-0" />
                    <h3 id="scam-answer" className="text-xl font-semibold leading-snug">
                        {answer.headline}
                    </h3>
                </div>
                {answer.reasons.length > 0 && (
                    <div>
                        <h4 className="mb-2 font-semibold">Why</h4>
                        <ul className="flex flex-col gap-2">
                            {answer.reasons.map((reason) => (
                                <li key={reason.code} className="rounded-lg border border-border p-3 leading-relaxed">
                                    {reason.why}
                                </li>
                            ))}
                        </ul>
                    </div>
                )}
                <div>
                    <h4 className="mb-2 font-semibold">What to do</h4>
                    <ol className="flex list-decimal flex-col gap-2 pl-6 leading-relaxed">
                        {answer.what_to_do.map((step) => (
                            <li key={step}>{step}</li>
                        ))}
                    </ol>
                </div>
                <p className="rounded-lg bg-muted/50 p-3 font-medium">{answer.never_asks}</p>
                <p className="text-sm text-muted-foreground">{answer.limits}</p>
                <Button
                    type="button"
                    className="motion-m1 min-h-12 self-start px-6 text-base"
                    onClick={() => {
                        setAnswer(null);
                        setText("");
                    }}
                >
                    Check something else
                </Button>
            </section>
        );
    }

    return (
        <section className="flex flex-col gap-4" data-testid="scam-form">
            <div role="radiogroup" aria-label="What are you checking?" className="grid grid-cols-2 gap-2">
                {(["message", "call"] as const).map((choice) => (
                    <button
                        key={choice}
                        type="button"
                        role="radio"
                        aria-checked={kind === choice}
                        onClick={() => setKind(choice)}
                        className={cn(
                            "motion-m1 min-h-12 rounded-lg border px-3 text-base font-medium",
                            kind === choice ? "border-foreground bg-foreground text-background" : "border-border hover:bg-muted/50",
                        )}
                    >
                        {choice === "message" ? "A message" : "A phone call"}
                    </button>
                ))}
            </div>
            <label className="flex flex-col gap-2">
                <span className="font-medium">
                    {kind === "message" ? "Paste the message here" : "What did the caller say?"}
                </span>
                <textarea
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    rows={5}
                    maxLength={5000}
                    className="w-full rounded-lg border border-input bg-background p-3 text-base leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    placeholder={
                        kind === "message"
                            ? "For example: Your account will be blocked today. Update KYC at…"
                            : "For example: They said they were from my bank and asked for a code…"
                    }
                />
            </label>
            <SpeakButton onText={(said) => setText((was) => (was ? `${was} ${said}` : said))} />
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
            <Button type="button" className="motion-m1 min-h-12 text-base" disabled={checking} onClick={() => void check()}>
                {checking && <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />}
                {checking ? "Checking…" : "Check it"}
            </Button>
            <p className="text-sm text-muted-foreground">{NEVER_ASKS} What you paste is checked and not kept.</p>
        </section>
    );
}

export default ScamCheckPanel;
