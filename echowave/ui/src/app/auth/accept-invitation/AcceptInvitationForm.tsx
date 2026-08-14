"use client";

/**
 * Claiming an account somebody else created.
 *
 * The invited person has no password — the account is deliberately created
 * without one, because a temporary password mailed out is a working credential
 * sitting in an inbox for as long as the mailbox exists. So this is the only
 * door they can come through, and if it is confusing they are stuck outside.
 *
 * Two decisions follow from that. The email is prefilled from the link and
 * shown read-only when it is: they were invited at a particular address, and
 * letting them edit it just produces "no invitation is waiting for that
 * address" from a form that looked like it was asking. And a wrong code says
 * how many attempts are left, because five is few enough that silently burning
 * them cancels an invitation the person cannot reissue themselves.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { acceptInvitationApiV1AuthAcceptInvitationPost } from "@/client/sdk.gen";
import { AuthShell } from "@/components/auth/AuthShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromError } from "@/lib/apiError";

const MIN_PASSWORD = 8;

export function AcceptInvitationForm() {
    const [email, setEmail] = useState("");
    const [emailFromLink, setEmailFromLink] = useState(false);
    const [code, setCode] = useState("");
    const [name, setName] = useState("");
    const [password, setPassword] = useState("");
    const [confirm, setConfirm] = useState("");
    const [loading, setLoading] = useState(false);

    // Prefilled from the link so the common path is "type a password". Read
    // from window rather than useSearchParams so this needs no Suspense
    // boundary, matching LoginForm.
    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        const invited = params.get("email");
        const sent = params.get("code");
        if (invited) {
            setEmail(invited);
            setEmailFromLink(true);
        }
        if (sent) setCode(sent);
    }, []);

    const passwordsMatch = password === confirm;
    const canSubmit =
        email.trim() &&
        code.trim().length >= 4 &&
        password.length >= MIN_PASSWORD &&
        passwordsMatch &&
        !loading;

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!canSubmit) return;
        setLoading(true);
        try {
            const res = await acceptInvitationApiV1AuthAcceptInvitationPost({
                body: {
                    email: email.trim(),
                    code: code.trim(),
                    password,
                    name: name.trim() || null,
                },
            });
            if (res.error || !res.data) {
                toast.error(
                    detailFromError(res.error, "That invitation could not be accepted"),
                );
                return;
            }

            // Same session hand-off as sign-in: the httpOnly cookie is set by
            // the server route, never by script holding the token.
            await fetch("/api/auth/session", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ token: res.data.token, user: res.data.user }),
            });
            window.location.href = "/after-sign-in";
        } catch {
            toast.error("An error occurred. Please try again.");
        } finally {
            setLoading(false);
        }
    };

    return (
        <AuthShell>
            <div className="space-y-1.5">
                <h1 className="text-2xl font-semibold tracking-tight">
                    Set up your account
                </h1>
                <p className="text-sm text-muted-foreground">
                    Enter the code you were sent and choose a password. That password
                    is the only one — nobody has set one for you.
                </p>
            </div>

            <form onSubmit={handleSubmit} className="space-y-4">
                <div className="space-y-2">
                    <Label htmlFor="invite-email">Email</Label>
                    <Input
                        id="invite-email"
                        type="email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        readOnly={emailFromLink}
                        className={emailFromLink ? "bg-muted text-muted-foreground" : ""}
                        placeholder="you@company.com"
                        required
                    />
                </div>

                <div className="space-y-2">
                    <Label htmlFor="invite-code">Invitation code</Label>
                    <Input
                        id="invite-code"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        value={code}
                        onChange={(e) => setCode(e.target.value)}
                        placeholder="000000"
                        className="tracking-[0.3em]"
                        required
                    />
                </div>

                <div className="space-y-2">
                    <Label htmlFor="invite-name">Your name (optional)</Label>
                    <Input
                        id="invite-name"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="Alex Kumar"
                    />
                </div>

                <div className="space-y-2">
                    <Label htmlFor="invite-password">Choose a password</Label>
                    <Input
                        id="invite-password"
                        type="password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        placeholder={`At least ${MIN_PASSWORD} characters`}
                        required
                    />
                </div>

                <div className="space-y-2">
                    <Label htmlFor="invite-confirm">Confirm password</Label>
                    <Input
                        id="invite-confirm"
                        type="password"
                        value={confirm}
                        onChange={(e) => setConfirm(e.target.value)}
                        required
                    />
                    {/* Only once they have typed something in both, so the
                        warning is never the first thing they see. */}
                    {confirm.length > 0 && !passwordsMatch && (
                        <p className="text-xs text-destructive">
                            Those two do not match.
                        </p>
                    )}
                </div>

                <Button
                    type="submit"
                    className="w-full bg-brand-blue text-white shadow-[0_10px_30px_-12px_rgba(40,179,240,0.6)] hover:bg-brand-blue-hover"
                    disabled={!canSubmit}
                >
                    {loading ? "Setting up..." : "Set password and sign in →"}
                </Button>
            </form>

            <p className="text-center text-sm text-muted-foreground">
                Already set up?{" "}
                <Link
                    href="/auth/login"
                    className="font-medium text-brand-blue underline-offset-4 hover:underline"
                >
                    Sign in
                </Link>
            </p>
        </AuthShell>
    );
}
