"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { loginApiV1AuthLoginPost } from "@/client/sdk.gen";
import { AuthEnterpriseCTA } from "@/components/auth/AuthEnterpriseCTA";
import { AuthShell } from "@/components/auth/AuthShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function LoginForm({ signupEnabled }: { signupEnabled: boolean }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    try {
      const res = await loginApiV1AuthLoginPost({
        body: { email, password },
      });

      if (res.error || !res.data) {
        const detail = (res.error as { detail?: string })?.detail;
        toast.error(detail || "Login failed");
        return;
      }

      // Set httpOnly cookies via server route
      await fetch("/api/auth/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: res.data.token, user: res.data.user }),
      });

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
        <h1 className="text-2xl font-semibold tracking-tight" data-testid="login-title">Welcome back</h1>
        <p className="text-sm text-muted-foreground">
          Sign in to your EchoWave workspace.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4" data-testid="login-form">
        <div className="space-y-2">
          <Label htmlFor="email">Work email</Label>
          <Input
            id="email"
            type="email"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            data-testid="login-email-input"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            placeholder="Enter your password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            data-testid="login-password-input"
          />
        </div>
        <Button
          type="submit"
          className="w-full bg-brand-blue text-white shadow-[0_10px_30px_-12px_rgba(40,179,240,0.6)] hover:bg-brand-blue-hover"
          disabled={loading}
          data-testid="login-submit-btn"
        >
          {loading ? "Signing in..." : "Sign in →"}
        </Button>
      </form>

      {signupEnabled && (
        <p className="text-center text-sm text-muted-foreground">
          Don&apos;t have an account?{" "}
          <Link href="/auth/signup" className="font-medium text-brand-blue underline-offset-4 hover:underline" data-testid="login-signup-link">
            Sign up
          </Link>
        </p>
      )}
    </AuthShell>
  );
}
