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
// **The pitch is the whole product, not one channel.** It read "Decibyl
// answers your phone" -- true, and a third of what a customer buys. The
// same bot replies on WhatsApp and email, looks things up in the tools the
// business already runs, and files what it did. A visitor who only wants
// the phone still reads it in the first line; one who wants a bot that
// finishes a job no longer has to guess whether this does that.

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
          An agent for every job nobody has time for.
        </h1>
        <p className="mx-auto mt-3 max-w-md text-[15px] leading-relaxed text-brand-body">
          Decibyl&rsquo;s agents answer the phone, reply on WhatsApp and email,
          look things up in the tools you already run, and hand back what they
          finished. In Hindi, Tamil, Telugu and eight more. Set one up yourself
          in ten minutes.
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
