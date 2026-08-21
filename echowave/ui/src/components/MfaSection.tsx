"use client";

/**
 * Two-factor authentication for this account.
 *
 * The backend has had enroll/verify/disable since before this component
 * existed, with nowhere in the product to reach any of them — an account
 * could never actually turn MFA on. The login form already knew what to do
 * with `mfa_required`; this is the other half, the one that makes that code
 * path reachable at all.
 *
 * Enrolling and enabling are two separate steps on purpose. Enabling on the
 * strength of a freshly-generated secret alone — before anything has proved
 * the authenticator app actually captured it — risks locking the account out
 * with only the recovery codes as a way back, which is exactly the situation
 * those codes exist to prevent, not to create. So `enroll` stores a pending
 * secret and `mfa_enabled` stays false until `verify` proves a real code
 * comes back out of it.
 *
 * No QR code here — the secret is shown as text for manual entry into an
 * authenticator app (Google Authenticator, 1Password, Authy, etc.). Every
 * such app accepts a pasted secret; a QR scan is a convenience on top of
 * that, not a requirement.
 */

import { AlertTriangle, Check, Copy, Loader2, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    disableMfaApiV1AuthMfaDisablePost,
    enrollMfaApiV1AuthMfaEnrollPost,
    getCurrentUserApiV1AuthMeGet,
    verifyMfaApiV1AuthMfaVerifyPost,
} from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export function MfaSection() {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);

    const [mfaEnabled, setMfaEnabled] = useState<boolean | null>(null);
    const [loading, setLoading] = useState(true);

    // Enrollment in progress: the secret and recovery codes are shown exactly
    // once, right after enroll returns them — there is no way to read them
    // back afterwards.
    const [enrolling, setEnrolling] = useState(false);
    const [secret, setSecret] = useState("");
    const [uri, setUri] = useState("");
    const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
    const [verifyCode, setVerifyCode] = useState("");
    const [enrollBusy, setEnrollBusy] = useState(false);
    const [enrollError, setEnrollError] = useState<string | null>(null);

    const [disabling, setDisabling] = useState(false);
    const [disablePassword, setDisablePassword] = useState("");
    const [disableCode, setDisableCode] = useState("");
    const [disableBusy, setDisableBusy] = useState(false);
    const [disableError, setDisableError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        const result = await getCurrentUserApiV1AuthMeGet();
        if (!result.error && result.data) {
            setMfaEnabled(Boolean(result.data.mfa_enabled));
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void load();
    }, [authLoading, user, load]);

    const startEnroll = async () => {
        setEnrollBusy(true);
        setEnrollError(null);
        const result = await enrollMfaApiV1AuthMfaEnrollPost();
        if (result.error || !result.data) {
            setEnrollError(detailFromError(result.error, "Could not start enrolment"));
        } else {
            setSecret(result.data.secret);
            setUri(result.data.uri);
            setRecoveryCodes(result.data.recovery_codes);
            setVerifyCode("");
            setEnrolling(true);
        }
        setEnrollBusy(false);
    };

    const finishEnroll = async () => {
        if (verifyCode.trim().length < 6) {
            setEnrollError("Enter the 6-digit code from your authenticator app.");
            return;
        }
        setEnrollBusy(true);
        setEnrollError(null);
        const result = await verifyMfaApiV1AuthMfaVerifyPost({
            body: { code: verifyCode.trim() },
        });
        if (result.error) {
            setEnrollError(detailFromError(result.error, "Invalid authentication code"));
        } else {
            toast.success("Two-factor authentication is on");
            setEnrolling(false);
            setSecret("");
            setUri("");
            setVerifyCode("");
            setMfaEnabled(true);
            // Recovery codes stay on screen until the account owner dismisses
            // them — this is genuinely the only time they are ever shown.
        }
        setEnrollBusy(false);
    };

    const confirmDisable = async () => {
        if (!disablePassword || disableCode.trim().length < 6) {
            setDisableError("Enter your password and a current 6-digit code.");
            return;
        }
        setDisableBusy(true);
        setDisableError(null);
        const result = await disableMfaApiV1AuthMfaDisablePost({
            body: { password: disablePassword, code: disableCode.trim() },
        });
        if (result.error) {
            setDisableError(detailFromError(result.error, "Could not disable MFA"));
        } else {
            toast.success("Two-factor authentication is off");
            setDisabling(false);
            setDisablePassword("");
            setDisableCode("");
            setMfaEnabled(false);
        }
        setDisableBusy(false);
    };

    const copyRecoveryCodes = () => {
        navigator.clipboard
            .writeText(recoveryCodes.join("\n"))
            .then(() => toast.success("Recovery codes copied"))
            .catch(() => toast.error("Failed to copy"));
    };

    if (loading) {
        return <Skeleton className="h-24 w-full" />;
    }

    // recoveryCodes.length > 0 with enrolling still true is the "just
    // finished verify, show the codes once more before closing" window —
    // handled inline below rather than as a separate state, since it is the
    // same screen with the input hidden.
    if (enrolling) {
        return (
            <div className="space-y-4">
                {recoveryCodes.length > 0 && mfaEnabled !== true && (
                    <>
                        <div className="space-y-2">
                            <Label>1. Add this secret to your authenticator app</Label>
                            <div className="flex items-center gap-2">
                                <code className="flex-1 truncate rounded-md border bg-muted px-3 py-2 text-sm">
                                    {secret}
                                </code>
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="icon"
                                    onClick={() => {
                                        navigator.clipboard
                                            .writeText(secret)
                                            .then(() => toast.success("Secret copied"))
                                            .catch(() => toast.error("Failed to copy"));
                                    }}
                                    aria-label="Copy secret"
                                >
                                    <Copy className="h-4 w-4" />
                                </Button>
                            </div>
                            <p className="text-xs text-muted-foreground">
                                Google Authenticator, 1Password, Authy — any TOTP app accepts a
                                pasted secret. ({uri ? "otpauth URI also captured" : ""})
                            </p>
                        </div>

                        <div className="space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3">
                            <p className="flex items-center gap-1.5 text-sm font-medium text-amber-800 dark:text-amber-300">
                                <AlertTriangle className="h-3.5 w-3.5" />
                                Recovery codes — shown once, save them now
                            </p>
                            <div className="grid grid-cols-2 gap-1 font-mono text-xs">
                                {recoveryCodes.map((code) => (
                                    <span key={code}>{code}</span>
                                ))}
                            </div>
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={copyRecoveryCodes}
                            >
                                <Copy className="mr-1.5 h-3.5 w-3.5" />
                                Copy all
                            </Button>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="mfa-verify-code">
                                2. Enter the code your app is showing now
                            </Label>
                            <div className="flex gap-2">
                                <Input
                                    id="mfa-verify-code"
                                    value={verifyCode}
                                    onChange={(e) => setVerifyCode(e.target.value)}
                                    placeholder="000000"
                                    maxLength={6}
                                    className="w-32"
                                    autoComplete="one-time-code"
                                />
                                <Button
                                    type="button"
                                    onClick={() => void finishEnroll()}
                                    disabled={enrollBusy}
                                >
                                    {enrollBusy && (
                                        <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                                    )}
                                    Turn on
                                </Button>
                                <Button
                                    type="button"
                                    variant="ghost"
                                    onClick={() => {
                                        setEnrolling(false);
                                        setSecret("");
                                        setRecoveryCodes([]);
                                    }}
                                    disabled={enrollBusy}
                                >
                                    Cancel
                                </Button>
                            </div>
                            {enrollError && (
                                <p className="text-sm text-destructive">{enrollError}</p>
                            )}
                        </div>
                    </>
                )}
            </div>
        );
    }

    if (mfaEnabled) {
        return (
            <div className="space-y-3">
                <div className="flex items-center gap-2">
                    <Badge className="gap-1 bg-green-600 hover:bg-green-600">
                        <ShieldCheck className="h-3 w-3" />
                        Enabled
                    </Badge>
                    <span className="text-sm text-muted-foreground">
                        A code from your authenticator app is required at every sign-in.
                    </span>
                </div>

                {!disabling ? (
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setDisabling(true)}
                    >
                        Disable
                    </Button>
                ) : (
                    <div className="space-y-2 rounded-lg border p-3">
                        <p className="text-xs text-muted-foreground">
                            Needs your password and a current code — a stolen session alone
                            must not be enough to strip this off.
                        </p>
                        <div className="grid gap-2 sm:grid-cols-2">
                            <div className="space-y-1">
                                <Label htmlFor="mfa-disable-password" className="text-xs">
                                    Password
                                </Label>
                                <Input
                                    id="mfa-disable-password"
                                    type="password"
                                    value={disablePassword}
                                    onChange={(e) => setDisablePassword(e.target.value)}
                                />
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="mfa-disable-code" className="text-xs">
                                    Current code
                                </Label>
                                <Input
                                    id="mfa-disable-code"
                                    value={disableCode}
                                    onChange={(e) => setDisableCode(e.target.value)}
                                    placeholder="000000"
                                    maxLength={6}
                                />
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                            <Button
                                type="button"
                                variant="destructive"
                                size="sm"
                                onClick={() => void confirmDisable()}
                                disabled={disableBusy}
                            >
                                {disableBusy && (
                                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                                )}
                                Confirm disable
                            </Button>
                            <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={() => {
                                    setDisabling(false);
                                    setDisablePassword("");
                                    setDisableCode("");
                                    setDisableError(null);
                                }}
                                disabled={disableBusy}
                            >
                                Cancel
                            </Button>
                        </div>
                        {disableError && (
                            <p className="text-xs text-destructive">{disableError}</p>
                        )}
                    </div>
                )}
            </div>
        );
    }

    return (
        <div className="space-y-2">
            <div className="flex items-center gap-2">
                <Badge variant="outline" className="text-muted-foreground">
                    Not enabled
                </Badge>
                <span className="text-sm text-muted-foreground">
                    Anyone with your password alone can sign in.
                </span>
            </div>
            <Button type="button" size="sm" onClick={() => void startEnroll()} disabled={enrollBusy}>
                {enrollBusy ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                    <Check className="mr-1.5 h-3.5 w-3.5" />
                )}
                Enable two-factor authentication
            </Button>
            {enrollError && <p className="text-sm text-destructive">{enrollError}</p>}
        </div>
    );
}
