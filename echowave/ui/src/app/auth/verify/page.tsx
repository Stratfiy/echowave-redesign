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

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
  getAuthUserApiV1UserAuthUserGet,
  resendEmailVerificationApiV1AuthEmailResendPost,
  verifyEmailApiV1AuthEmailVerifyPost,
} from "@/client/sdk.gen";
import { AUTH_COPY } from "@/components/auth/steps/copy";
import { STEP_INPUT_CLASS, StepAction, StepError, StepShell } from "@/components/auth/steps/StepShell";
import { Input } from "@/components/ui/input";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { announceBalanceChanged } from "@/lib/billing/balanceEvents";
import { useFeature } from "@/lib/features";

const NEXT = "/after-sign-in";
const ERROR_ID = "verify-step-error";
const copy = AUTH_COPY.verify;

export default function VerifyEmailPage() {
  const { user, loading: authLoading } = useAuth();
  // Free while we are early: no credits to promise (free_mode.py).
  const freeMode = useFeature("free_mode");
  const [address, setAddress] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      setError(detailFromResult(response, copy.rejected));
      return;
    }
    const granted = (response.data as { bonus_granted_paise?: number } | undefined)
      ?.bonus_granted_paise;
    if (granted) announceBalanceChanged();
    toast.success(
      granted
        ? `Verified. ${Math.floor(granted / 50)} free credits are in.`
        : copy.verified,
    );
    window.location.href = NEXT;
  };

  const resend = async () => {
    setBusy(true);
    const response = await resendEmailVerificationApiV1AuthEmailResendPost({});
    setBusy(false);
    if (response.error) {
      setError(detailFromResult(response, copy.resendFailed));
      return;
    }
    toast[response.data?.sent ? "success" : "message"](
      response.data?.sent ? copy.resent : copy.recentlySent,
    );
  };

  if (!checked) return null;

  return (
    <StepShell
      stepKey="verify"
      title={<span data-testid="verify-title">{copy.title}</span>}
      hint={
        <>
          {copy.sentTo}{" "}
          {address ? <strong className="font-medium text-foreground">{address}</strong> : copy.yourAddress}.{" "}
          {!freeMode && copy.bonus}
        </>
      }
      footer={
        <div className="flex items-center justify-between">
          <button
            type="button"
            className="underline-offset-4 hover:text-foreground hover:underline"
            onClick={() => void resend()}
            disabled={busy}
          >
            {copy.resend}
          </button>
          <a href={NEXT} className="underline-offset-4 hover:text-foreground hover:underline">
            {copy.later}
          </a>
        </div>
      }
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (code.length === 6 && !busy) void verify();
        }}
        noValidate
        data-testid="verify-form"
      >
        <Input
          value={code}
          onChange={(event) => {
            setCode(event.target.value.replace(/\D/g, "").slice(0, 6));
            setError(null);
          }}
          placeholder="000000"
          aria-label={copy.label}
          aria-invalid={error ? true : undefined}
          aria-describedby={ERROR_ID}
          inputMode="numeric"
          autoComplete="one-time-code"
          autoFocus
          className={`${STEP_INPUT_CLASS} text-center font-mono text-xl tracking-[0.4em] md:text-xl`}
          data-testid="verify-code-input"
        />
        <StepError id={ERROR_ID} message={error} />
        <StepAction disabled={busy || code.length < 6} testId="verify-submit">
          {copy.submit}
        </StepAction>
      </form>
    </StepShell>
  );
}
