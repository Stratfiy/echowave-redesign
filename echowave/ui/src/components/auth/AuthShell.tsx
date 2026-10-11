// Decibyl auth shell: one centred column, the form card in the middle.
//
// The card is the centre of the page, the brand sits above it, and the
// enterprise line is a quiet footer for the few who need it. Self-hosting
// and BYOK stay true and stay in that footer, not the first sentence.
//
// **The ground is the app's ground.** This page used to open on a large
// coral-to-mauve orb behind the headline, which is the one decoration the
// product itself no longer has anywhere: the chat lost its purple wash, and
// a door dressed differently from the room behind it reads as a different
// product. So the canvas is plain `--background`, the same surface the
// signed-in app stands on, and the only colour is the logo and one accented
// phrase.
//
// **The pitch is the founder's sentence.** Decided 9 Oct 2026: Decibyl is
// an intelligent agent that grows and evolves with you -- one personal
// assistant for life and work, not a phone line and not a bot shelf. Voice
// is one channel, so the pitch names none.

import type { ReactNode } from "react";

import { BrandLogo } from "@/components/BrandLogo";

export function AuthShell({
  children,
  enterpriseSlot,
}: {
  children: ReactNode;
  enterpriseSlot?: ReactNode;
}) {
  return (
    <div className="flex min-h-screen w-full flex-col items-center overflow-x-hidden bg-background px-6 py-10 text-foreground sm:py-14">
      {/* Brand */}
      <header className="relative z-10 flex flex-col items-center gap-3">
        <BrandLogo className="h-9" />
        <span className="rounded-full border border-border px-3 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          by nAutomation Labs
        </span>
      </header>

      {/* Headline. 400 weight, tight leading, one accented phrase. */}
      <div className="relative z-10 mt-8 max-w-xl text-center">
        <h1 className="text-balance text-[28px] font-normal leading-[1.1] tracking-[-0.01em] text-brand-heading sm:text-[34px]">
          An intelligent agent that grows and evolves with you.
        </h1>
        <p className="mx-auto mt-3 max-w-md text-[15px] leading-relaxed text-brand-body">
          One personal assistant for life and work. Talk in your language
          &mdash; Hindi, Tamil, Telugu and eight more &mdash; give it a task,
          and let it help you follow through. It learns your preferences and,
          with your approval, gets better at your work over time.
        </p>
      </div>

      {/* The card: dead centre, the reason the page exists. */}
      <main className="auth-imprint relative z-10 mt-8 w-full max-w-md">
        <div className="space-y-6 rounded-[var(--radius-large)] border border-border bg-card p-6 shadow-[var(--shadow-subtle)] sm:p-8">
          {children}
        </div>

        {/* Enterprise: a footer line, not a competing block. */}
        {enterpriseSlot && (
          <div className="mt-6 flex flex-col items-center gap-3 text-center">
            <p className="text-sm text-brand-body">
              Need on-prem, data residency or a data perimeter? We deploy
              Decibyl inside your environment.
            </p>
            <div className="w-full max-w-xs">{enterpriseSlot}</div>
          </div>
        )}
      </main>
    </div>
  );
}
