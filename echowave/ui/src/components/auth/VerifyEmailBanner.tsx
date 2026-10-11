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
import { detailFromResult } from "@/lib/apiError";
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
      toast.error(detailFromResult(response, "Could not send a code."));
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

  // A phone gets two rows: what to do, then the code. Four rows of wrapped
  // sentence sat above every screen and cost a quarter of it.
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border bg-[var(--tint-amber)] px-4 py-2 text-sm md:px-6 md:py-2.5">
      <MailCheck className="h-4 w-4 shrink-0 text-foreground/70" />
      <span className="min-w-0 flex-1 truncate md:flex-none md:whitespace-normal">
        Verify {address ? <strong className="font-medium">{address}</strong> : "your email"}
        <span className="hidden md:inline">
          {" "}— enter the six-digit code we sent you.
        </span>
      </span>
      <Button
        variant="ghost"
        size="icon"
        aria-label="Dismiss"
        className="ml-auto h-7 w-7 shrink-0 md:order-last"
        onClick={() => setDismissed(true)}
      >
        <X className="h-4 w-4" />
      </Button>
      <div className="flex w-full items-center gap-2 md:w-auto">
        <Input
          value={code}
          onChange={(event) =>
            setCode(event.target.value.replace(/\D/g, "").slice(0, 6))
          }
          placeholder="6-digit code"
          aria-label="Six-digit verification code"
          inputMode="numeric"
          autoComplete="one-time-code"
          className="h-8 min-w-0 flex-1 bg-card font-mono tracking-[0.2em] placeholder:font-sans placeholder:tracking-normal md:w-32 md:flex-none"
        />
        <Button size="sm" onClick={() => void verify()} disabled={busy || code.length < 6}>
          Verify
        </Button>
        <Button size="sm" variant="ghost" onClick={() => void resend()} disabled={busy}>
          Resend
        </Button>
      </div>
    </div>
  );
}

export default VerifyEmailBanner;
