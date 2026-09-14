"use client";

/**
 * The door between signing up and the workspace (KAN-132).
 *
 * Email/password accounts land here straight from the signup form, with the
 * six-digit code already on its way. Entering it proves the address and pays
 * the first 150 free credits; only then does the account go through to the
 * product. Before this the person arrived in a workspace with a banner, hit
 * the wall later when they bought a number, and the free credit landed at a
 * moment nobody was looking at it.
 *
 * Accounts that arrive already verified (Google, Stack, or a code entered
 * earlier) are sent straight on: the page is a door, not a wall.
 */

import { MailCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
  getAuthUserApiV1UserAuthUserGet,
  resendEmailVerificationApiV1AuthEmailResendPost,
  verifyEmailApiV1AuthEmailVerifyPost,
} from "@/client/sdk.gen";
import { AuthShell } from "@/components/auth/AuthShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { announceBalanceChanged } from "@/lib/billing/balanceEvents";

const NEXT = "/after-sign-in";

export default function VerifyEmailPage() {
  const { user, loading: authLoading } = useAuth();
  const [address, setAddress] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const response = await getAuthUserApiV1UserAuthUserGet();
    if (response.error || !response.data) {
      setChecked(true);
      return;
    }
    if (response.data.email_verified !== false) {
      window.location.href = NEXT;
      return;
    }
    setAddress(response.data.email ?? null);
    setChecked(true);
  }, []);

  useEffect(() => {
    if (authLoading) return;
    if (!user) {
      window.location.href = "/auth/login";
      return;
    }
    void load();
  }, [authLoading, user, load]);

  const verify = async () => {
    setBusy(true);
    const response = await verifyEmailApiV1AuthEmailVerifyPost({ body: { code } });
    setBusy(false);
    if (response.error) {
      toast.error(detailFromResult(response, "That code was not accepted."));
      return;
    }
    const granted = (response.data as { bonus_granted_paise?: number } | undefined)
      ?.bonus_granted_paise;
    if (granted) announceBalanceChanged();
    toast.success(
      granted
        ? `Verified. ${Math.floor(granted / 50)} free credits are in.`
        : "Email verified.",
    );
    window.location.href = NEXT;
  };

  const resend = async () => {
    setBusy(true);
    const response = await resendEmailVerificationApiV1AuthEmailResendPost({});
    setBusy(false);
    if (response.error) {
      toast.error(detailFromResult(response, "Could not send a code."));
      return;
    }
    toast[response.data?.sent ? "success" : "message"](
      response.data?.sent
        ? "A new code is on its way."
        : "A code was sent recently — check your inbox, including spam.",
    );
  };

  if (!checked) return null;

  return (
    <AuthShell>
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight" data-testid="verify-title">
          Check your email
        </h1>
        <p className="text-sm text-muted-foreground">
          We sent a six-digit code to{" "}
          {address ? <strong className="font-medium text-foreground">{address}</strong> : "your address"}.
          Enter it and your first 150 free credits land.
        </p>
      </div>

      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (code.length === 6 && !busy) void verify();
        }}
        data-testid="verify-form"
      >
        <div className="flex items-center gap-3">
          <MailCheck className="h-5 w-5 shrink-0 text-muted-foreground" />
          <Input
            value={code}
            onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
            placeholder="000000"
            aria-label="Six-digit verification code"
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            className="h-11 font-mono text-lg tracking-[0.3em]"
            data-testid="verify-code-input"
          />
        </div>
        <Button type="submit" className="w-full" disabled={busy || code.length < 6} data-testid="verify-submit">
          Verify and continue
        </Button>
      </form>

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <button type="button" className="underline-offset-2 hover:underline" onClick={() => void resend()} disabled={busy}>
          Send the code again
        </button>
        <a href={NEXT} className="underline-offset-2 hover:underline">
          Do this later
        </a>
      </div>
    </AuthShell>
  );
}
