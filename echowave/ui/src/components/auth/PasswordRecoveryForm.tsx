"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { AuthShell } from "@/components/auth/AuthShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAppConfig } from "@/context/AppConfigContext";
import { resolveBrowserBackendUrl } from "@/lib/apiClient";
import { detailFromError } from "@/lib/apiError";

export function PasswordRecoveryForm({ reset = false }: { reset?: boolean }) {
  const { config } = useAppConfig();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const tokenRead = useRef(false);
  const [ready, setReady] = useState(!reset);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!reset || tokenRead.current) return;
    tokenRead.current = true;
    const value = new URLSearchParams(window.location.hash.slice(1)).get("token");
    setToken(value);
    window.history.replaceState({}, "", window.location.pathname);
    setReady(true);
    if (!value) setError("This reset link is incomplete. Request a new link.");
  }, [reset]);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (reset && password !== confirmation) { setError("Passwords do not match."); return; }
    if (reset && (password.length < 8 || new TextEncoder().encode(password).length > 72)) {
      setError("Use at least 8 characters and no more than 72 bytes for your password."); return;
    }
    setPending(true);
    try {
      const base = resolveBrowserBackendUrl(config?.backendApiEndpoint);
      const response = await fetch(`${base}/api/v1/auth/password-reset/${reset ? "confirm" : "request"}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reset ? { token, password } : { email }),
      });
      const body = await response.json();
      if (!response.ok) { setError(detailFromError(body, "Could not complete this request. Please try again.")); return; }
      setMessage(reset ? "Password updated. Sign in with your new password. Your existing browser sessions have been signed out; two-factor authentication stays enabled." : "If an account exists for this address, a reset link will arrive shortly. Check your spam folder too.");
      setPassword(""); setConfirmation(""); setToken(null);
    } catch { setError("Could not reach Decibyl. Check your connection and try again."); }
    finally { setPending(false); }
  };

  return <AuthShell>
    <div><h1 className="text-2xl font-semibold">{reset ? "Choose a new password" : "Reset your password"}</h1>
      <p className="mt-2 text-sm text-muted-foreground">{reset ? "The link expires after 30 minutes and works once." : "Enter your account email and we will send a reset link. You can also use this to set a password for a Google account. Two-factor authentication stays enabled."}</p></div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {message ? <p role="status" className="text-sm">{message}</p> : ready && (!reset || token) &&
      <form onSubmit={submit} className="space-y-4" aria-label="Password recovery">
        {reset ? <>
          <div className="space-y-2"><Label htmlFor="new-password">New password</Label><Input id="new-password" type="password" autoComplete="new-password" required minLength={8} value={password} onChange={event => setPassword(event.target.value)} /></div>
          <div className="space-y-2"><Label htmlFor="confirm-password">Confirm password</Label><Input id="confirm-password" type="password" autoComplete="new-password" required minLength={8} value={confirmation} onChange={event => setConfirmation(event.target.value)} /></div>
        </> : <div className="space-y-2"><Label htmlFor="recovery-email">Account email</Label><Input id="recovery-email" type="email" autoComplete="email" required value={email} onChange={event => setEmail(event.target.value)} /></div>}
        <Button type="submit" disabled={pending} className="w-full">{pending ? "Please wait…" : reset ? "Update password" : "Send reset link"}</Button>
      </form>}
    {reset && !message && <Link href="/auth/forgot-password" className="text-sm underline">Request a new reset link</Link>}
    <a href="/auth/login" className="text-sm underline">Back to sign in</a>
    <p className="text-xs text-muted-foreground">Need help? <a className="underline" href="mailto:support@decibyl.ai">Contact support</a>.</p>
  </AuthShell>;
}
