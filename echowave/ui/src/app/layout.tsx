import "./globals.css";

import type { Metadata } from "next";
import { Geist_Mono, Lato } from "next/font/google";
import { Suspense } from "react";

import ChatwootWidget from "@/components/ChatwootWidget";
import AppLayout from "@/components/layout/AppLayout";
import PostHogIdentify from "@/components/PostHogIdentify";
import { SentryErrorBoundary } from "@/components/SentryErrorBoundary";
import SpinLoader from "@/components/SpinLoader";
import { ThemeProvider } from "@/components/ThemeProvider";
import { Toaster } from "@/components/ui/sonner";
import { AppConfigProvider } from "@/context/AppConfigContext";
import { OnboardingProvider } from "@/context/OnboardingContext";
import { OrgConfigProvider } from "@/context/OrgConfigContext";
import { TelephonyConfigWarningsProvider } from "@/context/TelephonyConfigWarningsContext";
import { ACCENT_BOOT_SCRIPT } from "@/lib/accent";
import { AuthProvider } from "@/lib/auth";

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
const appSans = Lato({
  variable: "--font-app-sans",
  subsets: ["latin"],
  weight: ["400", "700", "900"],
  display: "swap",
});

const appMono = Geist_Mono({
  variable: "--font-app-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Decibyl — AI teammates for Indian businesses",
  description:
    "Hire a bot for a job — answering the phone, confirming orders, chasing payments, answering from your own documents. Self-hostable, BYOK, MCP-native.",
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
      className={`${appSans.variable} ${appMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        {/* The anti-flash script that used to live here restored a stored 'dark'
            class before hydration. The app is light-only now, so it would only
            reintroduce a theme nothing styles — and a stale localStorage entry
            from a previous visit would have kept doing so indefinitely.

            The accent is a different matter: it is a real preference, and
            restoring it after hydration would repaint every link and focus
            ring a beat late. This runs before first paint and only replays
            what the settings screen already computed — see lib/accent.ts. */}
        <script dangerouslySetInnerHTML={{ __html: ACCENT_BOOT_SCRIPT }} />
      </head>
      <body className="antialiased">
        {/* forcedTheme keeps the provider mounted — every consumer of useTheme
            still resolves — while guaranteeing the .dark class is never set. */}
        <ThemeProvider
          attribute="class"
          forcedTheme="light"
          defaultTheme="light"
          enableSystem={false}
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
                        <AppLayout>{children}</AppLayout>
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
