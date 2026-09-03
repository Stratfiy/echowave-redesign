"use client";

import { MailCheck, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
  getAuthUserApiV1UserAuthUserGet,
  resendEmailVerificationApiV1AuthEmailResendPost,
  verifyEmailApiV1AuthEmailVerifyPost,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { detailFromError, detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

/**
 * The prompt to finish email verification.
 *
 * A banner rather than a wall. Nothing in the product refuses an unverified
 * account — every account that predates the feature is unverified, and locking
 * those people out to enforce a rule introduced after they signed up would be
 * an outage dressed as a security improvement. So this asks, and stays out of
 * the way of someone who wants to get on with their work.
 *
 * It carries the code field itself instead of linking to a page. The code is in
 * an email the user is already reading; making them navigate somewhere to type
 * six digits is a step that exists only because it was easier to build.
 */
export function VerifyEmailBanner() {
  const { user, loading: authLoading } = useAuth();
  const [needed, setNeeded] = useState(false);
  const [address, setAddress] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  // Dismissal is per-tab and not persisted: the address still needs proving
  // tomorrow, and a banner that can be permanently silenced by one click is a
  // banner that never gets acted on.
  const [dismissed, setDismissed] = useState(false);

  const refresh = useCallback(async () => {
    const response = await getAuthUserApiV1UserAuthUserGet();
    if (response.error || !response.data) return;
    setNeeded(response.data.email_verified === false);
    setAddress(response.data.email ?? null);
  }, []);

  useEffect(() => {
    if (authLoading || !user) return;
    void refresh();
  }, [authLoading, user, refresh]);

  if (!needed || dismissed) return null;

  const verify = async () => {
    setBusy(true);
    const response = await verifyEmailApiV1AuthEmailVerifyPost({
      body: { code },
    });
    setBusy(false);
    if (response.error) {
      toast.error(detailFromResult(response, "That code was not accepted."));
      return;
    }
    toast.success("Email verified.");
    setNeeded(false);
  };

  const resend = async () => {
    setBusy(true);
    const response = await resendEmailVerificationApiV1AuthEmailResendPost({});
    setBusy(false);
    if (response.error) {
      toast.error(detailFromError(response.error, "Could not send a code."));
      return;
    }
    // The endpoint reports whether it actually went out. Saying "sent" when it
    // was rate-limited would have the user waiting for a message that is not
    // coming, which is the failure this whole banner exists to avoid.
    toast[response.data?.sent ? "success" : "message"](
      response.data?.sent
        ? "A new code is on its way."
        : "A code was sent recently — check your inbox, including spam."
    );
  };

  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-border bg-[var(--tint-amber)] px-6 py-2.5 text-sm">
      <MailCheck className="h-4 w-4 shrink-0 text-foreground/70" />
      <span className="min-w-0">
        Verify {address ? <strong className="font-medium">{address}</strong> : "your email"} —
        enter the six-digit code we sent you.
      </span>
      <div className="flex items-center gap-2">
        <Input
          value={code}
          onChange={(event) =>
            setCode(event.target.value.replace(/\D/g, "").slice(0, 6))
          }
          placeholder="000000"
          aria-label="Six-digit verification code"
          inputMode="numeric"
          autoComplete="one-time-code"
          className="h-8 w-28 bg-card font-mono tracking-[0.2em]"
        />
        <Button size="sm" onClick={() => void verify()} disabled={busy || code.length < 6}>
          Verify
        </Button>
        <Button size="sm" variant="ghost" onClick={() => void resend()} disabled={busy}>
          Resend
        </Button>
      </div>
      <Button
        variant="ghost"
        size="icon"
        aria-label="Dismiss"
        className="ml-auto h-7 w-7"
        onClick={() => setDismissed(true)}
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}

export default VerifyEmailBanner;
