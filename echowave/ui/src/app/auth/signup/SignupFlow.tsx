"use client";

/**
 * Sign-up, one question per screen: Google or email → invite code (only
 * while invite-only is on) → name → password → agreement → account.
 *
 * Enter advances, Back goes back, and a server refusal lands on the step it
 * is about with the reason under the input — a taken email returns to the
 * email, a refused invite to the invite code — rather than a toast over a
 * form the person has already left.
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import posthog from "posthog-js";
import { type FormEvent, useState } from "react";

import { signupApiV1AuthSignupPost } from "@/client/sdk.gen";
import { GoogleSignInButton } from "@/components/auth/GoogleSignInButton";
import { AUTH_COPY } from "@/components/auth/steps/copy";
import { LegalLinks, PasswordInput } from "@/components/auth/steps/fields";
import {
  type SignupPath,
  type SignupStep,
  signupSteps,
  type SignupValues,
  stepForSignupError,
  validateSignupStep,
} from "@/components/auth/steps/signupSteps";
import { STEP_INPUT_CLASS, StepAction, StepError, StepShell } from "@/components/auth/steps/StepShell";
import { useGoogleSignIn } from "@/components/auth/useGoogleSignIn";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PostHogEvent } from "@/constants/posthog-events";
import { detailFromResult } from "@/lib/apiError";
import { useFeature } from "@/lib/features";

type StepErrors = Partial<Record<SignupStep, string>>;

const ERROR_ID = "signup-step-error";
const copy = AUTH_COPY.signup;

export function SignupFlow() {
  // A partner's referral code, from the link they handed out. Sent with the
  // signup rather than stored: attribution happens once, at provisioning.
  const searchParams = useSearchParams();
  const referralCode = searchParams.get("ref");
  // Invite-only (INVITE-1, KAN-273). The invite email links here with
  // `?invite=` (older links: `?code=`) so the step arrives filled.
  const inviteOnly = useFeature("invite_only_signup");
  // Set when the server refuses an invite the flag had not asked for yet —
  // the flag answers after first paint, the server is the authority.
  const [inviteNeeded, setInviteNeeded] = useState(false);
  const inviteRequired = inviteOnly || inviteNeeded;

  const google = useGoogleSignIn();

  const [values, setValues] = useState<SignupValues>(() => ({
    email: "",
    inviteCode: searchParams.get("invite") ?? searchParams.get("code") ?? "",
    name: "",
    password: "",
    agreed: false,
  }));
  const [path, setPath] = useState<SignupPath>("email");
  const [step, setStep] = useState<SignupStep>("start");
  const [errors, setErrors] = useState<StepErrors>({});
  const [submitting, setSubmitting] = useState(false);

  const steps = signupSteps({ inviteRequired, path });
  const index = Math.max(0, steps.indexOf(step));
  const error = errors[step];

  const update = <K extends keyof SignupValues>(key: K, value: SignupValues[K]) => {
    setValues((previous) => ({ ...previous, [key]: value }));
    if (errors[step]) setErrors((previous) => ({ ...previous, [step]: undefined }));
  };

  const goTo = (target: SignupStep, message?: string) => {
    setErrors((previous) => ({ ...previous, [target]: message }));
    setStep(target);
  };

  const back = () => {
    if (index === 0) return;
    const previous = steps[index - 1];
    if (previous === "start") setPath("email");
    setStep(previous);
  };

  const leaveForGoogle = async () => {
    const problem = await google.start({
      referralCode,
      inviteCode: values.inviteCode.trim() || null,
    });
    if (problem) goTo(step, problem);
  };

  const chooseGoogle = () => {
    if (inviteRequired) {
      setPath("google");
      goTo("invite");
      return;
    }
    void leaveForGoogle();
  };

  const submit = async () => {
    setSubmitting(true);
    // Before the request, so a sign-up that never returns still counts as an
    // attempt. The email is not sent: the user is identified once signed in.
    posthog.capture(PostHogEvent.SIGNUP_SUBMITTED, { referred: Boolean(referralCode) });
    try {
      const res = await signupApiV1AuthSignupPost({
        body: {
          email: values.email.trim(),
          password: values.password,
          name: values.name.trim() || null,
          referral_code: referralCode,
          invite_code: values.inviteCode.trim() || null,
          // The same two keys the server's click-wrap checks for; only sent
          // once the box on the agreement step is ticked.
          accepted_agreements: ["terms", "privacy"],
        },
      });
      if (res.error || !res.data) {
        const detail = detailFromResult(res, copy.failed);
        posthog.capture(PostHogEvent.SIGNUP_FAILED, { reason: detail });
        const target = stepForSignupError(res.response?.status, detail);
        if (target === "invite") setInviteNeeded(true);
        goTo(target, detail);
        return;
      }
      posthog.capture(PostHogEvent.SIGNUP_SUCCEEDED, { referred: Boolean(referralCode) });
      await fetch("/api/auth/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: res.data.token, user: res.data.user }),
      });
      // The code screen comes before the workspace (KAN-132).
      window.location.href = res.data.email_verification_required ? "/auth/verify" : "/after-sign-in";
    } catch {
      posthog.capture(PostHogEvent.SIGNUP_FAILED, { reason: "network" });
      goTo(step, AUTH_COPY.networkError);
    } finally {
      setSubmitting(false);
    }
  };

  const advance = (event: FormEvent) => {
    event.preventDefault();
    if (submitting || google.starting) return;
    // The first screen's form is the email door; Google has its own button.
    const activeSteps = step === "start" ? signupSteps({ inviteRequired, path: "email" }) : steps;
    if (step === "start") setPath("email");
    const problem = validateSignupStep(step, values);
    if (problem) {
      goTo(step, problem);
      return;
    }
    setErrors((previous) => ({ ...previous, [step]: undefined }));
    const position = activeSteps.indexOf(step);
    const next = activeSteps[position + 1];
    if (next) {
      setStep(next);
      return;
    }
    if (path === "google") void leaveForGoogle();
    else void submit();
  };

  const fieldProps = {
    "aria-invalid": error ? true : undefined,
    "aria-describedby": ERROR_ID,
  } as const;

  const shell = {
    stepKey: step,
    current: index + 1,
    total: steps.length,
    onBack: index > 0 ? back : undefined,
    testId: "signup-flow",
  };

  if (step === "start") {
    return (
      <StepShell
        {...shell}
        title={<span data-testid="signup-title">{copy.start.title}</span>}
        hint={copy.start.hint}
        footer={
          <>
            {copy.start.haveAccount}{" "}
            <Link href="/auth/login" className="font-medium text-foreground underline-offset-4 hover:underline" data-testid="signup-signin-link">
              {copy.start.signIn}
            </Link>
          </>
        }
      >
        <GoogleSignInButton
          google={google}
          label={copy.start.google}
          onStart={chooseGoogle}
          className="mb-6"
          notice={
            // Only when Google is the last step: with an invite step in
            // between, the same line is shown there, right before leaving.
            !inviteRequired ? (
              <p className="text-xs text-muted-foreground" data-testid="signup-google-notice">
                {copy.start.googleNotice} <LegalLinks />.
              </p>
            ) : null
          }
        />
        <form onSubmit={advance} noValidate data-testid="signup-form">
          <Label htmlFor="signup-email" className="sr-only">
            {copy.start.emailLabel}
          </Label>
          <Input
            id="signup-email"
            type="email"
            inputMode="email"
            autoComplete="email"
            autoFocus
            placeholder={copy.start.emailPlaceholder}
            value={values.email}
            onChange={(event) => update("email", event.target.value)}
            className={STEP_INPUT_CLASS}
            data-testid="signup-email-input"
            {...fieldProps}
          />
          <StepError id={ERROR_ID} message={error} />
          <StepAction testId="signup-next">{AUTH_COPY.continue}</StepAction>
        </form>
      </StepShell>
    );
  }

  if (step === "invite") {
    const toGoogle = path === "google";
    return (
      <StepShell {...shell} title={copy.invite.title} hint={copy.invite.hint}>
        <form onSubmit={advance} noValidate data-testid="signup-invite-form">
          <Label htmlFor="signup-invite" className="sr-only">
            {copy.invite.label}
          </Label>
          <Input
            id="signup-invite"
            autoFocus
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            placeholder={copy.invite.placeholder}
            value={values.inviteCode}
            onChange={(event) => update("inviteCode", event.target.value)}
            className={`${STEP_INPUT_CLASS} font-mono tracking-wider`}
            data-testid="signup-invite-input"
            {...fieldProps}
          />
          <StepError id={ERROR_ID} message={error} />
          {toGoogle && (
            <p className="mt-4 text-xs text-muted-foreground" data-testid="signup-google-notice">
              {copy.start.googleNotice} <LegalLinks />.
            </p>
          )}
          <StepAction testId="signup-next" disabled={google.starting}>
            {toGoogle ? copy.invite.toGoogle : AUTH_COPY.continue}
          </StepAction>
        </form>
      </StepShell>
    );
  }

  if (step === "name") {
    return (
      <StepShell {...shell} title={copy.name.title} hint={copy.name.hint}>
        <form onSubmit={advance} noValidate data-testid="signup-name-form">
          <Label htmlFor="signup-name" className="sr-only">
            {copy.name.label}
          </Label>
          <Input
            id="signup-name"
            autoFocus
            autoComplete="name"
            placeholder={copy.name.placeholder}
            value={values.name}
            onChange={(event) => update("name", event.target.value)}
            className={STEP_INPUT_CLASS}
            data-testid="signup-name-input"
            {...fieldProps}
          />
          <StepError id={ERROR_ID} message={error} />
          <StepAction testId="signup-next">{AUTH_COPY.continue}</StepAction>
        </form>
      </StepShell>
    );
  }

  if (step === "password") {
    return (
      <StepShell {...shell} title={copy.password.title} hint={copy.password.hint}>
        <form onSubmit={advance} noValidate data-testid="signup-password-form">
          <Label htmlFor="signup-password" className="sr-only">
            {copy.password.label}
          </Label>
          <PasswordInput
            id="signup-password"
            value={values.password}
            onChange={(value) => update("password", value)}
            autoComplete="new-password"
            invalid={Boolean(error)}
            describedBy={ERROR_ID}
            testId="signup-password-input"
          />
          <StepError id={ERROR_ID} message={error} />
          <StepAction testId="signup-next">{AUTH_COPY.continue}</StepAction>
        </form>
      </StepShell>
    );
  }

  return (
    <StepShell {...shell} title={copy.agreement.title} hint={copy.agreement.hint}>
      <form onSubmit={advance} noValidate data-testid="signup-agreement-form">
        <div className="flex items-start gap-3 rounded-lg border border-border p-4">
          <Checkbox
            id="signup-agree"
            autoFocus
            checked={values.agreed}
            onCheckedChange={(checked) => update("agreed", checked === true)}
            className="mt-0.5 size-5"
            data-testid="signup-agree-checkbox"
            {...fieldProps}
          />
          <Label htmlFor="signup-agree" className="block text-[15px] font-normal leading-snug">
            {copy.agreement.agreePrefix} <LegalLinks />
          </Label>
        </div>
        <StepError id={ERROR_ID} message={error} />
        <StepAction testId="signup-submit-btn" disabled={submitting}>
          {submitting ? copy.agreement.submitting : copy.agreement.submit}
        </StepAction>
      </form>
    </StepShell>
  );
}
