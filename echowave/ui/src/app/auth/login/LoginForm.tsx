"use client";

/**
 * Log-in in the same shell as sign-up: email → password, with Google on the
 * first screen and "Forgot password?" beside the password. A third step, the
 * authentication code, appears only once the server has said this account
 * has a second factor.
 */

import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";

import { loginApiV1AuthLoginPost } from "@/client/sdk.gen";
import { GoogleSignInButton } from "@/components/auth/GoogleSignInButton";
import { AUTH_COPY } from "@/components/auth/steps/copy";
import { PasswordInput } from "@/components/auth/steps/fields";
import { STEP_INPUT_CLASS, StepAction, StepError, StepShell } from "@/components/auth/steps/StepShell";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromResult } from "@/lib/apiError";

type LoginStep = "email" | "password" | "mfa";

const ERROR_ID = "login-step-error";
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const copy = AUTH_COPY.login;

export function LoginForm({ signupEnabled }: { signupEnabled: boolean }) {
  const [step, setStep] = useState<LoginStep>("email");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [errors, setErrors] = useState<Partial<Record<LoginStep, string>>>({});
  const [loading, setLoading] = useState(false);
  // The second factor, once the server has told us this account has one.
  // The backend answers `401 detail="mfa_required"`; without this step an
  // account with MFA could never sign in.
  const [mfaRequired, setMfaRequired] = useState(false);

  // Google sign-in failures come back as ?error= on this page. Shown on the
  // first step, then cleared so a reload does not replay it. Read from
  // window rather than useSearchParams so no Suspense boundary is needed.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const message = params.get("error");
    if (!message) return;
    setErrors({ email: message });
    const url = new URL(window.location.href);
    url.searchParams.delete("error");
    window.history.replaceState({}, "", url.toString());
  }, []);

  const steps: LoginStep[] = mfaRequired ? ["email", "password", "mfa"] : ["email", "password"];
  const index = steps.indexOf(step);
  const error = errors[step];

  const goTo = (target: LoginStep, message?: string) => {
    setErrors((previous) => ({ ...previous, [target]: message }));
    setStep(target);
  };

  const clearError = () => {
    if (errors[step]) setErrors((previous) => ({ ...previous, [step]: undefined }));
  };

  const back = () => {
    if (index > 0) setStep(steps[index - 1]);
  };

  const signIn = async () => {
    setLoading(true);
    try {
      const res = await loginApiV1AuthLoginPost({
        body: {
          email: email.trim(),
          password,
          // Only once asked for: an empty string would be a wrong code.
          ...(mfaRequired && mfaCode.trim() ? { mfa_code: mfaCode.trim() } : {}),
        },
      });
      if (res.error || !res.data) {
        const detail = detailFromResult(res, copy.failed);
        if (detail === "mfa_required") {
          // Not a failure: the password was right. Ask for the code.
          setMfaRequired(true);
          goTo("mfa");
          return;
        }
        goTo(mfaRequired ? "mfa" : "password", detail);
        return;
      }
      await fetch("/api/auth/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: res.data.token, user: res.data.user }),
      });
      window.location.href = "/after-sign-in";
    } catch {
      goTo(step, AUTH_COPY.networkError);
    } finally {
      setLoading(false);
    }
  };

  const advance = (event: FormEvent) => {
    event.preventDefault();
    if (loading) return;
    if (step === "email") {
      if (!EMAIL_PATTERN.test(email.trim())) {
        goTo("email", AUTH_COPY.signup.start.emailInvalid);
        return;
      }
      goTo("password");
      return;
    }
    if (step === "password" && !password) {
      goTo("password", copy.password.required);
      return;
    }
    if (step === "mfa" && !mfaCode.trim()) {
      goTo("mfa", copy.mfa.required);
      return;
    }
    void signIn();
  };

  const shell = {
    stepKey: step,
    current: index + 1,
    total: steps.length,
    onBack: index > 0 ? back : undefined,
    testId: "login-flow",
  };

  const fieldProps = {
    "aria-invalid": error ? true : undefined,
    "aria-describedby": ERROR_ID,
  } as const;

  if (step === "email") {
    return (
      <StepShell
        {...shell}
        title={<span data-testid="login-title">{copy.email.title}</span>}
        hint={copy.email.hint}
        footer={
          signupEnabled ? (
            <>
              {copy.email.noAccount}{" "}
              <Link href="/auth/signup" className="font-medium text-foreground underline-offset-4 hover:underline" data-testid="login-signup-link">
                {copy.email.signUp}
              </Link>
            </>
          ) : null
        }
      >
        <GoogleSignInButton label={copy.email.google} className="mb-6" />
        <form onSubmit={advance} noValidate data-testid="login-form">
          <Label htmlFor="login-email" className="sr-only">
            {AUTH_COPY.signup.start.emailLabel}
          </Label>
          <Input
            id="login-email"
            type="email"
            inputMode="email"
            autoComplete="username"
            autoFocus
            placeholder={AUTH_COPY.signup.start.emailPlaceholder}
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              clearError();
            }}
            className={STEP_INPUT_CLASS}
            data-testid="login-email-input"
            {...fieldProps}
          />
          <StepError id={ERROR_ID} message={error} />
          <StepAction testId="login-next">{AUTH_COPY.continue}</StepAction>
        </form>
      </StepShell>
    );
  }

  if (step === "password") {
    return (
      <StepShell {...shell} title={copy.password.title} hint={email.trim()}>
        <form onSubmit={advance} noValidate data-testid="login-password-form">
          {/* The address again, hidden, so a password manager pairs the two. */}
          <input type="email" autoComplete="username" value={email} readOnly hidden />
          <div className="mb-2 flex items-center justify-between">
            <Label htmlFor="login-password" className="text-sm text-muted-foreground">
              {AUTH_COPY.signup.password.label}
            </Label>
            <Link
              href="/auth/forgot"
              className="text-sm font-medium text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
              data-testid="login-forgot-link"
            >
              {copy.password.forgot}
            </Link>
          </div>
          <PasswordInput
            id="login-password"
            value={password}
            onChange={(value) => {
              setPassword(value);
              clearError();
            }}
            autoComplete="current-password"
            invalid={Boolean(error)}
            describedBy={ERROR_ID}
            testId="login-password-input"
          />
          <StepError id={ERROR_ID} message={error} />
          <StepAction testId="login-submit-btn" disabled={loading}>
            {loading ? copy.password.submitting : copy.password.submit}
          </StepAction>
        </form>
      </StepShell>
    );
  }

  return (
    <StepShell {...shell} title={copy.mfa.title} hint={copy.mfa.hint}>
      <form onSubmit={advance} noValidate data-testid="login-mfa-form">
        <Label htmlFor="login-mfa" className="sr-only">
          {copy.mfa.label}
        </Label>
        <Input
          id="login-mfa"
          inputMode="numeric"
          autoComplete="one-time-code"
          autoFocus
          placeholder={copy.mfa.placeholder}
          value={mfaCode}
          onChange={(event) => {
            setMfaCode(event.target.value);
            clearError();
          }}
          className={`${STEP_INPUT_CLASS} font-mono`}
          data-testid="login-mfa-input"
          {...fieldProps}
        />
        <StepError id={ERROR_ID} message={error} />
        <StepAction testId="login-submit-btn" disabled={loading}>
          {loading ? copy.password.submitting : copy.mfa.submit}
        </StepAction>
      </form>
    </StepShell>
  );
}
