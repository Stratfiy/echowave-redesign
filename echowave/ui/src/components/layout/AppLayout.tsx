"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";
import { usePathname } from "next/navigation";
import React, { ReactNode,useEffect } from "react";

import { AgreementsGate } from "@/components/auth/AgreementsGate";
import { ImpersonationBanner } from "@/components/auth/ImpersonationBanner";
import { VerifyEmailBanner } from "@/components/auth/VerifyEmailBanner";
import { Button } from "@/components/ui/button";
import { SidebarInset, SidebarProvider, useSidebar } from "@/components/ui/sidebar";
import { useAppConfig } from "@/context/AppConfigContext";
import { LeadFormsProvider } from "@/context/LeadFormsContext";
import { useFeature } from "@/lib/features";
import { applyTheme, readStoredTheme } from "@/lib/themes";
import { cn } from "@/lib/utils";

import { AppSidebar } from "./AppSidebar";
import { TopBar } from "./TopBar";
import { AppRailV2 } from "./v2/AppRailV2";

/** The mock's rail is 224px; a touch wider for the workspace switcher. */
const V2_RAIL_STYLE = { "--sidebar-width": "15rem" } as React.CSSProperties;

function BackendStatusBanner() {
  const { config, loading, refresh } = useAppConfig();

  if (!config || config.backendStatus === "reachable") {
    return null;
  }

  const backendUrl = config.backendUrl && config.backendUrl !== "unknown"
    ? config.backendUrl
    : "the configured backend";
  const message = config.backendMessage || `Backend is not reachable at ${backendUrl}.`;

  return (
    <div
      role="alert"
      className="border-b border-amber-300 bg-amber-50 px-4 py-3 text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <div className="min-w-0">
            <p className="text-sm font-semibold">Backend connection failed</p>
            <p className="break-words text-sm">{message}</p>
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void refresh()}
          disabled={loading}
          className="h-8 shrink-0 border-amber-400 bg-transparent text-amber-950 hover:bg-amber-100 dark:border-amber-700 dark:text-amber-100 dark:hover:bg-amber-900/40"
        >
          <RefreshCw className="h-4 w-4" />
          Retry
        </Button>
      </div>
    </div>
  );
}

interface AppLayoutProps {
  children: ReactNode;
}

/**
 * The rail remembers whether it was collapsed on this device.
 *
 * The sidebar writes a cookie on every toggle and nothing read it back, so
 * every page load opened the rail again. Read after mount rather than at
 * render: the server has no cookie, and a first render that disagreed with
 * it would be a hydration mismatch on every page.
 */
/** The chosen theme, applied on arrival -- and whatever accent colour an
 *  older build stored (the purple) cleared for good. */
function ThemeRestorer() {
  useEffect(() => {
    applyTheme(readStoredTheme(), { store: false });
  }, []);
  return null;
}

function SidebarStateRestorer() {
  const { setOpen } = useSidebar();
  useEffect(() => {
    try {
      const match = document.cookie.match(/(?:^|; )sidebar_state=(true|false)/);
      if (match) setOpen(match[1] === "true");
    } catch {
      // No cookie access: the rail stays open, which is the safe default.
    }
  }, [setOpen]);
  return null;
}

const AppLayout: React.FC<AppLayoutProps> = ({ children }) => {
  const pathname = usePathname();
  // KAN-208 UI-1: the new shell, off unless the flag is on. With it off the
  // tree below is exactly what it was.
  const shellV2 = useFeature("ui_shell_v2");

  // Check if current route should have sidebar
  // Hide sidebar for root (/), /handler routes (Stack Auth routes), and /auth routes
  // /start is the first-agent journey: one job, no navigation to wander off
  // into. It draws its own step rail instead.
  const shouldShowSidebar =
    pathname !== "/" &&
    !pathname.startsWith("/handler") &&
    !pathname.startsWith("/auth") &&
    !pathname.startsWith("/start") &&
    // The public share page: a prospect, no account, no rail.
    !pathname.startsWith("/talk") &&
    // The trust page, for the same reason: it answers a security review
    // before anybody has signed up, and a stranger was being shown a rail of
    // rooms they cannot open, under an account row that says "You".
    !pathname.startsWith("/trust") &&
    // The public marketplace: browsed before an account exists.
    !pathname.startsWith("/agents");

  // Only match the exact editor page /workflow/<id>, not sub-routes like /workflow/<id>/runs
  const isWorkflowEditor = /^\/workflow\/\d+$/.test(pathname);

  // Always render SidebarProvider to keep the component tree shape consistent
  // across route changes (avoids React hooks ordering violations during navigation).
  return (
    <SidebarProvider
      defaultOpen
      className={cn("app-shell", shellV2 && "shell-v2")}
      style={shellV2 ? V2_RAIL_STYLE : undefined}
    >
      <SidebarStateRestorer />
      <ThemeRestorer />
      {shouldShowSidebar ? (
        <LeadFormsProvider>
          {/* h-screen, not min-h-screen: the column is bounded, so a page
              that wants to scroll its own list (a chat) can say h-full and
              have it mean something. Ordinary pages scroll inside <main>. */}
          <div className="flex h-dvh w-full">
            {shellV2 ? <AppRailV2 /> : <AppSidebar />}
            <SidebarInset className="min-h-0 min-w-0 flex-1">
              <BackendStatusBanner />
              <ImpersonationBanner />
              <VerifyEmailBanner />
              <AgreementsGate />
              {/* The workflow editor is the one full-bleed canvas in the app —
                  it needs the whole viewport, so it opts out of the top bar. */}
              {!isWorkflowEditor && <TopBar />}
              {/* A page's own title band and tabs come from `PageHeader`,
                  rendered by the page itself. This shell deliberately offers no
                  second way to put a header on a screen — two of them is how the
                  app ended up with titles at different sizes in different
                  places. */}
              {/* The white card inside the gradient frame, the way Buzz sets
                  its channel pane: rounded, a hairline, a breath of margin on
                  the open sides. Pages scroll inside it, so a sticky header
                  stays pinned to the card's top edge.

                  The inset is desktop-only. On a phone the frame is not on
                  screen -- the panel is a sheet -- so a rounded card with a
                  margin down one side was 8px of gradient beside the content
                  and nothing else: the cost of a frame with none of the
                  point of one. */}
              <main className="app-surface app-card min-h-0 flex-1 overflow-y-auto md:mb-2 md:mr-2 md:rounded-2xl">
                {children}
              </main>
            </SidebarInset>
          </div>
        </LeadFormsProvider>
      ) : (
        <div className="app-surface w-full flex-1">
          <BackendStatusBanner />
          <ImpersonationBanner />
          {children}
        </div>
      )}
    </SidebarProvider>
  );
};

export default AppLayout;
