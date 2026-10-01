"use client";

/**
 * The shell for one-question-per-screen auth: a thin progress line at the
 * top, Back in the corner, one large question in the middle, and nothing
 * else. Sign-up, log-in and email verification all stand on it so the three
 * doors feel like one.
 *
 * Each step is re-keyed so it mounts fresh: the step's own input takes focus
 * via `autoFocus`, and the entrance is a short fade that `motion-safe:` drops
 * for anyone who prefers reduced motion.
 */

import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";

import { BrandLogo } from "@/components/BrandLogo";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { AUTH_COPY } from "./copy";

export function StepShell({
  stepKey,
  title,
  hint,
  current,
  total,
  onBack,
  footer,
  children,
  testId,
}: {
  /** Changes whenever the question changes, to remount and re-focus it. */
  stepKey: string;
  title: ReactNode;
  hint?: ReactNode;
  /** 1-based position; the progress line is hidden when either is absent. */
  current?: number;
  total?: number;
  onBack?: () => void;
  /** One quiet line under the question, e.g. "Already have an account?". */
  footer?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  const showProgress = typeof current === "number" && typeof total === "number" && total > 0;
  const percent = showProgress ? Math.round((current / total) * 100) : 0;

  return (
    <div className="flex min-h-dvh w-full flex-col bg-background text-foreground" data-testid={testId}>
      <div className="h-0.5 w-full bg-border/60">
        {showProgress && (
          <div
            role="progressbar"
            aria-label={AUTH_COPY.progressLabel}
            aria-valuemin={1}
            aria-valuemax={total}
            aria-valuenow={current}
            aria-valuetext={`Step ${current} of ${total}`}
            className="h-full bg-primary transition-[width] duration-300 ease-out motion-reduce:transition-none"
            style={{ width: `${percent}%` }}
            data-testid="auth-progress"
          />
        )}
      </div>

      <header className="flex h-16 items-center px-4 sm:px-8">
        {onBack ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onBack}
            className="-ml-2 gap-1.5 text-muted-foreground hover:text-foreground"
            data-testid="auth-back"
          >
            <ArrowLeft aria-hidden="true" />
            {AUTH_COPY.back}
          </Button>
        ) : (
          <BrandLogo mark className="h-7" />
        )}
      </header>

      <main className="flex flex-1 items-start justify-center px-4 pb-16 pt-[8vh] sm:items-center sm:pt-0">
        <div
          key={stepKey}
          className={cn(
            "w-full max-w-sm",
            "motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-2 motion-safe:duration-300",
          )}
          data-step={stepKey}
        >
          <h1 className="text-balance text-[28px] font-semibold leading-tight tracking-tight sm:text-[34px]">
            {title}
          </h1>
          {hint && <p className="mt-2 text-[15px] leading-relaxed text-muted-foreground">{hint}</p>}
          <div className="mt-8">{children}</div>
          {footer && <div className="mt-8 text-sm text-muted-foreground">{footer}</div>}
        </div>
      </main>
    </div>
  );
}

/** The step's error, announced as it appears. Always rendered so the live
 *  region exists before there is anything to say. */
export function StepError({ id, message }: { id: string; message?: string | null }) {
  return (
    <p
      id={id}
      role="alert"
      aria-live="assertive"
      className={cn("text-sm text-destructive", message ? "mt-3" : "sr-only")}
      data-testid="auth-step-error"
    >
      {message ?? ""}
    </p>
  );
}

/** The one primary action on a step. */
export function StepAction({
  children,
  disabled,
  testId,
}: {
  children: ReactNode;
  disabled?: boolean;
  testId?: string;
}) {
  return (
    <Button type="submit" className="mt-6 h-11 w-full text-[15px]" disabled={disabled} data-testid={testId}>
      {children}
    </Button>
  );
}

/** Large, calm input class shared by every step. */
export const STEP_INPUT_CLASS = "h-12 text-base md:text-base";
