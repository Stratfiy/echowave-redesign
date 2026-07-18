// EchoWave auth shell — rebranded from the legacy dark 2-col layout to a
// premium light/blue nAutomation Labs aesthetic with an on-panel dark-mode
// toggle. LEFT: brand + value panel with animated waveform, feature chips,
// and enterprise CTA. RIGHT: the auth form card. Mobile collapses to a
// single column. The palette leans on the app's new blue accent
// (--brand-blue) with soft neutrals so both light and dark modes read as
// coherent variants of the same brand.

import type { ReactNode } from "react";

import { BrandLogo } from "@/components/BrandLogo";
import ThemeToggle from "@/components/ThemeSwitcher";

const HIGHLIGHTS = [
  "Speech-to-speech",
  "MCP-native",
  "BYOK · any model",
  "Self-hostable",
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
          <div className="auth-waveform" aria-hidden>
            <span /><span /><span /><span /><span /><span /><span /><span />
          </div>
          <h1 className="text-4xl font-semibold leading-[1.05] tracking-tight text-brand-heading xl:text-[44px]">
            Build production
            <br />
            <span className="text-brand-blue">voice AI agents</span>
            <br />
            without vendor lock-in.
          </h1>
          <p className="max-w-md text-[15px] leading-relaxed text-brand-body">
            EchoWave is the open, self-hostable voice AI platform. Design workflows visually, connect any LLM / STT / TTS provider, and ship real conversations in minutes.
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
        <div className="relative max-w-md space-y-3 rounded-2xl border border-brand-chip-border bg-brand-card/70 p-5 backdrop-blur-sm">
          <h2 className="text-sm font-semibold text-brand-heading">
            Need on-prem, data residency &amp; a data perimeter?
          </h2>
          <p className="text-sm text-brand-body">
            We deploy EchoWave inside your environment for regulated and
            high-scale teams.
          </p>
          {enterpriseSlot}
        </div>
      </aside>

      {/* Form column (RIGHT) — scrolls and stays centered so tall forms never
          clip. Carries the giant faded "echowave" imprint along its bottom. */}
      <main className="auth-imprint relative flex min-h-screen flex-col overflow-y-auto">
        <div className="absolute right-6 top-6 z-10">
          <ThemeToggle data-testid="auth-theme-toggle" />
        </div>
        <div className="flex min-h-full items-center justify-center p-6 sm:p-10">
          <div className="w-full max-w-md space-y-6">
            {/* Mobile-only wordmark (brand panel is hidden) */}
            <div className="mb-2 lg:hidden">
              <BrandLogo className="h-8" />
            </div>
            <div className="space-y-6 rounded-2xl border border-border/60 bg-card p-6 shadow-[0_20px_60px_-30px_rgba(15,60,120,0.25)] sm:p-8">
              {children}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
