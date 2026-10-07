import "./globals.css";
import "./shell-v2.css";
import "./motion.css";
import "./simple-mode.css";

import type { Metadata, Viewport } from "next";
import {
  Familjen_Grotesk,
  Geist_Mono,
  Inter,
  Martian_Mono,
  Schibsted_Grotesk,
} from "next/font/google";
import { Suspense } from "react";

import ChatwootWidget from "@/components/ChatwootWidget";
import { DesktopSession } from "@/components/desktop/DesktopSession";
import AppLayout from "@/components/layout/AppLayout";
import PostHogIdentify from "@/components/PostHogIdentify";
import { SentryErrorBoundary } from "@/components/SentryErrorBoundary";
import SessionReplayGuard from "@/components/SessionReplayGuard";
import SpinLoader from "@/components/SpinLoader";
import { ThemeProvider } from "@/components/ThemeProvider";
import { Toaster } from "@/components/ui/sonner";
import { VoiceProvider } from "@/components/voice/VoiceProvider";
import { AppConfigProvider } from "@/context/AppConfigContext";
import { OnboardingProvider } from "@/context/OnboardingContext";
import { OrgConfigProvider } from "@/context/OrgConfigContext";
import { TelephonyConfigWarningsProvider } from "@/context/TelephonyConfigWarningsContext";
import { AuthProvider } from "@/lib/auth";
import { THEME_BOOT_SCRIPT } from "@/lib/themes";

// Lato, self-hosted by next/font at build time rather than fetched from a CDN
// at runtime — a restricted deployment has no egress to fonts.gstatic.com, and
// a missing webfont silently falls back to the system stack.
//
// One face for the whole product (15 Sept). The app ran Inter for body, a
// geometric display face for h1/h2 and a third for code, and the three
// never agreed: a product screen with a display heading over grotesque body
// text reads as two products. Slack's own UI is one humanist sans, Lato, at
// 15px, bold for emphasis and nothing in between — and Lato is on Google
// Fonts under the OFL. It ships 400, 700 and 900 only, which is the point:
// Tailwind's font-medium (500) resolves to regular and font-semibold (600)
// to bold, so the whole app collapses to the two weights Slack uses.
//
// The variable must also be *consumed*: a previous attempt set the font here
// and mapped it under `@theme inline`, which does not emit `--font-sans` as a
// custom property, so `--default-font-family` fell through to its own fallback
// and every element kept rendering in the system font. globals.css now sets
// font-family on html explicitly. Verify with getComputedStyle, not by reading.
const appSans = Inter({
  variable: "--font-app-sans",
  subsets: ["latin"],
  display: "swap",
});

const appMono = Geist_Mono({
  variable: "--font-app-mono",
  subsets: ["latin"],
});

// The v2 shell's three faces (KAN-208, behind `ui_shell_v2`), self-hosted like
// the rest. `preload: false`: nothing is fetched on first paint, and a browser
// downloads a face only when text on the page is set in it -- which happens
// only inside `.shell-v2` (see shell-v2.css). With the flag off they cost a
// few @font-face rules and no bytes.
const v2Display = Familjen_Grotesk({
  variable: "--font-v2-display",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  preload: false,
});

const v2Body = Schibsted_Grotesk({
  variable: "--font-v2-body",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
  preload: false,
});

const v2Mono = Martian_Mono({
  variable: "--font-v2-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
  preload: false,
});

export const metadata: Metadata = {
  title: "Decibyl — AI teammates for Indian businesses",
  description:
    "Add an agent for a job — answering the phone, confirming orders, chasing payments, answering from your own documents. Self-hostable, BYOK, MCP-native.",
};

// viewport-fit=cover so env(safe-area-inset-*) reports the notch and the
// home indicator on phones: without it every inset is 0 and the bottom bar
// and composer sit under the home indicator. The shell pads by those insets
// (AppLayout, MobileTabBar, the onboarding and early-access pages).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // The font variables go on <html>, not <body>. globals.css sets
    // font-family on html, and a var() referenced on an element that does not
    // carry it resolves to nothing — which makes the whole declaration invalid
    // and drops the element to the browser default serif. That failure looks
    // like a font bug and is actually a scoping bug.
    <html
      lang="en"
      className={`${appSans.variable} ${appMono.variable} ${v2Display.variable} ${v2Body.variable} ${v2Mono.variable}`}
      suppressHydrationWarning
    >
      <head>
        {/* Before first paint: a chosen theme (Settings → Appearance) goes
            back on, and the accent colour older builds stored -- the purple
            -- is cleared. The default theme needs nothing; it is globals.css.
            See lib/themes.ts. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="antialiased">
        {/* Light unless the person chose otherwise in Settings → Appearance.
            Its own storage key, so a "theme" value the retired sidebar toggle
            left behind is never replayed as a surprise dark mode. */}
        <ThemeProvider
          attribute="class"
          defaultTheme="light"
          enableSystem
          storageKey="decibyl.theme"
          disableTransitionOnChange
        >
          <SentryErrorBoundary>
            <AuthProvider>
              <AppConfigProvider>
                <Suspense fallback={<SpinLoader />}>
                  <OrgConfigProvider>
                    <TelephonyConfigWarningsProvider>
                      <OnboardingProvider>
                        <PostHogIdentify />
                        <DesktopSession />
                        <SessionReplayGuard />
                        <AppLayout>{children}</AppLayout>
                        <VoiceProvider />
                        <Toaster />
                        <ChatwootWidget />
                      </OnboardingProvider>
                    </TelephonyConfigWarningsProvider>
                  </OrgConfigProvider>
                </Suspense>
              </AppConfigProvider>
            </AuthProvider>
          </SentryErrorBoundary>
        </ThemeProvider>
      </body>
    </html>
  );
}
