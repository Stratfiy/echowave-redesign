"use client";

import { Eye, EyeOff } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { AUTH_COPY, LEGAL_LINKS } from "./copy";
import { STEP_INPUT_CLASS } from "./StepShell";

/** The two documents, each its own link, so "I agree" names what it means.
 *  New tab, so reading them does not lose a half-finished sign-up. */
export function LegalLinks() {
  const copy = AUTH_COPY.signup.agreement;
  const linkClass = "font-medium text-foreground underline underline-offset-4";
  return (
    <>
      <Link href={LEGAL_LINKS.terms} target="_blank" rel="noopener" className={linkClass}>
        {copy.terms}
      </Link>{" "}
      {copy.and}{" "}
      <Link href={LEGAL_LINKS.privacy} target="_blank" rel="noopener" className={linkClass}>
        {copy.privacy}
      </Link>
    </>
  );
}

/** A password field with a show/hide toggle inside it. */
export function PasswordInput({
  id,
  value,
  onChange,
  autoComplete,
  invalid,
  describedBy,
  placeholder,
  testId,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: "new-password" | "current-password";
  invalid?: boolean;
  describedBy?: string;
  placeholder?: string;
  testId?: string;
}) {
  const [visible, setVisible] = useState(false);
  const copy = AUTH_COPY.signup.password;
  return (
    <div className="relative">
      <Input
        id={id}
        type={visible ? "text" : "password"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        autoFocus
        placeholder={placeholder}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        className={cn(STEP_INPUT_CLASS, "pr-12")}
        data-testid={testId}
      />
      <button
        type="button"
        onClick={() => setVisible((shown) => !shown)}
        aria-label={visible ? copy.hide : copy.show}
        aria-pressed={visible}
        aria-controls={id}
        className="absolute inset-y-0 right-0 flex w-12 items-center justify-center rounded-r-md text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
        data-testid="auth-password-toggle"
      >
        {visible ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />}
      </button>
    </div>
  );
}
