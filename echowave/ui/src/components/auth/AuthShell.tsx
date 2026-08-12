// Decibyl auth shell. Two columns: LEFT a brand/value panel with the animated
// waveform, feature chips and enterprise CTA; RIGHT the auth form card. Mobile
// collapses to a single column.
//
// This is the first screen a customer sees, and the one place where showing the
// brand is the job — so the panel takes a flat peach tint. It is a tint, not a
// gradient and not a glow: an earlier pass ran a three-stop orange gradient
// here with two blurred radial blobs on top of it, and that page is most of
// what made the rebrand read as an alarm rather than a brand.

import type { ReactNode } from "react";

import { BrandLogo } from "@/components/BrandLogo";

const HIGHLIGHTS = [
  "Speech-to-speech",
  "MCP-native",
  "BYOK · any model",
  "7 Indian languages",
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
        <div className="relative flex items-center justify-between">
          <BrandLogo className="h-9" />
          <span className="rounded-sm border border-brand-chip-border bg-white px-3 py-1 text-[11px] font-medium uppercase tracking-wider text-brand-chip-fg">
            by nAutomation Labs
          </span>
        </div>

        <div className="relative max-w-lg space-y-7">
          <div className="auth-waveform" aria-hidden>
            <span /><span /><span /><span /><span /><span /><span /><span />
          </div>
          {/* Light 300 with negative tracking is the display treatment the switch
              to Poppins was made for, and the thing that most separates this
              from the 700-weight SaaS default. */}
          <h1 className="text-4xl font-light leading-[1.05] tracking-[-0.02em] text-brand-heading xl:text-[44px]">
            Build production
            <br />
            <span className="font-normal text-brand-blue">voice AI agents</span>
            <br />
            without vendor lock-in.
          </h1>
          <p className="max-w-md text-[15px] leading-relaxed text-brand-body">
            Decibyl runs your confirmation, follow-up and reminder calls end to end — in Hindi, Tamil, Telugu, Kannada, Marathi, Gujarati and English. Design the conversation visually, connect any LLM / STT / TTS provider, and ship in minutes.
          </p>
          <ul className="flex flex-wrap gap-2">
            {HIGHLIGHTS.map((point) => (
              <li
                key={point}
                className="rounded-sm border border-brand-chip-border bg-white px-3 py-1 text-xs font-medium text-brand-chip-fg"
              >
                {point}
              </li>
            ))}
          </ul>
        </div>

        {/* Enterprise CTA block */}
        <div className="relative max-w-md space-y-3 rounded-3xl border border-brand-chip-border bg-brand-card p-5">
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
          clip. Carries the giant faded "decibyl" imprint along its bottom. */}
      <main className="auth-imprint relative flex min-h-screen flex-col overflow-y-auto">
        <div className="flex min-h-full items-center justify-center p-6 sm:p-10">
          <div className="w-full max-w-md space-y-6">
            {/* Mobile-only wordmark (brand panel is hidden) */}
            <div className="mb-2 lg:hidden">
              <BrandLogo className="h-8" />
            </div>
            {/* The shadow here was a hand-written rgba(15,60,120) — a blue drop
                shadow left over from the blue brand, on the product's first
                screen. It now takes the app's one shared elevation. */}
            <div className="space-y-6 rounded-3xl border border-border/60 bg-card p-6 shadow-sm sm:p-8">
              {children}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
