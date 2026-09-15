"use client";

/**
 * The door: three questions, then say hi to Decibyl.
 *
 * It asked six things once, half of them a phone product's: expected call
 * volume, which voice provider you were leaving and why, and an on-prem
 * detour with a captcha. A person hiring their first bot for WhatsApp
 * replies met a form about call minutes. Now it is what Slack asks and no
 * more: what is your role, what business are you in, where did you hear
 * about us. Then the modal closes on the home screen, where Decibyl says
 * hello and asks what to take off your plate.
 *
 * Still compulsory: no outside close, no escape, the one exit is "Get
 * started" once the three are answered.
 */

import { Rocket } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAppConfig } from "@/context/AppConfigContext";
import { useAuth } from "@/lib/auth";

import {
  ONBOARDING_BUSINESS_OPTIONS,
  ONBOARDING_HEARD_OPTIONS,
  ONBOARDING_ROLE_OPTIONS,
} from "./leadFieldOptions";
import { LeadModalShell } from "./LeadModalShell";
import { submitOnboarding } from "./submitOnboarding";

interface OnboardingModalProps {
  open: boolean;
  /** Called once the three are answered. Onboarding is compulsory, so
   *  `skipped` is always false; the argument stays for the callers. */
  onComplete: (skipped: boolean) => void;
}

const QUESTIONS = [
  { id: "ob-role", label: "What's your role?", options: ONBOARDING_ROLE_OPTIONS },
  { id: "ob-business", label: "What business are you in?", options: ONBOARDING_BUSINESS_OPTIONS },
  { id: "ob-heard", label: "Where did you hear about us?", options: ONBOARDING_HEARD_OPTIONS },
] as const;

type QuestionId = (typeof QUESTIONS)[number]["id"];

export function OnboardingModal({ open, onComplete }: OnboardingModalProps) {
  const { user } = useAuth();
  const { config } = useAppConfig();
  const origin = config?.deploymentMode === "cloud" ? "cloud_app" : "oss_app";
  const userEmail = user ? ("primaryEmail" in user ? user.primaryEmail ?? "" : user.email ?? "") : "";

  const [answers, setAnswers] = useState<Record<QuestionId, string>>({
    "ob-role": "",
    "ob-business": "",
    "ob-heard": "",
  });
  const [submitting, setSubmitting] = useState(false);

  const complete = QUESTIONS.every((q) => Boolean(answers[q.id]));
  const canSubmit = complete && !submitting;

  const handleSubmit = () => {
    if (!complete) {
      toast.error("Three quick answers and you're in");
      return;
    }
    if (submitting) return;
    setSubmitting(true);
    // The door opens first; the answers travel after. A lead service that
    // is slow or down must never hold somebody outside their own account.
    onComplete(false);
    void submitOnboarding(
      {
        role: answers["ob-role"],
        business: answers["ob-business"],
        // `persona` is what the lead service has always filed under; the
        // business is the nearest thing to it.
        persona: answers["ob-business"],
        howHeard: answers["ob-heard"],
      },
      origin,
      userEmail,
    ).catch(() => {
      // Logged by the client; the account is already open.
    });
  };

  return (
    <LeadModalShell
      open={open}
      onOpenChange={() => {}}
      contentProps={{
        className: "[&>button]:hidden",
        onEscapeKeyDown: (e) => e.preventDefault(),
        onPointerDownOutside: (e) => e.preventDefault(),
        onInteractOutside: (e) => e.preventDefault(),
      }}
      icon={Rocket}
      eyebrow="Welcome"
      title="Welcome to Decibyl"
      description="Three quick answers, then say hi to Decibyl."
      primary={{ label: "Get started", onClick: handleSubmit, disabled: !canSubmit, loading: submitting }}
    >
      <div className="grid gap-4">
        {QUESTIONS.map((q) => (
          <div key={q.id} className="space-y-1.5">
            <Label htmlFor={q.id}>{q.label}</Label>
            <Select
              value={answers[q.id]}
              onValueChange={(v) => setAnswers((was) => ({ ...was, [q.id]: v }))}
            >
              <SelectTrigger id={q.id} data-testid={q.id}>
                <SelectValue placeholder="Select one" />
              </SelectTrigger>
              <SelectContent>
                {q.options.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ))}
      </div>
    </LeadModalShell>
  );
}
