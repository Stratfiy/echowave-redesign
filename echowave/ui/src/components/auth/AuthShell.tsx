// Decibyl auth shell. LEFT: brand and value panel. RIGHT: the auth form card.
// Mobile collapses to a single column.
//
// The copy here is the first thing anyone reads, so it has to agree with what
// we actually sell. It used to say "open, self-hostable" and "BYOK · any
// model", which is the developer-tool pitch — and it told every visitor the
// opposite of the pricing: we hold the provider keys, the markup is where the
// margin is, and BYOK is an enterprise arrangement rather than the headline.
// A prospect who arrives expecting to bring their own OpenAI key and finds a
// managed rate card has been mis-sold before they reach the form.
//
// It now says what the product does for the person buying it: answer the
// phone. Self-hosting and BYOK are still true, still available, and belong in
// the enterprise block below rather than in the first sentence.

import type { ReactNode } from "react";

import { BrandLogo } from "@/components/BrandLogo";

const HIGHLIGHTS = [
  "11 Indian languages",
  "Answers in one ring",
  "Live in 10 minutes",
  "No setup fee",
];

export function AuthShell({
  children,
  enterpriseSlot,
}: {
  children: ReactNode;
  enterpriseSlot?: ReactNode;
}) {
  return (
    <div className="grid min-h-screen w-full bg-background lg:grid-cols-[52%_48%]">
      {/* Brand / value panel (LEFT) — hidden on mobile */}
      <aside className="relative hidden flex-col justify-between overflow-hidden bg-brand-panel p-10 lg:flex xl:p-14">
        {/* Soft radial glow anchoring the brand color */}
        <div
          aria-hidden
          className="pointer-events-none absolute -left-24 top-1/4 size-[32rem] rounded-full opacity-40 blur-3xl"
          style={{ background: "radial-gradient(circle, var(--brand-blue-soft), transparent 65%)" }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -right-16 bottom-0 size-[24rem] rounded-full opacity-30 blur-3xl"
          style={{ background: "radial-gradient(circle, var(--brand-blue-glow), transparent 70%)" }}
        />

        <div className="relative flex items-center justify-between">
          <BrandLogo className="h-9" />
          <span className="rounded-full border border-brand-blue/25 bg-brand-blue/10 px-3 py-1 text-[11px] font-medium uppercase tracking-wider text-brand-blue">
            by nAutomation Labs
          </span>
        </div>

        <div className="relative max-w-lg space-y-7">
          <h1 className="text-4xl font-medium leading-[1.05] tracking-tight text-brand-heading xl:text-[44px]">
            Every missed call
            <br />
            <span className="text-brand-blue">is a customer</span>
            <br />
            who rang someone else.
          </h1>
          <p className="max-w-md text-[15px] leading-relaxed text-brand-body">
            Decibyl answers your phone in Hindi, Tamil, Telugu and eight more —
            books the appointment, qualifies the lead, and hands anything real
            to a person. Set one up yourself in ten minutes.
          </p>
          <ul className="flex flex-wrap gap-2">
            {HIGHLIGHTS.map((point) => (
              <li
                key={point}
                className="rounded-full border border-brand-chip-border bg-brand-chip px-3 py-1 text-xs font-medium text-brand-chip-fg"
              >
                {point}
              </li>
            ))}
          </ul>
        </div>

        {/* Enterprise CTA block */}
        <div className="relative max-w-md space-y-3 rounded-[var(--radius-large)] border border-brand-chip-border bg-brand-card p-5">
          <h2 className="text-sm font-semibold text-brand-heading">
            Need on-prem, data residency &amp; a data perimeter?
          </h2>
          <p className="text-sm text-brand-body">
            We deploy Decibyl inside your environment for regulated and
            high-scale teams.
          </p>
          {enterpriseSlot}
        </div>
      </aside>

      {/* Form column (RIGHT) — scrolls and stays centered so tall forms never
          clip. The theme toggle that sat in the top-right corner is gone with
          the dark theme: under forcedTheme="light" it rendered a control that
          changed nothing, which is worse than no control. */}
      <main className="auth-imprint relative flex min-h-screen flex-col overflow-y-auto">
        <div className="flex min-h-full items-center justify-center p-6 sm:p-10">
          <div className="w-full max-w-md space-y-6">
            {/* Mobile-only wordmark (brand panel is hidden) */}
            <div className="mb-2 lg:hidden">
              <BrandLogo className="h-8" />
            </div>
            <div className="space-y-6 rounded-[var(--radius-large)] border border-border bg-card p-6 sm:p-8">
              {children}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
