"use client";

/**
 * "Don't have a code? Ask for one", answered where the question is asked.
 *
 * Invite-only sign-up used to dead-end here: no code, no way to ask. This
 * sends the same request the /early-access page does (one row per address,
 * the approvers are mailed on a new one) and says so in place, so nobody
 * has to leave the screen to finish (AGENTS.md).
 */

import Link from "next/link";
import { type FormEvent, useId, useState } from "react";

import { joinWaitlistApiV1PublicEarlyAccessWaitlistPost } from "@/client/sdk.gen";
import { AUTH_COPY } from "@/components/auth/steps/copy";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromResult } from "@/lib/apiError";

const copy = AUTH_COPY.signup.askCode;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type Outcome = "sent" | "invited" | "registered";

export function AskForCode({ initialEmail = "", className }: { initialEmail?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState(initialEmail);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const ids = { email: useId(), note: useId(), error: useId() };

  if (outcome) {
    return (
      <div role="status" className={`rounded-lg border border-border p-4 text-sm ${className ?? ""}`} data-testid="ask-code-result" data-result={outcome}>
        {outcome === "registered" ? (
          <>
            {copy.registered}{" "}
            <Link href="/auth/login" className="font-medium text-foreground underline-offset-4 hover:underline">
              {copy.signIn}
            </Link>
          </>
        ) : outcome === "invited" ? (
          copy.invited
        ) : (
          copy.sent
        )}
      </div>
    );
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => {
          setEmail((was) => was || initialEmail);
          setOpen(true);
        }}
        className={`block text-left text-sm font-medium text-foreground underline-offset-4 hover:underline ${className ?? ""}`}
        data-testid="ask-code-open"
      >
        {copy.open}
      </button>
    );
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    if (!EMAIL.test(email.trim())) {
      setError(AUTH_COPY.signup.start.emailInvalid);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const response = await joinWaitlistApiV1PublicEarlyAccessWaitlistPost({
        body: { email: email.trim(), first_task: note.trim() || null },
      });
      if (response.error || !response.data) {
        setError(detailFromResult(response, copy.failed));
        return;
      }
      // A repeat (created: false) is the same request as before: same answer.
      const { state } = response.data;
      setOutcome(state === "already_registered" ? "registered" : state === "invited" ? "invited" : "sent");
    } catch {
      setError(copy.failed);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form
      onSubmit={submit}
      noValidate
      className={`flex flex-col gap-3 rounded-lg border border-border p-4 ${className ?? ""}`}
      data-testid="ask-code-form"
      aria-busy={submitting}
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={ids.email} className="text-sm text-muted-foreground">
          {copy.emailLabel}
        </Label>
        <Input
          id={ids.email}
          type="email"
          inputMode="email"
          autoComplete="email"
          autoFocus
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? ids.error : undefined}
          className="h-11 text-base md:text-base"
          data-testid="ask-code-email"
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={ids.note} className="text-sm text-muted-foreground">
          {copy.noteLabel}
        </Label>
        <Input
          id={ids.note}
          maxLength={2000}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          className="h-11 text-base md:text-base"
          data-testid="ask-code-note"
        />
      </div>
      {error && (
        <p id={ids.error} role="alert" className="text-sm text-destructive" data-testid="ask-code-error">
          {error}
        </p>
      )}
      <div className="flex items-center gap-3">
        <Button type="submit" variant="outline" className="h-11 flex-1 text-[15px]" disabled={submitting} data-testid="ask-code-submit">
          {submitting ? copy.submitting : copy.submit}
        </Button>
        <Button type="button" variant="ghost" className="h-11" onClick={() => setOpen(false)} disabled={submitting}>
          {copy.cancel}
        </Button>
      </div>
    </form>
  );
}
