"use client";

/**
 * The way back in for somebody who forgot their password.
 *
 * Two steps on one page: an address, then the code from the inbox with the
 * new password beside it. The first step answers the same way whatever the
 * address, because a form that says "no account with that email" is a
 * membership oracle for anyone with a list — the inbox is where the person
 * finds out what happened.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import {
  forgotPasswordApiV1AuthPasswordForgotPost,
  resetPasswordApiV1AuthPasswordResetPost,
} from "@/client/sdk.gen";
import { AuthShell } from "@/components/auth/AuthShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromResult } from "@/lib/apiError";

export default function ForgotPasswordPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const sendCode = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true);
    const response = await forgotPasswordApiV1AuthPasswordForgotPost({ body: { email } });
    setLoading(false);
    if (response.error) {
      toast.error(detailFromResult(response, "Could not send a code."));
      return;
    }
    setSent(true);
  };

  const reset = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true);
    const response = await resetPasswordApiV1AuthPasswordResetPost({
      body: { email, code, new_password: password },
    });
    setLoading(false);
    if (response.error) {
      toast.error(detailFromResult(response, "That code was not accepted."));
      return;
    }
    toast.success("Password changed. Sign in with the new one.");
    router.push("/auth/login");
  };

  return (
    <AuthShell>
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight" data-testid="forgot-title">
          Reset your password
        </h1>
        <p className="text-sm text-muted-foreground">
          {sent
            ? "If that address has an account, a six-digit code is on its way. Enter it with your new password."
            : "Enter the email you signed up with. We will send a code to set a new password."}
        </p>
      </div>

      {sent ? (
        <form onSubmit={reset} className="space-y-4" data-testid="reset-form">
          <div className="space-y-2">
            <Label htmlFor="code">Code from the email</Label>
            <Input
              id="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              autoFocus
              placeholder="000000"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              required
              className="font-mono tracking-[0.2em]"
              data-testid="reset-code-input"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-password">New password</Label>
            <Input
              id="new-password"
              type="password"
              autoComplete="new-password"
              placeholder="At least 8 characters"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              data-testid="reset-password-input"
            />
          </div>
          <Button type="submit" className="w-full" disabled={loading || code.length < 6} data-testid="reset-submit-btn">
            {loading ? "Saving..." : "Set new password →"}
          </Button>
          <p className="text-center text-xs text-muted-foreground">
            No email after a minute? Check spam, or{" "}
            <button type="button" className="font-medium underline-offset-4 hover:underline" onClick={() => setSent(false)}>
              send another code
            </button>
            .
          </p>
        </form>
      ) : (
        <form onSubmit={sendCode} className="space-y-4" data-testid="forgot-form">
          <div className="space-y-2">
            <Label htmlFor="email">Work email</Label>
            <Input
              id="email"
              type="email"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              data-testid="forgot-email-input"
            />
          </div>
          <Button type="submit" className="w-full" disabled={loading} data-testid="forgot-submit-btn">
            {loading ? "Sending..." : "Send code →"}
          </Button>
        </form>
      )}

      <p className="text-center text-sm text-muted-foreground">
        Remembered it?{" "}
        <Link href="/auth/login" className="font-medium text-brand-blue underline-offset-4 hover:underline">
          Sign in
        </Link>
      </p>
    </AuthShell>
  );
}
