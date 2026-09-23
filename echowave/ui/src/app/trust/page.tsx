"use client";

/**
 * Trust: what happens to the data, and who else touches it — no account.
 *
 * A security review happens before somebody signs up, not after. Every fact
 * here comes from `/public/trust`, which derives them from the code that does
 * the thing: the sub-processors are the vendors this deployment holds keys
 * for plus anything a call was actually billed against, and the retention
 * numbers are the ones the deletion job reads. Nothing on this page is typed
 * out beside the system it describes, because a trust page maintained by hand
 * is a set of specific written promises that go false the first time somebody
 * adds a vendor and forgets.
 *
 * The per-account answer — who processed *your* calls — stays behind a login,
 * on Privacy. Answering that for a stranger would itself be a disclosure.
 */

import { AlertTriangle, Clock, Database, Loader2, MapPin, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { resolveBrowserBackendUrl } from "@/lib/apiClient";

type Subprocessor = {
    name: string;
    purpose: string;
    data: string;
    basis: string;
};

type Trust = {
    region: string;
    retention: {
        recording_days: number;
        transcript_days: number;
        subprocessor_window_days: number;
    };
    subprocessors: Subprocessor[];
    grievance_officer: { name: string | null; email: string | null; address: string | null };
};

/** What put a vendor on the list, in words rather than a code. */
const BASIS: Record<string, string> = {
    configured: "We hold a key for it",
    observed: "A call was billed against it",
    infrastructure: "Runs the platform itself",
};

/** The region code as somewhere on a map. Unknown codes print as themselves
 *  rather than being guessed at: a wrong city is worse than a bare code. */
const REGIONS: Record<string, string> = {
    "ap-south-1": "Mumbai, India",
    "ap-southeast-1": "Singapore",
    "us-east-1": "Northern Virginia, USA",
    "eu-west-1": "Ireland",
};

function Section({
    icon: Icon,
    title,
    children,
}: {
    icon: typeof ShieldCheck;
    title: string;
    children: React.ReactNode;
}) {
    return (
        <section className="border-t border-border pt-6">
            <h2 className="flex items-center gap-2 text-base font-semibold">
                <Icon className="h-4 w-4 text-[var(--accent-brand)]" aria-hidden />
                {title}
            </h2>
            <div className="mt-3 space-y-3 text-sm leading-relaxed text-muted-foreground">
                {children}
            </div>
        </section>
    );
}

export default function TrustPage() {
    const [data, setData] = useState<Trust | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const response = await fetch(
                    `${resolveBrowserBackendUrl()}/api/v1/public/trust`,
                );
                if (!response.ok) throw new Error(String(response.status));
                const body = (await response.json()) as Trust;
                if (!cancelled) setData(body);
            } catch {
                if (!cancelled) setError("Could not load this right now. Try again in a moment.");
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    return (
        <main className="mx-auto w-full max-w-3xl px-6 py-12">
            <p className="text-xs font-semibold uppercase tracking-wider text-[var(--accent-brand)]">
                Decibyl
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight">Trust</h1>
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
                Agents answer your phone, so this page is about the recording of
                somebody&apos;s voice and what it says. Everything below is read
                from the running system rather than written down beside it, so a
                vendor cannot be added without this page saying so.
            </p>

            {error && (
                <p className="mt-8 flex items-center gap-2 text-sm text-destructive" role="alert">
                    <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
                    {error}
                </p>
            )}

            {!data && !error && (
                <p className="mt-8 flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    Reading the current list…
                </p>
            )}

            {data && (
                <div className="mt-10 space-y-8">
                    <Section icon={MapPin} title="Where it lives">
                        <p>
                            Calls, recordings, transcripts and your account&apos;s own
                            records are stored in{" "}
                            <span className="font-medium text-foreground">
                                {REGIONS[data.region] ?? data.region}
                            </span>
                            . Model and carrier vendors listed below process what a call
                            needs while it is happening; where each of those runs is
                            theirs to state, not ours.
                        </p>
                    </Section>

                    <Section icon={Clock} title="How long it is kept">
                        <ul className="space-y-1.5">
                            <li>
                                <span className="font-medium text-foreground">
                                    Recordings: {data.retention.recording_days} days
                                </span>{" "}
                                by default.
                            </li>
                            <li>
                                <span className="font-medium text-foreground">
                                    Transcripts and call context:{" "}
                                    {data.retention.transcript_days} days
                                </span>{" "}
                                by default.
                            </li>
                        </ul>
                        <p>
                            Both are yours to shorten per account, and a number you can
                            erase on request is erased for good — the operation is
                            irreversible by design, because a right to erasure satisfied
                            by something recoverable is not satisfied.
                        </p>
                    </Section>

                    <Section icon={Database} title="Who else processes it">
                        <p>
                            Derived, not maintained: a vendor appears here because this
                            deployment holds a key for it, because a call in the last{" "}
                            {data.retention.subprocessor_window_days} days was billed
                            against it, or because it runs the platform itself.
                        </p>
                        <div className="overflow-x-auto">
                            <table className="mt-2 w-full min-w-[34rem] border-collapse text-left text-sm">
                                <thead>
                                    <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                                        <th className="py-2 pr-4 font-medium">Who</th>
                                        <th className="py-2 pr-4 font-medium">What for</th>
                                        <th className="py-2 pr-4 font-medium">What they see</th>
                                        <th className="py-2 font-medium">Why listed</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.subprocessors.map((row) => (
                                        <tr key={`${row.name}-${row.basis}`} className="border-b border-border/60 align-top">
                                            <td className="py-2.5 pr-4 font-medium text-foreground">
                                                {row.name}
                                            </td>
                                            <td className="py-2.5 pr-4">{row.purpose}</td>
                                            <td className="py-2.5 pr-4">{row.data}</td>
                                            <td className="py-2.5 text-xs">
                                                {BASIS[row.basis] ?? row.basis}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        {data.subprocessors.length === 0 && (
                            <p>Nothing is listed yet on this deployment.</p>
                        )}
                    </Section>

                    <Section icon={ShieldCheck} title="What you can do about it">
                        <p>
                            Every account can export its own data, erase a caller&apos;s
                            number from its calls, set its own retention and read who
                            inside it looked at what — from Privacy, in the product, no
                            support ticket. The list above, narrowed to the vendors that
                            actually handled that account&apos;s calls, is on the same
                            screen.
                        </p>
                        {data.grievance_officer.email && (
                            <p>
                                Complaints and data requests:{" "}
                                <a
                                    className="font-medium text-foreground underline underline-offset-4"
                                    href={`mailto:${data.grievance_officer.email}`}
                                >
                                    {data.grievance_officer.email}
                                </a>
                                {data.grievance_officer.name ? ` (${data.grievance_officer.name})` : ""}
                                {data.grievance_officer.address
                                    ? `, ${data.grievance_officer.address}`
                                    : ""}
                                .
                            </p>
                        )}
                        <p className="flex flex-wrap gap-x-4 gap-y-1">
                            <a
                                className="font-medium text-foreground underline underline-offset-4"
                                href="https://decibyl.ai/legal/terms"
                            >
                                Terms, including what an agent may not be used for
                            </a>
                            <a
                                className="font-medium text-foreground underline underline-offset-4"
                                href="https://decibyl.ai/legal/privacy"
                            >
                                Privacy policy
                            </a>
                            <Link
                                className="font-medium text-foreground underline underline-offset-4"
                                href="/privacy"
                            >
                                Your account&apos;s own controls
                            </Link>
                        </p>
                    </Section>
                </div>
            )}
        </main>
    );
}
