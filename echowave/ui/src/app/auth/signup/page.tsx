"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { signupApiV1AuthSignupPost } from "@/client/sdk.gen";
import { AuthEnterpriseCTA } from "@/components/auth/AuthEnterpriseCTA";
import { AuthShell } from "@/components/auth/AuthShell";
import { GoogleSignInButton } from "@/components/auth/GoogleSignInButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromError } from "@/lib/apiError";

function SignupForm() {
  // A partner's referral code, from the link they handed out. Read here and
  // sent with the signup rather than stored anywhere: attribution happens once,
  // at provisioning, and a code that lingers in a cookie would attribute an
  // account somebody created weeks later from a different link.
  const referralCode = useSearchParams().get("ref");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
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

    setLoading(true);

    try {
      const res = await signupApiV1AuthSignupPost({
        body: { email, password, referral_code: referralCode },
      });

      if (res.error || !res.data) {
        toast.error(detailFromError(res.error, "Signup failed"));
        return;
      }

      // Set httpOnly cookies via server route
      const session = await fetch("/api/auth/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: res.data.token, user: res.data.user }),
      });

      if (!session.ok) {
        toast.error("Your account was created, but we could not save your session. Please sign in.");
        return;
      }

      window.location.href = "/after-sign-in";
    } catch {
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

      <p className="text-xs text-muted-foreground">By creating an account, you agree to our <a className="underline" href="https://www.decibyl.ai/legal/terms" target="_blank" rel="noopener noreferrer">Terms of Service</a>. Read how we use your information in our <a className="underline" href="https://www.decibyl.ai/legal/privacy" target="_blank" rel="noopener noreferrer">Privacy Policy</a>.</p>

      <GoogleSignInButton label="Sign up with Google" referralCode={referralCode} />

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
        <Button
          type="submit"
          className="w-full bg-brand-blue text-primary-foreground shadow-[var(--shadow-subtle)] hover:bg-brand-blue-hover"
          disabled={loading}
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
