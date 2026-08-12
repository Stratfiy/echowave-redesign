import "./globals.css";

import type { Metadata } from "next";
import { Geist_Mono, Poppins } from "next/font/google";
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


// Poppins, self-hosted by next/font at build time — not fetched from a CDN at
// runtime, so it cannot silently fall back and needs no font-src exception.
//
// Geometric humanist with rounded terminals: it sits with pill buttons and
// large card radii in a way Geist's tighter grotesk never did. 300 is here
// because display headlines are set light — that weight is the whole reason
// this face reads as friendly rather than corporate.
const appSans = Poppins({
  variable: "--font-geist-sans",
  weight: ["300", "400", "500", "600", "700"],
  subsets: ["latin"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Decibyl — Voice AI Platform by nAutomation Labs",
  description: "Build production voice AI agents with a visual workflow builder. Seven Indian languages, BYOK, MCP-native.",
};

export default function RootLayout({
  children
}: {
  children: React.ReactNode
}) {

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* There is one theme now, and it is light. This script used to honour a
            stored 'dark' preference; it runs before React hydrates so it is
            also the only place that can strip a preference already sitting in
            a returning user's localStorage. Without the eviction those users
            would boot into a dark theme whose tokens no longer exist, with no
            toggle left to escape it. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function() {
                try {
                  document.documentElement.classList.remove('dark');
                  localStorage.removeItem('theme');
                } catch (e) {}
              })();
            `,
          }}
        />
      </head>
      <body
        className={`${appSans.variable} ${geistMono.variable} antialiased`}>
        <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false} disableTransitionOnChange>
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
