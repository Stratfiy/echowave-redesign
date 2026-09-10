"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import posthog from "posthog-js";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { signupApiV1AuthSignupPost } from "@/client/sdk.gen";
import { AuthEnterpriseCTA } from "@/components/auth/AuthEnterpriseCTA";
import { AuthShell } from "@/components/auth/AuthShell";
import { GoogleSignInButton } from "@/components/auth/GoogleSignInButton";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PostHogEvent } from "@/constants/posthog-events";

function SignupForm() {
  // A partner's referral code, from the link they handed out. Read here and
  // sent with the signup rather than stored anywhere: attribution happens once,
  // at provisioning, and a code that lingers in a cookie would attribute an
  // account somebody created weeks later from a different link.
  const referralCode = useSearchParams().get("ref");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  // The click-wrap. One tick, two named documents each with its own link,
  // and the server checks the same list — the form cannot accept by omission.
  const [agreed, setAgreed] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (password.length < 8) {
      toast.error("Password must be at least 8 characters");
      return;
    }

    if (password !== confirmPassword) {
      toast.error("Passwords do not match");
      return;
    }

    if (!agreed) {
      toast.error("Please accept the Terms of Service and Privacy Policy.");
      return;
    }

    setLoading(true);
    // Before the request, so a sign-up that never returns still counts as an
    // attempt. The email is not sent: the user is identified once signed in.
    posthog.capture(PostHogEvent.SIGNUP_SUBMITTED, { referred: Boolean(referralCode) });

    try {
      const res = await signupApiV1AuthSignupPost({
        body: {
          email,
          password,
          referral_code: referralCode,
          accepted_agreements: ["terms", "privacy"],
        },
      });

      if (res.error || !res.data) {
        const detail = (res.error as { detail?: string })?.detail;
        posthog.capture(PostHogEvent.SIGNUP_FAILED, { reason: detail ?? "unknown" });
        toast.error(detail || "Signup failed");
        return;
      }
      posthog.capture(PostHogEvent.SIGNUP_SUCCEEDED, { referred: Boolean(referralCode) });

      // Set httpOnly cookies via server route
      await fetch("/api/auth/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: res.data.token, user: res.data.user }),
      });

      window.location.href = "/after-sign-in";
    } catch {
      posthog.capture(PostHogEvent.SIGNUP_FAILED, { reason: "network" });
      toast.error("An error occurred. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell enterpriseSlot={<AuthEnterpriseCTA />}>
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight" data-testid="signup-title">Create your account</h1>
        <p className="text-sm text-muted-foreground">Start building voice agents in minutes — no credit card required.</p>
      </div>

      <GoogleSignInButton label="Sign up with Google" referralCode={referralCode} />
      <p className="text-center text-xs text-muted-foreground" data-testid="signup-google-notice">
        Continuing with Google means you agree to the <LegalLinks />.
      </p>

      <form onSubmit={handleSubmit} className="space-y-4" data-testid="signup-form">
        <div className="space-y-2">
          <Label htmlFor="email">Work email</Label>
          <Input
            id="email"
            type="email"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            data-testid="signup-email-input"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            placeholder="At least 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            data-testid="signup-password-input"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="confirmPassword">Confirm password</Label>
          <Input
            id="confirmPassword"
            type="password"
            placeholder="Re-enter your password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
            minLength={8}
            data-testid="signup-confirm-password-input"
          />
        </div>
        <label className="flex items-start gap-2.5 text-sm" htmlFor="agree">
          <Checkbox
            id="agree"
            checked={agreed}
            onCheckedChange={(value) => setAgreed(value === true)}
            className="mt-0.5"
            data-testid="signup-agree-checkbox"
          />
          <span className="text-muted-foreground">
            I agree to the <LegalLinks />.
          </span>
        </label>
        <Button
          type="submit"
          className="w-full bg-primary text-primary-foreground shadow-[var(--shadow-subtle)] hover:bg-[var(--primary-pressed)]"
          disabled={loading || !agreed}
          data-testid="signup-submit-btn"
        >
          {loading ? "Creating account..." : "Create account →"}
        </Button>
      </form>

      <p className="text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/auth/login" className="font-medium text-brand-blue underline-offset-4 hover:underline" data-testid="signup-signin-link">
          Sign in
        </Link>
      </p>
    </AuthShell>
  );
}

/**
 * `useSearchParams` needs a Suspense boundary, or this route opts out of static
 * rendering at build time.
 */
export default function SignupPage() {
  return (
    <Suspense fallback={null}>
      <SignupForm />
    </Suspense>
  );
}

/** The two documents, each its own link, so "I agree" names what it means. */
function LegalLinks() {
  return (
    <>
      <a
        href="https://decibyl.ai/legal/terms"
        target="_blank"
        rel="noreferrer"
        className="font-medium text-foreground underline-offset-4 hover:underline"
      >
        Terms of Service
      </a>{" "}
      and{" "}
      <a
        href="https://decibyl.ai/privacy"
        target="_blank"
        rel="noreferrer"
        className="font-medium text-foreground underline-offset-4 hover:underline"
      >
        Privacy Policy
      </a>
    </>
  );
}
