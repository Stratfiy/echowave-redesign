import "./globals.css";

import type { Metadata } from "next";
import { Geist_Mono,Inter } from "next/font/google";
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
import { AuthProvider } from "@/lib/auth";


// Inter, self-hosted by next/font at build time rather than fetched from a CDN
// at runtime — a restricted deployment has no egress to fonts.gstatic.com, and
// a missing webfont silently falls back to the system stack.
//
// Inter is the reference system's body face and does the work from 11px
// micro-labels up to 30px headings. The reference pairs it with Satoshi for
// 36px+ display type; Satoshi is not on Google Fonts and self-hosting it would
// mean shipping a licensed binary, so we take the substitute the reference
// itself names — Inter at weight 500 with -0.02em tracking, which is what the
// h1/h2 rule in globals.css applies. Weight 300 is gone with it: the display
// treatment is medium now, and nothing else used it.
//
// The variable must also be *consumed*: a previous attempt set the font here
// and mapped it under `@theme inline`, which does not emit `--font-sans` as a
// custom property, so `--default-font-family` fell through to its own fallback
// and every element kept rendering in the system font. globals.css now sets
// font-family on html explicitly. Verify with getComputedStyle, not by reading.
const appSans = Inter({
  variable: "--font-app-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const appMono = Geist_Mono({
  variable: "--font-app-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Decibyl — Voice AI Platform by nAutomation Labs",
  description: "Build production voice AI agents with a visual workflow builder. Self-hostable, BYOK, MCP-native.",
};

export default function RootLayout({
  children
}: {
  children: React.ReactNode
}) {

  return (
    // The font variables go on <html>, not <body>. globals.css sets
    // font-family on html, and a var() referenced on an element that does not
    // carry it resolves to nothing — which makes the whole declaration invalid
    // and drops the element to the browser default serif. That failure looks
    // like a font bug and is actually a scoping bug.
    <html lang="en" className={`${appSans.variable} ${appMono.variable}`} suppressHydrationWarning>
      <head>
        {/* The anti-flash script that used to live here restored a stored 'dark'
            class before hydration. The app is light-only now, so it would only
            reintroduce a theme nothing styles — and a stale localStorage entry
            from a previous visit would have kept doing so indefinitely. */}
      </head>
      <body className="antialiased">
        {/* forcedTheme keeps the provider mounted — every consumer of useTheme
            still resolves — while guaranteeing the .dark class is never set. */}
        <ThemeProvider attribute="class" forcedTheme="light" defaultTheme="light" enableSystem={false} disableTransitionOnChange>
          <SentryErrorBoundary>
            <AuthProvider>
              <AppConfigProvider>
                <Suspense fallback={<SpinLoader />}>
                  <OrgConfigProvider>
                    <TelephonyConfigWarningsProvider>
                      <OnboardingProvider>
                        <PostHogIdentify />
                        <AppLayout>
                          {children}
                        </AppLayout>
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
