"use client";

import {
  AlertTriangle,
  ArrowUpCircle,
  Bot,
  CalendarClock,
  ChartColumnBig,
  ChevronLeft,
  ChevronRight,
  Database,
  Home,
  LifeBuoy,
  LogOut,
  Settings,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import posthog from "posthog-js";
import React, { useEffect, useRef } from "react";

import { OrganizationSwitcher } from "@/components/layout/OrganizationSwitcher";
import { SidebarBots } from "@/components/layout/SidebarBots";
import { SidebarChannels } from "@/components/layout/SidebarChannels";
import { SidebarTeamSwitcher } from "@/components/layout/SidebarTeamSwitcher";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { PostHogEvent } from "@/constants/posthog-events";
import { SETUP_CALL_LABEL, SETUP_CALL_URL } from "@/constants/setupCall";
import { useAppConfig } from "@/context/AppConfigContext";
import { useTelephonyConfigWarnings } from "@/context/TelephonyConfigWarningsContext";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { useLatestReleaseVersion } from "@/hooks/useLatestReleaseVersion";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

import {
  getActiveNavUrl,
  getContextSections,
  getVisibleNavSections,
  NAV_CONTEXTS,
  type NavContextId,
  type SidebarNavItem,
} from "./navigation";

const TELEPHONY_WARNING_COPY = "Action required";


/** Stable destinations: selecting one always navigates, never swaps the sidebar. */
const PINNED_ROWS: SidebarNavItem[] = [
  { title: "Decibyl", icon: Home, url: "/overview" },
  { title: "Tasks", icon: CalendarClock, url: "/tasks" },
  { title: "Agents", icon: Bot, url: "/workflow" },
  { title: "Knowledge", icon: Database, url: "/files" },
  { title: "Activity", icon: ChartColumnBig, url: "/usage" },
];

/** Marketplace, setup and account controls remain available from the account menu. */
const PERSONAL_CONTEXTS: NavContextId[] = ["marketplace", "setup", "account"];

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { state, isMobile, setOpenMobile } = useSidebar();
  const { user, logout, provider } = useAuth();
  const displayIdentity =
    user?.displayName ||
    (user as { primaryEmail?: string } | undefined)?.primaryEmail ||
    (user as { email?: string } | undefined)?.email ||
    "";
  const initials = displayIdentity
    ? displayIdentity
        .split(/[\s@._-]+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((part) => part[0]?.toUpperCase() ?? "")
        .join("")
    : "?";
  const { config } = useAppConfig();
  const {
    telnyxMissingWebhookPublicKeyCount,
    vonageMissingSignatureSecretCount,
  } = useTelephonyConfigWarnings();
  const hasTelephonyWarning =
    telnyxMissingWebhookPublicKeyCount > 0 ||
    vonageMissingSignatureSecretCount > 0;
  const isCollapsed = !isMobile && state === "collapsed";

  // Read for the self-hosted update check below, not for display. The
  // version used to sit beside the logo, where the first thing a customer
  // saw was a build number that means nothing to them and reads as stale
  // the moment it is a release behind.
  const versionInfo = config
    ? { ui: config.uiVersion, api: config.apiVersion }
    : null;

  // Check for updates only on self-hosted (OSS) deployments — cloud is managed for the user.
  const {
    latest: latestRelease,
    isBehind,
    isLatest,
  } = useLatestReleaseVersion(versionInfo?.ui, {
    enabled: config?.deploymentMode === "oss",
  });

  // Staff-ness and organization standing are server facts, read from the
  // server rather than inferred from anything the browser already holds. A
  // failure leaves both unprivileged: showing a staff link to a customer is
  // worse than making a reviewer reload.
  const roles = useAccessRoles();

  const navSections = getVisibleNavSections({
    isStaff: roles.isStaff,
    isOrganizationAdmin: roles.isOrganizationAdmin,
    isSuperadmin: roles.staffRole === "superadmin",
  });
  const activeUrl = getActiveNavUrl(pathname, navSections);

  const accountGroups = PERSONAL_CONTEXTS.map((id) => ({
    id,
    title: NAV_CONTEXTS.find((context) => context.id === id)!.title,
    items: getContextSections(id, navSections).flatMap((section) => section.items)
      .filter((item) => !PINNED_ROWS.some((row) => row.url === item.url)),
  }));
  const activityLinks = getContextSections("activity", navSections)
    .flatMap((section) => section.items).filter((item) => item.url !== "/usage");
  const activeLinkRef = useRef<HTMLAnchorElement | null>(null);
  useEffect(() => {
    activeLinkRef.current?.scrollIntoView({ block: "nearest" });
  }, [pathname]);

  const handleMobileNavClick = () => {
    if (isMobile) {
      setOpenMobile(false);
    }
  };

  const SidebarLink = ({ item }: { item: SidebarNavItem }) => {
    const isConversation = /^\/workflow\/[^/]+\/thread(?:\/|$)/.test(pathname) || pathname.startsWith("/channels/");
    const isItemActive = !isConversation && activeUrl === item.url;
    const Icon = item.icon;
    const showWarningDot = item.showsTelephonyWarning && hasTelephonyWarning;
    const tooltip = {
      children: (
        <div className="notranslate" translate="no">
          <p>{item.title}</p>
          {showWarningDot && (
            <p className="text-amber-600 dark:text-amber-400">
              {TELEPHONY_WARNING_COPY}
            </p>
          )}
        </div>
      ),
    };
    const warningIndicator = (
      <AlertTriangle
        aria-label="Action required on a telephony configuration"
        className={cn(
          "text-amber-500",
          isCollapsed
            ? "absolute -right-0.5 -top-0.5 h-3 w-3"
            : "ml-auto h-3.5 w-3.5",
        )}
      />
    );

    return (
      <SidebarMenuButton
        asChild
        tooltip={tooltip}
        isActive={isItemActive}
        // Selected state: a pale tint of the accent with the icon in full
        // accent, which is the one place besides a primary button where the
        // orange is allowed to appear. The previous treatment stacked a tinted
        // fill, a left bar AND a 6px glow behind the icon on the same item —
        // three signals to say the one thing a fill already says.
        className={cn(
          "rounded-md text-sidebar-foreground/85 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
          isItemActive &&
            "bg-sidebar-accent font-semibold text-sidebar-accent-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
        )}
      >
        <Link
          ref={isItemActive ? activeLinkRef : undefined}
          href={item.url}
          aria-current={isItemActive ? "page" : undefined}
          onClick={handleMobileNavClick}
          className={cn("relative", isCollapsed && "justify-center")}
          translate="no"
        >
          <Icon
            className={cn(
              "h-4 w-4 shrink-0",
              isItemActive
                ? "text-sidebar-accent-foreground"
                : "text-sidebar-foreground/70",
            )}
          />
          <span
            className={cn(
              "notranslate min-w-0 flex-1 truncate",
              isCollapsed && "sr-only",
            )}
            translate="no"
          >
            {item.title}
          </span>
          {showWarningDot &&
            (isCollapsed ? (
              warningIndicator
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>{warningIndicator}</TooltipTrigger>
                <TooltipContent side="right">
                  <p>{TELEPHONY_WARNING_COPY}</p>
                </TooltipContent>
              </Tooltip>
            ))}
        </Link>
      </SidebarMenuButton>
    );
  };

  // Used to read "Hire an Expert" — an offer to sell an agency, permanently
  // visible, on a product sold on not needing one. Now it books a setup call
  // instead: same help, no contradiction. It lives on the rail beside
  // Account, as an icon with a tooltip, whatever the panel is doing.
  const setupCallButton = (
    <Tooltip>
      <TooltipTrigger asChild>
        <a
          href={SETUP_CALL_URL}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Get help — ${SETUP_CALL_LABEL}`}
          onClick={() =>
            posthog.capture(PostHogEvent.HIRE_EXPERT_OPENED, {
              source: "sidebar",
            })
          }
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-rail-foreground/70 transition-colors hover:bg-black/5 hover:text-rail-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"
        >
          <LifeBuoy className="h-4 w-4" />
        </a>
      </TooltipTrigger>
      <TooltipContent side="top">
        <p>{SETUP_CALL_LABEL}</p>
      </TooltipContent>
    </Tooltip>
  );

  return (
    <Sidebar collapsible="icon" variant="sidebar" className="app-sidebar">
      <SidebarContent
        className="notranslate h-full flex-col gap-0 p-0"
        translate="no"
      >
        {/* The head: the brand mark and the workspace name on one row, the
            way Buzz heads its panel. `data-rail` marks it as chrome so the
            panel's own Decibyl row is found apart from the mark. */}
        <div
          data-rail=""
          className={cn(
            "flex min-h-11 items-center gap-2 px-2 text-rail-foreground [&_button]:text-rail-foreground [&_button:hover]:bg-black/5 [&_button_svg]:text-rail-foreground/70",
            isCollapsed && "justify-center px-0",
          )}
        >
          <Link
            href="/"
            onClick={handleMobileNavClick}
            aria-label="Decibyl"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[var(--accent-brand)] text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-brand)]"
          >
            <span className="text-sm font-semibold leading-none">d</span>
          </Link>
          {!isCollapsed && (
            <>
              <div className="min-w-0 flex-1">
                <OrganizationSwitcher collapsed={isCollapsed} />
              </div>
              {isMobile && <button type="button" aria-label="Close navigation" onClick={() => setOpenMobile(false)} className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"><X className="h-5 w-5" /></button>}
              {isBehind && latestRelease && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <a
                      href="https://docs.decibyl.ai/deployment/update"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex shrink-0 items-center gap-1 rounded-md border bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium leading-none text-amber-900 transition-opacity hover:opacity-80"
                    >
                      <ArrowUpCircle className="h-3 w-3" />
                      Update
                    </a>
                  </TooltipTrigger>
                  <TooltipContent side="top">
                    <p>
                      Latest: {latestRelease} - click to see the update guide
                    </p>
                  </TooltipContent>
                </Tooltip>
              )}
              {isLatest && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="inline-flex shrink-0 items-center rounded-md border bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium leading-none text-emerald-900">
                      Latest
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="top">
                    <p>You&apos;re running the latest release</p>
                  </TooltipContent>
                </Tooltip>
              )}
            </>
          )}
        </div>
        {provider === "stack" && !isCollapsed && (
          <div className="notranslate px-2 pb-1" translate="no">
            <SidebarTeamSwitcher />
          </div>
        )}

        <nav aria-label="Workspace" data-rail="" className="px-1 pt-1">
          <SidebarMenu>
            {PINNED_ROWS.map((item) => <SidebarMenuItem key={item.url}><SidebarLink item={item} /></SidebarMenuItem>)}
          </SidebarMenu>
        </nav>
        <div className={cn("min-w-0 flex-1 overflow-y-auto px-1 py-1", isCollapsed && "hidden")}
          onClick={(event) => { if ((event.target as HTMLElement).closest("a[href]")) handleMobileNavClick(); }}>
          {activityLinks.length > 0 && <SidebarGroup aria-label="Activity tools" className="px-0 py-1">
            <p className="px-2 py-1 text-xs text-sidebar-foreground/60">Activity tools</p>
            <SidebarMenu>{activityLinks.map((item) => <SidebarMenuItem key={item.url}><SidebarLink item={item} /></SidebarMenuItem>)}</SidebarMenu>
          </SidebarGroup>}
          <SidebarChannels collapsed={isCollapsed} />
          <SidebarBots collapsed={isCollapsed} />
        </div>
      </SidebarContent>

      {/* The foot is the person, as Buzz's profile card: who is signed in,
          the setup call, the fold. It stays put when the panel scrolls. */}
      <SidebarFooter className="gap-1 px-1 pb-2 pt-1">
        <div className={cn("flex items-center gap-1", isCollapsed && "flex-col")}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label="Account menu"
                className={cn(
                  "flex min-w-0 flex-1 items-center gap-2 rounded-xl px-2 py-1.5 text-left transition-colors hover:bg-sidebar-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
                  isCollapsed && "flex-none justify-center px-0",
                )}
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-sidebar-border bg-background/60 text-xs font-medium text-sidebar-foreground">
                  {initials}
                </span>
                {!isCollapsed && (
                  <span className="flex min-w-0 flex-1 flex-col leading-tight">
                    <span className="truncate text-sm font-medium text-sidebar-foreground">
                      {user?.displayName || displayIdentity || "You"}
                    </span>
                    {displayIdentity && user?.displayName && (
                      <span className="truncate text-[11px] text-sidebar-foreground/60">{displayIdentity}</span>
                    )}
                  </span>
                )}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" side="top" className="max-h-[70vh] w-64 overflow-y-auto">
              {accountGroups.map((group) => group.items.length > 0 && <React.Fragment key={group.id}>
                <DropdownMenuLabel>{group.title}</DropdownMenuLabel>
                {group.items.map((item) => <DropdownMenuItem key={item.url} asChild>
                  <Link href={item.url} aria-current={activeUrl === item.url ? "page" : undefined} onClick={handleMobileNavClick}>
                    <item.icon className="mr-2 h-4 w-4" />{item.title}
                    {item.showsTelephonyWarning && hasTelephonyWarning && <AlertTriangle aria-label={TELEPHONY_WARNING_COPY} className="ml-auto h-4 w-4 text-amber-500" />}
                  </Link>
                </DropdownMenuItem>)}
                <DropdownMenuSeparator />
              </React.Fragment>)}
              {provider === "stack" && (
                <DropdownMenuItem onClick={() => { handleMobileNavClick(); router.push("/handler/account-settings"); }} className="cursor-pointer">
                  <Settings className="mr-2 h-4 w-4" />
                  Account settings
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => { handleMobileNavClick(); logout(); }} className="cursor-pointer">
                <LogOut className="mr-2 h-4 w-4" />
                Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          {setupCallButton}
          <SidebarTrigger
            className="h-8 w-8 shrink-0 rounded-md text-rail-foreground/70 hover:bg-black/5 hover:text-rail-foreground"
            aria-label={isCollapsed ? "Open the panel" : "Fold the panel away"}
          >
            {isCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </SidebarTrigger>
        </div>
      </SidebarFooter>

      <SidebarRail />
    </Sidebar>
  );
}
