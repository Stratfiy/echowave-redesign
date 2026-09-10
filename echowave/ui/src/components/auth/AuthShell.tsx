// Decibyl auth shell: one centred column, the form card in the middle.
//
// Near-white canvas, ink text, one accent — Decibyl coral — and a single
// gradient orb doing the work a hero illustration usually does. The old
// two-column split put the form off to one side and filled the other half
// with chips and a sales block; a visitor's eye went everywhere except the
// thing they came to do. Now the card is the centre of the page, the brand
// sits above it, and the enterprise line is a quiet footer for the few who
// need it.
//
// The copy still says what the product does for the buyer — answer the
// phone — rather than the developer-tool pitch. Self-hosting and BYOK stay
// true and stay in the enterprise line, not the first sentence.

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
    <div className="relative flex min-h-screen w-full flex-col items-center overflow-x-hidden bg-background px-6 py-10 text-foreground sm:py-14">
      {/* The orb. One soft coral sphere, sitting behind the top of the page.
          Decorative only: it is the sole chromatic element on an otherwise
          achromatic canvas, so it must never compete with the form. */}
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 -z-0 flex justify-center">
        <div
          className="mt-[-9rem] size-[26rem] rounded-full opacity-[0.55] blur-3xl sm:size-[34rem]"
          style={{ background: "var(--brand-gradient)" }}
        />
      </div>

      {/* Brand */}
      <header className="relative z-10 flex flex-col items-center gap-3">
        <BrandLogo className="h-9" />
        <span
          className="rounded-full border px-3 py-1 text-[11px] font-medium uppercase tracking-wider"
          style={{
            borderColor: "var(--accent-brand-soft)",
            background: "var(--accent-brand-tint)",
            color: "var(--accent-brand)",
          }}
        >
          by nAutomation Labs
        </span>
      </header>

      {/* Headline. 400 weight, tight leading, one accented phrase. */}
      <div className="relative z-10 mt-8 max-w-xl text-center">
        <h1 className="text-[28px] font-normal leading-[1.1] tracking-[-0.01em] text-brand-heading sm:text-[34px]">
          Every missed call{" "}
          <span style={{ color: "var(--accent-brand)" }}>is a customer</span>{" "}
          who rang someone else.
        </h1>
        <p className="mx-auto mt-3 max-w-md text-[15px] leading-relaxed text-brand-body">
          Decibyl answers your phone in Hindi, Tamil, Telugu and eight more —
          books the appointment, qualifies the lead, and hands anything real to
          a person. Set one up yourself in ten minutes.
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
