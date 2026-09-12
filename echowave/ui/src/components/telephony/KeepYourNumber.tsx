"use client";

/**
 * Forward the number a business already has, instead of publishing a new one.
 *
 * The step that decides whether a clinic can go live at all. Its number is on
 * the board outside, on prescription pads and in its Maps listing; none of
 * those change, so a new number reaches nobody who already knows them.
 * Forwarding keeps the number and sends only the calls it cannot answer to an
 * agent.
 *
 * In the product rather than only in the docs, because this is read standing
 * on the screen where somebody is about to buy a number they may not need —
 * and a guide nobody finds is the same as no guide.
 *
 * The codes are standard GSM and go to the operator from the customer's own
 * SIM. We cannot set them, and saying so plainly is part of the instruction:
 * an operator waiting for Decibyl to do it waits forever.
 */

import { Check, ChevronDown, Copy, PhoneForwarded } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";

/** The placeholder shown where we do not know the agent's number yet. Kept
 *  obviously unreal so nobody dials it, rather than a plausible-looking
 *  number somebody might. */
const PLACEHOLDER = "<your Decibyl number>";

type ForwardCase = {
    key: string;
    label: string;
    when: string;
    on: string;
    off: string;
    /** The one most front desks actually want, marked so the screen has an
     *  opinion rather than four equal options. */
    recommended?: boolean;
};

const CASES: ForwardCase[] = [
    {
        key: "busy",
        label: "When the line is busy",
        when: "Somebody is already on a call — the second caller reaches the agent instead of an engaged tone.",
        on: "**67*",
        off: "##67#",
        recommended: true,
    },
    {
        key: "no-reply",
        label: "When nobody picks up",
        when: "It rings out. The agent answers rather than the call being lost.",
        on: "**61*",
        off: "##61#",
        recommended: true,
    },
    {
        key: "unreachable",
        label: "When the phone is off or out of range",
        when: "Switched off, no signal, flight mode.",
        on: "**62*",
        off: "##62#",
    },
    {
        key: "always",
        label: "Every call",
        when: "Nothing rings at your end. Use this for nights and Sundays, switching it off when you open.",
        on: "**21*",
        off: "##21#",
    },
];

function CodeRow({ code, label }: { code: string; label: string }) {
    const [copied, setCopied] = useState(false);

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(code);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            // Clipboard is blocked in some browsers and every embedded
            // webview. The code is on screen and readable either way, so this
            // fails quietly rather than claiming it copied.
        }
    };

    return (
        <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/30 px-2.5 py-1.5">
            <div className="min-w-0">
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
                    {label}
                </p>
                <code className="break-all text-sm">{code}</code>
            </div>
            <Button
                size="sm"
                variant="ghost"
                className="shrink-0"
                onClick={() => void copy()}
                aria-label={`Copy ${label} code`}
            >
                {copied ? (
                    <Check className="h-3.5 w-3.5" />
                ) : (
                    <Copy className="h-3.5 w-3.5" />
                )}
            </Button>
        </div>
    );
}

export function KeepYourNumber({ agentNumber }: { agentNumber?: string | null }) {
    const [open, setOpen] = useState(false);
    const target = agentNumber?.trim() || PLACEHOLDER;

    return (
        <Card>
            <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                    <PhoneForwarded className="h-4 w-4" />
                    Keep the number you already have
                </CardTitle>
                <CardDescription>
                    Your number stays on your board, your cards and your Maps listing.
                    Only the calls it cannot answer reach the agent — so you do not have
                    to tell anybody a new number.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 pb-4">
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setOpen((previous) => !previous)}
                >
                    {open ? "Hide" : "Show me how"}
                    <ChevronDown
                        className={`ml-1 h-3.5 w-3.5 transition-transform ${
                            open ? "rotate-180" : ""
                        }`}
                    />
                </Button>

                {open && (
                    <div className="space-y-4">
                        <p className="text-sm text-muted-foreground">
                            Dial these from the phone whose calls you want forwarded,
                            like a phone number. You set them, not us — the instruction
                            goes to your operator from your own SIM, so no platform can
                            do it for you.
                        </p>

                        {!agentNumber && (
                            <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100">
                                Give the agent a number first, or attach one — the codes
                                below need somewhere to forward to.
                            </p>
                        )}

                        <div className="space-y-3">
                            {CASES.map((item) => (
                                <div key={item.key} className="space-y-1.5">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="text-sm font-medium">
                                            {item.label}
                                        </span>
                                        {item.recommended && (
                                            <span className="rounded-full bg-[var(--accent-brand-soft)] px-2 py-0.5 text-[11px] font-medium text-[var(--accent-brand)]">
                                                Most front desks use this
                                            </span>
                                        )}
                                    </div>
                                    <p className="text-xs text-muted-foreground">
                                        {item.when}
                                    </p>
                                    <div className="grid gap-2 sm:grid-cols-2">
                                        <CodeRow
                                            label="Turn on"
                                            code={`${item.on}${target}#`}
                                        />
                                        <CodeRow label="Turn off" code={item.off} />
                                    </div>
                                </div>
                            ))}
                        </div>

                        <div className="space-y-2 rounded-md border border-border bg-muted/20 p-3">
                            <p className="text-sm font-medium">Then check it took</p>
                            <p className="text-xs text-muted-foreground">
                                Dial <code>*#21#</code> to see what your line is
                                forwarding and where. Do this every time — the
                                confirmation a handset shows after dialling is sometimes
                                shown whether or not your operator accepted it.
                            </p>
                            <p className="text-xs text-muted-foreground">
                                Then ring your own number from another phone and let it
                                ring out. The agent should answer. If your voicemail
                                answers instead, the forward did not take.
                            </p>
                        </div>

                        <p className="text-xs text-muted-foreground">
                            On a <strong>landline</strong>, and on some networks, these
                            are set by the operator rather than by dialling. If{" "}
                            <code>*#21#</code> does not confirm it, call your operator
                            and ask for &ldquo;call forwarding on busy and no
                            answer&rdquo; to this number. It is a routine request.
                        </p>

                        <p className="text-xs text-muted-foreground">
                            Your operator charges you for forwarding the call to us, on
                            your own plan. We charge for the call the agent answers. A
                            forwarded call therefore costs a little more than a call
                            straight to a Decibyl number — worth knowing before the
                            first bill.
                        </p>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
