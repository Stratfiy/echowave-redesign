"use client";

/**
 * Settings -> Decibyl identity (screens 23 and 24; launch stream identity).
 *
 * Email: the address lifecycle first, then a small alias form, then what
 * the address does. Copy appears only once the address is active; nothing
 * here presents a mailbox that does not exist. The virtual card is a quiet
 * "coming soon" row with optional interest -- never card details.
 *
 * Phone: the verification and number lifecycle as steps, the next action,
 * and how paying for a number works. The amount is a placeholder until the
 * founder decides who pays; a request is an approval card on this page.
 * Chat works throughout, with or without a number.
 */

import { Check, Copy } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    cardInterestApiV1MeCardInterestPut,
    checkAliasApiV1MeEmailIdentityCheckPost,
    myEmailIdentityApiV1MeEmailIdentityGet,
    myPhoneIdentityApiV1MePhoneIdentityGet,
    proposeNumberApiV1MePhoneIdentityRequestPost,
    proposeSendApiV1MeEmailIdentitySendPost,
    provisionAliasApiV1MeEmailIdentityProvisionPost,
    releaseAliasApiV1MeEmailIdentityReleasePost,
    reserveAliasApiV1MeEmailIdentityReservePost,
} from "@/client/sdk.gen";
import type { AliasCheck, EmailIdentityView, PhoneIdentityView } from "@/client/types.gen";
import { IdentityCardPanel } from "@/components/identity/IdentityCardPanel";
import { useIdentityCards } from "@/components/identity/useIdentityCards";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState, ErrorState, SettingsSection } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { cn } from "@/lib/utils";

const EMAIL_STEPS = [
    { state: "reserved", label: "Reserved" },
    { state: "provisioning", label: "Setting up" },
    { state: "active", label: "Active" },
] as const;

const EMAIL_STATE_LABEL: Record<string, string> = {
    unallocated: "No address yet",
    reserved: "Reserved, not set up",
    provisioning: "Setting up: waiting for the check message",
    active: "Active",
    delivery_issue: "Delivery issue",
    suspended: "Sending paused",
};

function Steps({ steps, current }: { steps: readonly { state: string; label: string }[]; current: string }) {
    // The last step reached is complete, not "in progress".
    const found = steps.findIndex((s) => s.state === current);
    const at = found === steps.length - 1 ? steps.length : found;
    return (
        <ol className="mb-3 flex flex-col gap-1.5 text-sm sm:flex-row sm:flex-wrap sm:gap-4" aria-label="Progress">
            {steps.map((step, i) => (
                <li key={step.state} className={cn("flex items-center gap-1.5", i <= at ? "font-medium" : "text-muted-foreground")} aria-current={i === Math.min(at, steps.length - 1) ? "step" : undefined}>
                    <span
                        aria-hidden
                        className={cn(
                            "flex h-5 w-5 items-center justify-center rounded-full border text-[11px]",
                            i < at ? "border-[#075A39] bg-[#075A39] text-white" : i === at ? "border-foreground" : "border-border",
                        )}
                    >
                        {i < at ? <Check className="h-3 w-3" /> : i + 1}
                    </span>
                    {step.label}
                </li>
            ))}
        </ol>
    );
}

function EmailSection() {
    const { user, loading: authLoading } = useAuth();
    const [view, setView] = useState<EmailIdentityView | null>(null);
    const [failed, setFailed] = useState(false);
    const [alias, setAlias] = useState("");
    const [checked, setChecked] = useState<AliasCheck | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [copied, setCopied] = useState(false);
    const [draft, setDraft] = useState({ to: "", subject: "", body: "" });
    const { cards, refresh: refreshCards } = useIdentityCards(["send_identity_email"]);
    const started = useRef(false);

    const load = useCallback(async () => {
        const res = await myEmailIdentityApiV1MeEmailIdentityGet();
        if (res.error || !res.data) {
            setFailed(true);
            return;
        }
        setFailed(false);
        setView(res.data);
    }, []);

    useEffect(() => {
        if (authLoading || !user || started.current) return;
        started.current = true;
        void load();
    }, [authLoading, user, load]);

    const act = async (fn: () => Promise<{ data?: EmailIdentityView; error?: unknown }>, fallback: string) => {
        setBusy(true);
        setError(null);
        const res = await fn();
        setBusy(false);
        if (res.error || !res.data) {
            setError(detailFromError(res.error, fallback));
            return;
        }
        setView(res.data);
    };

    const check = async () => {
        setError(null);
        const res = await checkAliasApiV1MeEmailIdentityCheckPost({ body: { alias } });
        if (res.error || !res.data) {
            setError(detailFromError(res.error, "Could not check that name. Try again."));
            return;
        }
        setChecked(res.data);
    };

    const send = async () => {
        setError(null);
        const res = await proposeSendApiV1MeEmailIdentitySendPost({ body: draft });
        if (res.error) {
            setError(detailFromError(res.error, "Could not prepare that email."));
            return;
        }
        await refreshCards();
    };

    const toggleInterest = async () => {
        if (!view) return;
        const res = await cardInterestApiV1MeCardInterestPut({ body: { interested: !view.card_interest } });
        if (!res.error && res.data) setView({ ...view, card_interest: res.data.interested });
    };

    if (failed && !view) return <ErrorState title="Could not load your address" onRetry={() => void load()} />;
    if (!view) return <Skeleton className="h-32 w-full" />;

    // The newest few: a long history belongs in Activity, not here.
    const live = cards.filter((card) => !["declined", "cancelled"].includes(card.state)).slice(0, 3);
    return (
        <>
            <SettingsSection
                id="email"
                title="Email"
                description="Your own address at Decibyl. It receives mail for you; it is not a full mailbox."
                scope="Just you"
            >
                <p className="mb-2 text-sm" role="status" data-testid="email-state">
                    {EMAIL_STATE_LABEL[view.state] ?? view.state}
                </p>
                {view.state !== "unallocated" && <Steps steps={EMAIL_STEPS} current={["delivery_issue", "suspended"].includes(view.state) ? "active" : view.state} />}
                {view.next_step && <p className="mb-3 text-sm text-muted-foreground">{view.next_step}</p>}

                {view.address && (
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                        <code className="min-w-0 break-all rounded-md bg-muted px-2 py-1 text-sm" data-testid="email-address">
                            {view.address}
                        </code>
                        <Button
                            type="button"
                            variant="outline"
                            className="min-h-11 md:min-h-9"
                            onClick={() => {
                                void navigator.clipboard?.writeText(view.address ?? "");
                                setCopied(true);
                            }}
                        >
                            {copied ? <Check aria-hidden className="h-4 w-4" /> : <Copy aria-hidden className="h-4 w-4" />} {copied ? "Copied" : "Copy"}
                        </Button>
                    </div>
                )}
                {!view.address && view.pending_address && <p className="mb-3 break-all text-sm">Will be: {view.pending_address}</p>}

                {view.state === "unallocated" && (
                    <form
                        className="flex flex-col gap-2"
                        onSubmit={(e) => {
                            e.preventDefault();
                            void check();
                        }}
                    >
                        <Label htmlFor="alias">Choose a name</Label>
                        <div className="flex flex-wrap items-center gap-2">
                            <Input
                                id="alias"
                                className="min-w-0 flex-1 min-h-11 text-base md:min-h-9 md:text-sm"
                                value={alias}
                                maxLength={32}
                                onChange={(e) => {
                                    setAlias(e.target.value);
                                    setChecked(null);
                                }}
                                aria-describedby="alias-help"
                            />
                            <span className="text-sm text-muted-foreground">@{view.domain}</span>
                        </div>
                        <p id="alias-help" className="text-xs text-muted-foreground">
                            Letters, numbers, dots and hyphens. A name once used is never given to anyone else.
                        </p>
                        <div className="flex flex-wrap gap-2">
                            <Button type="submit" variant="outline" className="min-h-11 md:min-h-9" disabled={!alias.trim()}>
                                Check
                            </Button>
                            {checked?.available && (
                                <Button
                                    type="button"
                                    className="min-h-11 md:min-h-9"
                                    disabled={busy}
                                    onClick={() => void act(() => reserveAliasApiV1MeEmailIdentityReservePost({ body: { alias: checked.alias } }), "Could not reserve it.")}
                                >
                                    Reserve {checked.alias}@{view.domain}
                                </Button>
                            )}
                        </div>
                        {checked && !checked.available && (
                            <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                                {checked.message}
                            </p>
                        )}
                    </form>
                )}
                {(view.state === "reserved" || view.state === "provisioning") && (
                    <div className="flex flex-wrap gap-2">
                        {view.inbound.ready ? (
                            <Button type="button" className="min-h-11 md:min-h-9" disabled={busy} onClick={() => void act(() => provisionAliasApiV1MeEmailIdentityProvisionPost(), "Could not set it up.")}>
                                {view.state === "reserved" ? "Set it up" : "Send the check again"}
                            </Button>
                        ) : (
                            <p className="text-sm text-[#705500] dark:text-amber-300">Needs setup: {view.inbound.reason}</p>
                        )}
                        <Button type="button" variant="ghost" className="min-h-11 md:min-h-9" disabled={busy} onClick={() => void act(() => releaseAliasApiV1MeEmailIdentityReleasePost(), "Could not give it up.")}>
                            Give up this name
                        </Button>
                    </div>
                )}
                {error && (
                    <p role="alert" className="mt-2 text-sm text-[#772322] dark:text-red-300">
                        {error}
                    </p>
                )}

                {view.state === "active" && (
                    <div className="mt-4 border-t border-border pt-4">
                        <p className="mb-2 text-sm font-medium">Send from this address</p>
                        {!view.outbound.ready ? (
                            <p className="text-sm text-[#705500] dark:text-amber-300">Needs setup: {view.outbound.reason}</p>
                        ) : (
                            <form
                                className="flex flex-col gap-2"
                                onSubmit={(e) => {
                                    e.preventDefault();
                                    void send();
                                }}
                            >
                                <Label htmlFor="send-to">To</Label>
                                <Input id="send-to" type="email" className="min-h-11 text-base md:min-h-9 md:text-sm" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
                                <Label htmlFor="send-subject">Subject</Label>
                                <Input id="send-subject" className="min-h-11 text-base md:min-h-9 md:text-sm" maxLength={200} value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} />
                                <Label htmlFor="send-body">Message</Label>
                                <Textarea id="send-body" className="min-h-11 text-base md:min-h-9 md:text-sm" rows={4} value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
                                <Button type="submit" variant="outline" className="min-h-11 self-start md:min-h-9">
                                    Review before sending
                                </Button>
                            </form>
                        )}
                        {live.length > 0 && (
                            <div className="mt-3 flex flex-col gap-3">
                                {live.map((card) => (
                                    <IdentityCardPanel key={card.event_id} card={card} onChanged={() => void refreshCards()} />
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {(view.threads ?? []).length > 0 && (
                    <div className="mt-4 border-t border-border pt-4">
                        <p className="mb-2 text-sm font-medium">Recent mail</p>
                        <ul className="divide-y divide-border">
                            {(view.threads ?? []).map((thread) => (
                                <li key={thread.thread_key} className="py-2 text-sm">
                                    <p className="break-words font-medium">{thread.subject || "(no subject)"}</p>
                                    <p className="break-words text-muted-foreground">
                                        {thread.from_address} · {thread.messages} message{thread.messages === 1 ? "" : "s"}
                                        {thread.attachments.some((a) => (a as { blocked?: boolean }).blocked) && " · an attachment was blocked"}
                                    </p>
                                </li>
                            ))}
                        </ul>
                    </div>
                )}
            </SettingsSection>

            <SettingsSection id="virtual-card" title="Virtual card" description="Coming soon. Nothing is issued and no card details are asked for." scope="Just you">
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                    <span>{view.card_interest ? "We will tell you when it is available." : "Want to hear when it is available?"}</span>
                    <Button type="button" variant="outline" className="min-h-11 md:min-h-9" onClick={() => void toggleInterest()} aria-pressed={view.card_interest}>
                        {view.card_interest ? "No longer interested" : "I'm interested"}
                    </Button>
                </div>
            </SettingsSection>
        </>
    );
}

const PHONE_STEPS = [
    { state: "pending_verification", label: "Verify the business" },
    { state: "eligible", label: "Request a number" },
    { state: "provisioning", label: "Test call and handover" },
    { state: "active", label: "Ready" },
] as const;

const PHONE_STATE_LABEL: Record<string, string> = {
    not_requested: "No number yet",
    pending_verification: "Verification pending",
    rejected: "Verification not accepted",
    eligible: "Verified: a number can be requested",
    provisioning: "Number set up, not tested yet",
    active: "Ready",
    suspended: "Suspended",
    released: "Released",
    unknown: "Could not check",
};

function PhoneSection() {
    const { user, loading: authLoading } = useAuth();
    const [view, setView] = useState<PhoneIdentityView | null>(null);
    const [failed, setFailed] = useState(false);
    const [form, setForm] = useState({ address: "", helper: "" });
    const [error, setError] = useState<string | null>(null);
    const { cards, refresh: refreshCards } = useIdentityCards(["request_number"]);
    const started = useRef(false);

    const load = useCallback(async () => {
        const res = await myPhoneIdentityApiV1MePhoneIdentityGet();
        if (res.error || !res.data) {
            setFailed(true);
            return;
        }
        setFailed(false);
        setView(res.data);
    }, []);

    useEffect(() => {
        if (authLoading || !user || started.current) return;
        started.current = true;
        void load();
    }, [authLoading, user, load]);

    const request = async () => {
        setError(null);
        const res = await proposeNumberApiV1MePhoneIdentityRequestPost({
            body: { address: form.address, helper_id: Number(form.helper) },
        });
        if (res.error) {
            setError(detailFromError(res.error, "Could not prepare that request."));
            return;
        }
        await refreshCards();
    };

    if (failed && !view) return <ErrorState title="Could not load phone and verification" onRetry={() => void load()} />;
    if (!view) return <Skeleton className="h-32 w-full" />;

    const failedSources = Object.entries(view.sources).filter(([, v]) => v === "failed").map(([k]) => k);
    const live = cards.filter((card) => !["declined", "cancelled"].includes(card.state)).slice(0, 3);
    return (
        <SettingsSection id="phone" title="Phone and verification" description="Chat and everything else work without a number." scope="Your workspace">
            <p className="mb-2 text-sm" role="status" data-testid="phone-state">
                {PHONE_STATE_LABEL[view.state] ?? view.state}
            </p>
            <Steps steps={PHONE_STEPS} current={view.state === "rejected" || view.state === "not_requested" ? "pending_verification" : view.state} />
            {view.next_step && <p className="mb-3 text-sm text-muted-foreground">{view.next_step}</p>}
            {failedSources.length > 0 && (
                <p role="alert" className="mb-3 text-sm text-[#705500] dark:text-amber-300">
                    Could not check: {failedSources.join(", ")}. What is shown may be incomplete.
                </p>
            )}

            {view.numbers && view.numbers.length > 0 && (
                <ul className="mb-3 divide-y divide-border">
                    {view.numbers.map((number) => {
                        const helper = view.helpers?.find((h) => h.id === number.assigned_helper_id);
                        return (
                            <li key={number.id} className="py-2 text-sm">
                                <p className="font-medium">{number.address}</p>
                                <p className="text-muted-foreground">
                                    {PHONE_STATE_LABEL[number.state] ?? number.state}
                                    {helper?.name && ` · answers as ${helper.name}`}
                                    {number.incoming_call_ok_at ? " · test call passed" : " · test call not recorded"}
                                    {number.escalation_ok_at ? " · handover passed" : " · handover not recorded"}
                                </p>
                            </li>
                        );
                    })}
                </ul>
            )}

            <div className="rounded-[var(--radius)] border border-border p-3 text-sm" data-testid="number-payment">
                <p className="mb-1 font-medium">How paying for a number works</p>
                <ol className="mb-2 list-decimal space-y-1 pl-5">
                    {view.payment.steps.map((step) => (
                        <li key={step}>{step}</li>
                    ))}
                </ol>
                <p>{view.payment.who_pays}</p>
                <p className="mt-1">
                    Amount: <span data-placeholder="number-amount">{view.payment.amount}</span>
                </p>
            </div>

            {view.request.available && view.is_admin ? (
                <form
                    className="mt-3 flex flex-col gap-2"
                    onSubmit={(e) => {
                        e.preventDefault();
                        void request();
                    }}
                >
                    <Label htmlFor="number-address">Number to request</Label>
                    <Input id="number-address" inputMode="tel" className="min-h-11 text-base md:min-h-9 md:text-sm" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
                    <Label htmlFor="number-helper">Helper who answers</Label>
                    <select
                        id="number-helper"
                        className="min-h-11 rounded-md border border-input bg-background px-2 min-h-11 text-base md:min-h-9 md:text-sm"
                        value={form.helper}
                        onChange={(e) => setForm({ ...form, helper: e.target.value })}
                    >
                        <option value="">Choose a helper</option>
                        {(view.helpers ?? []).map((helper) => (
                            <option key={helper.id} value={helper.id}>
                                {helper.name}
                            </option>
                        ))}
                    </select>
                    <Button type="submit" variant="outline" className="min-h-11 self-start md:min-h-9" disabled={!form.helper || !form.address}>
                        Review the request
                    </Button>
                </form>
            ) : (
                view.request.reason && <p className="mt-3 text-sm text-muted-foreground">{view.request.reason}</p>
            )}
            {error && (
                <p role="alert" className="mt-2 text-sm text-[#772322] dark:text-red-300">
                    {error}
                </p>
            )}
            {live.length > 0 && (
                <div className="mt-3 flex flex-col gap-3">
                    {live.map((card) => (
                        <IdentityCardPanel key={card.event_id} card={card} onChanged={() => void refreshCards().then(load)} />
                    ))}
                </div>
            )}
        </SettingsSection>
    );
}

export default function IdentityPage() {
    const email = useFeature("identity_email");
    const phone = useFeature("identity_phone");
    return (
        <>
            <PageHeader title="Decibyl identity" description="Your Decibyl email address, and phone and verification." />
            <PageBody className="flex max-w-[640px] flex-col gap-6 px-4 md:px-6">
                {email && <EmailSection />}
                {phone && <PhoneSection />}
                {!email && !phone && <EmptyState title="Not switched on yet" description="This will appear when it is turned on for your workspace." />}
            </PageBody>
        </>
    );
}
