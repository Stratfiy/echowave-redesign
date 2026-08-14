"use client";

/**
 * Standing up an account for a customer.
 *
 * The case this exists for: somebody has been sold a subscription over a call
 * and should find their account waiting rather than being told to go and sign
 * up. Distinct from the team screen, which adds a colleague to an organization
 * that already exists.
 *
 * No password is set here and none is generated, and the screen says so —
 * because the obvious expectation is that creating an account produces
 * credentials to read out over the phone, and staff who expect that will go
 * looking for them. What it produces is a code the customer uses to choose
 * their own password.
 */

import { AlertTriangle, Check, Loader2, Mail, UserPlus } from "lucide-react";
import { useState } from "react";

import { createAccountApiV1AdminAccountsPost } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

type Created = {
    email: string;
    expires_at: string;
    email_sent: boolean;
    email_error: string | null;
};

export default function AdminAccountsPage() {
    const { user, loading: authLoading } = useAuth();

    const [email, setEmail] = useState("");
    const [note, setNote] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // A list rather than one, because staff creating accounts do it in a run
    // and need to see which of the batch actually went out.
    const [created, setCreated] = useState<Created[]>([]);

    const submit = async () => {
        const address = email.trim();
        if (!address) return;
        setBusy(true);
        setError(null);
        const result = await createAccountApiV1AdminAccountsPost({
            body: { email: address, note: note.trim() || null },
        });
        if (result.error) {
            setError(detailFromError(result.error, "Could not create that account"));
        } else {
            setCreated((prev) => [result.data as unknown as Created, ...prev]);
            setEmail("");
            setNote("");
        }
        setBusy(false);
    };

    if (authLoading || !user) {
        return null;
    }

    return (
        <div className="glass-canvas min-h-full">
            <div className="mx-auto w-full max-w-[900px] px-6 pb-10 pt-8">
                <header className="mb-6">
                    <h1 className="text-[2.125rem] font-semibold leading-[1.1] tracking-[-0.045em] text-foreground">
                        Create an account
                    </h1>
                    <p className="mt-1.5 max-w-2xl text-[0.9375rem] leading-relaxed tracking-[-0.011em] text-muted-foreground">
                        For a customer who has been sold a subscription and should find
                        it waiting. They get a code and choose their own password —
                        there is no password here to read out to them.
                    </p>
                </header>

                <section className="glass-panel px-5 pb-5 pt-4">
                    <div className="flex flex-wrap items-end gap-3">
                        <div className="min-w-[240px] flex-1 space-y-1.5">
                            <Label htmlFor="account-email" className="text-xs">
                                Customer email
                            </Label>
                            <Input
                                id="account-email"
                                type="email"
                                autoComplete="off"
                                placeholder="customer@clinic.com"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter") void submit();
                                }}
                            />
                        </div>
                        <div className="min-w-[200px] flex-1 space-y-1.5">
                            <Label htmlFor="account-note" className="text-xs">
                                Note (staff only)
                            </Label>
                            <Input
                                id="account-note"
                                placeholder="Signed up on the 12th, Telangana"
                                value={note}
                                onChange={(e) => setNote(e.target.value)}
                            />
                        </div>
                        <Button onClick={() => void submit()} disabled={busy || !email.trim()}>
                            {busy ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <UserPlus className="mr-2 h-4 w-4" />
                            )}
                            Create and invite
                        </Button>
                    </div>

                    <p className="mt-3 flex items-start gap-1.5 text-xs text-muted-foreground">
                        <Mail className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        The organization is created when they accept, so an invitation
                        nobody claims leaves nothing behind. The code lasts 72 hours.
                    </p>

                    {error && (
                        <p className="mt-3 flex items-start gap-1.5 text-xs text-destructive">
                            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            {error}
                        </p>
                    )}
                </section>

                {created.length > 0 && (
                    <section className="glass-panel mt-5 px-5 pb-4 pt-4">
                        <p className="mb-2 text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                            Created this session
                        </p>
                        <ul className="space-y-2 text-sm">
                            {created.map((row) => (
                                <li
                                    key={`${row.email}-${row.expires_at}`}
                                    className="flex flex-wrap items-center gap-2"
                                >
                                    {row.email_sent ? (
                                        <Check className="h-4 w-4 shrink-0 text-foreground" />
                                    ) : (
                                        <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
                                    )}
                                    <span className="font-medium text-foreground">
                                        {row.email}
                                    </span>
                                    {row.email_sent ? (
                                        <span className="text-xs text-muted-foreground">
                                            invited · expires{" "}
                                            {row.expires_at.slice(0, 10)}
                                        </span>
                                    ) : (
                                        // Reported rather than swallowed: the
                                        // invitation is staged either way, and
                                        // staff who are not told will send a
                                        // customer to an inbox nothing arrived in.
                                        <span className="text-xs text-destructive">
                                            created, but the email failed (
                                            {row.email_error}). Fix mail delivery and
                                            create it again.
                                        </span>
                                    )}
                                </li>
                            ))}
                        </ul>
                    </section>
                )}
            </div>
        </div>
    );
}
