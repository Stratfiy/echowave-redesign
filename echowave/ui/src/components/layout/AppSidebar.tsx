"use client";

import type { LucideIcon } from "lucide-react";
import {
  AlertTriangle,
  ArrowUpCircle,
  Bot,
  CalendarClock,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Database,
  LifeBuoy,
  LogOut,
  Settings,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import posthog from "posthog-js";
import React, { useEffect, useRef, useState } from "react";

import { OrganizationSwitcher } from "@/components/layout/OrganizationSwitcher";
import { SidebarBots } from "@/components/layout/SidebarBots";
import { SidebarChannels } from "@/components/layout/SidebarChannels";
import { SidebarTeamSwitcher } from "@/components/layout/SidebarTeamSwitcher";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
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
  contextIdForUrl,
  getActiveNavUrl,
  getContextSections,
  getVisibleNavSections,
  NAV_CONTEXTS,
  type NavContext,
  type NavContextId,
  type SidebarNavItem,
} from "./navigation";

const TELEPHONY_WARNING_COPY = "Action required";

/** Section labels are stored shouting ("DEVELOPERS") because the closed-set
 *  preference is keyed on them; the panel reads them the way Slack does,
 *  in sentence case at body size. */
function sentenceCase(label: string): string {
  return label.charAt(0) + label.slice(1).toLowerCase();
}

/** The rows pinned to the top of the panel, in reading order.
 *
 *  Buzz pins Inbox, Pulse, Projects, Agents and Workflows above its
 *  channel sections; this is the same shape read for a product whose
 *  members are bots. A context row opens that context's sections beneath
 *  it (Activity, Marketplace, Setup, Account); Home does that and goes
 *  home; Desk is a plain door onto the diary, the in-tray and the contact
 *  book, and Agents onto the roster, the way Buzz pins Agents above its
 *  channels. */
type PinnedRow =
  | { kind: "context"; title: string; icon: LucideIcon; context: NavContext }
  | { kind: "link"; title: string; icon: LucideIcon; url: string };

function contextRow(id: NavContextId): PinnedRow {
  const context = NAV_CONTEXTS.find((c) => c.id === id)!;
  return { kind: "context", title: context.title, icon: context.icon, context };
}

const PINNED_ROWS: PinnedRow[] = [
  contextRow("home"),
  contextRow("activity"),
  { kind: "link", title: "Desk", icon: CalendarClock, url: "/tasks" },
  // Buzz pins Agents above its channel sections, and the lists below are
  // Channels and Direct messages. This is that, read for a product whose
  // members are bots: Agents is the roster and what it can do, the lists
  // below are the conversations. Neither list's heading is a door now, so
  // this is the one way to the full list rather than the third.
  { kind: "link", title: "Agents", icon: Bot, url: "/workflow" },
];

/** The contexts behind the person, rather than above the bots.
 *
 *  Four rows are what a day needs: the thread, what the bots did, the desk,
 *  and the roster. The other three are a shop you visit when you want
 *  something, a setup you do once, and an account you check monthly -- and
 *  all three stood above the roster on every screen, on a phone most of all.
 *  Buzz keeps settings behind the profile card for the same reason.
 *
 *  Nothing is lost. Each opens its panel exactly as its row did, and the
 *  guard that every destination lives in exactly one panel still holds. */
const PERSONAL_CONTEXTS: NavContextId[] = ["marketplace", "setup", "account"];

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { state, isMobile, setOpenMobile, setOpen } = useSidebar();
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

  /* Which panel the rail is showing.
   *
   * Seeded from the page you are on, so arriving at /billing from a link opens
   * Account rather than leaving the rail pointing somewhere else — a rail that
   * disagrees with the screen is worse than no rail. Clicking a rail icon then
   * overrides it until you navigate, which is what makes browsing another
   * panel possible without leaving the page you are reading. */
  const [pickedContext, setPickedContext] = useState<NavContextId | null>(null);
  const routeContext = activeUrl ? contextIdForUrl(activeUrl) : "home";
  const activeContext = pickedContext ?? routeContext;
  useEffect(() => {
    setPickedContext(null);
  }, [pathname]);

  /* Collapsed is the rail on its own, so there is no panel to fill.
   *
   * It used to be the opposite: the rail was hidden and all seventeen
   * destinations were listed as bare glyphs in a column that needed its own
   * scrollbar. Seventeen unlabelled icons is not a navigation, it is a
   * memory test — and the one thing that *would* have survived the squeeze
   * intact, the five labelled contexts, was the thing being dropped. Nothing
   * becomes unreachable: every context is one click from here, and clicking
   * one opens the panel it names. */
  // Home shows places, not features. The nav rows -- a Home link, a Bots
  // link under a BUILD heading -- are the app's own table of contents, and in
  // the one panel that is supposed to be the workspace they read as chrome:
  // "Home" twice (the rail already says it), and "Bots" above a section
  // called YOUR BOTS. So Home renders the channels and the bots and nothing
  // else, the way the reference does. The rail's Home button carries the
  // navigation the removed link used to.
  const contextSections =
    isCollapsed || activeContext === "home"
      ? []
      : getContextSections(activeContext, navSections);
  const activeSection = navSections.find((section) =>
    section.items.some((item) => item.url === activeUrl),
  )?.label;
  const [closedSections, setClosedSections] = useState<string[]>([
    "MONITOR",
    "DEVELOPERS",
    "WORKSPACE",
  ]);
  useEffect(() => {
    try {
      const saved: unknown = JSON.parse(
        localStorage.getItem("decibyl.sidebar.closedSections") ?? "null",
      );
      if (
        Array.isArray(saved) &&
        saved.every((value) => typeof value === "string")
      )
        setClosedSections(saved);
    } catch {
      /* Storage may be unavailable in private browsing. */
    }
  }, []);
  useEffect(() => {
    if (activeSection)
      setClosedSections((current) =>
        current.filter((label) => label !== activeSection),
      );
  }, [pathname, activeSection]);
  const toggleSection = (label: string) => {
    const next = closedSections.includes(label)
      ? closedSections.filter((section) => section !== label)
      : [...closedSections, label];
    setClosedSections(next);
    try {
      localStorage.setItem(
        "decibyl.sidebar.closedSections",
        JSON.stringify(next),
      );
    } catch {
      /* Optional preference. */
    }
  };

  // Eighteen destinations do not fit a 900px viewport, so the list scrolls.
  // Left alone it rests at the top, which puts the *current* page half under
  // the footer — a selected item you cannot see reads as a broken sidebar
  // rather than a scrolled one. block: "nearest" means this only moves the
  // list when the active item is actually out of view.
  const activeLinkRef = useRef<HTMLAnchorElement | null>(null);
  useEffect(() => {
    activeLinkRef.current?.scrollIntoView({ block: "nearest" });
  }, [pathname, closedSections]);

  const handleMobileNavClick = () => {
    if (isMobile) {
      setOpenMobile(false);
    }
  };

  const SidebarLink = ({ item }: { item: SidebarNavItem }) => {
    const isItemActive = activeUrl === item.url;
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
      {/* pb-2 plus an opaque footer below: the nav list is taller than a 900px
          viewport once MANAGE has six entries, so the last item scrolls under
          the footer. Without a background on the footer it showed through and
          the final entry read as clipped rather than as scrolled. */}
      {/* Rail on the left, panel on the right.
          The model landed as a horizontal strip; this is the column it was
          always meant to be — five contexts down the edge, one panel beside
          them that swaps entirely. Same behaviour, and the tests that guard
          it are unchanged, because what they assert is which destinations a
          context offers rather than where the buttons sit.

          Every context is always rendered, which is the point. Filtering the
          panel without offering a way back to the others is how a destination
          silently stops being reachable. */}
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

        {/* The pinned rows, Buzz's Inbox / Pulse / Projects / Agents /
            Workflows read for this product: Home, Activity, Desk and
            Agents, with the conversations as the lists below. A context row
            opens its
            sections below; a plain row is a door. The five contexts were a
            vertical strip of icons down the edge; as rows they read like
            the rest of the panel and take the same tint when selected. */}
        <div role="tablist" aria-label="Workspace" data-rail="" className="px-1 pt-1">
          <SidebarMenu>
            {PINNED_ROWS.map((row) => {
              const Icon = row.icon;
              if (row.kind === "link") {
                const selected = activeUrl === row.url;
                return (
                  <SidebarMenuItem key={row.title}>
                    <SidebarMenuButton
                      asChild
                      tooltip={row.title}
                      isActive={selected}
                      className="rounded-md text-sidebar-foreground/85"
                    >
                      <Link
                        href={row.url}
                        aria-current={selected ? "page" : undefined}
                        onClick={handleMobileNavClick}
                        className={cn(isCollapsed && "justify-center")}
                      >
                        <Icon aria-hidden="true" className="h-4 w-4 shrink-0 text-sidebar-foreground/70" />
                        <span className={cn("truncate", isCollapsed && "sr-only")}>{row.title}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              }
              const context = row.context;
              const selected = context.id === activeContext;
              return (
                <SidebarMenuItem key={context.id}>
                  <SidebarMenuButton
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    aria-label={context.title}
                    tooltip={context.title}
                    isActive={selected}
                    onClick={() => {
                      setPickedContext(context.id);
                      if (isCollapsed) setOpen(true);
                      if (context.id === "home" && pathname !== "/overview") {
                        router.push("/overview");
                      }
                    }}
                    className={cn("rounded-md text-sidebar-foreground/85", isCollapsed && "justify-center")}
                  >
                    <Icon aria-hidden="true" className="h-4 w-4 shrink-0 text-sidebar-foreground/70" />
                    <span className={cn("truncate", isCollapsed && "sr-only")}>{context.title}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              );
            })}
          </SidebarMenu>
        </div>

        {/* What the selected row opens. Hidden when folded: a 64px column
            cannot hold a destination list, and the rows above are the doors. */}
        <div
          className={cn(
            "min-w-0 flex-1 overflow-y-auto px-1 py-1",
            isCollapsed && "hidden",
          )}
        >
          {contextSections.map((section) => (
            <React.Fragment key={section.label ?? "overview"}>
              <SidebarGroup className="py-1">
                {section.label && (
                  <SidebarGroupLabel
                    asChild
                    className="notranslate h-8 text-[15px] font-normal text-sidebar-foreground/70"
                    translate="no"
                  >
                    <button
                      type="button"
                      aria-expanded={!closedSections.includes(section.label)}
                      aria-controls={`nav-${section.label}`}
                      onClick={() => toggleSection(section.label!)}
                      className="w-full justify-between hover:text-sidebar-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"
                    >
                      {sentenceCase(section.label)}
                      <ChevronDown
                        aria-hidden="true"
                        className={cn(
                          "h-3.5 w-3.5 transition-transform",
                          closedSections.includes(section.label) && "-rotate-90",
                        )}
                      />
                    </button>
                  </SidebarGroupLabel>
                )}
                <SidebarMenu
                  id={section.label ? `nav-${section.label}` : undefined}
                  hidden={!!section.label && closedSections.includes(section.label)}
                  className={cn(
                    !!section.label && closedSections.includes(section.label) && "hidden",
                  )}
                >
                  {section.items.map((item) => (
                    <SidebarMenuItem key={item.title}>
                      <SidebarLink item={item} />
                    </SidebarMenuItem>
                  ))}
                </SidebarMenu>
              </SidebarGroup>
            </React.Fragment>
          ))}
          {/* Home is the workspace: the files, then the channels and the
              bots, Buzz's Channels and Direct messages.

              No Decibyl row. Home *is* Decibyl -- the pinned row above goes to
              /overview and this one went to /overview, so the assistant was
              announced twice on a panel we had just finished thinning out. The
              pinned row wins: it is the one that is there on every screen. */}
          {activeContext === "home" && (
            <>
              <SidebarGroup className="py-1">
                <SidebarMenu>
                  <SidebarMenuItem>
                    <SidebarMenuButton asChild isActive={pathname === "/files"}>
                      <Link href="/files">
                        <Database aria-hidden="true" className="h-4 w-4 shrink-0" />
                        <span className="truncate">Files</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                </SidebarMenu>
              </SidebarGroup>
              <SidebarChannels collapsed={isCollapsed} />
              <SidebarBots collapsed={isCollapsed} />
            </>
          )}
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
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-sidebar-border bg-white/60 text-xs font-medium text-sidebar-foreground">
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
            <DropdownMenuContent align="start" side="top" className="w-56">
              {/* The shop, the setup and the account, reached from the person
                  rather than from three rows of their own. Opening one swaps
                  the panel behind this menu, so every destination inside is
                  where it was. */}
              {PERSONAL_CONTEXTS.map((id) => {
                const context = NAV_CONTEXTS.find((candidate) => candidate.id === id)!;
                const Icon = context.icon;
                return (
                  <DropdownMenuItem
                    key={id}
                    onClick={() => {
                      setPickedContext(id);
                      setOpen(true);
                    }}
                    className="cursor-pointer"
                  >
                    <Icon className="mr-2 h-4 w-4" />
                    {context.title}
                  </DropdownMenuItem>
                );
              })}
              {provider === "stack" && (
                <DropdownMenuItem onClick={() => router.push("/handler/account-settings")} className="cursor-pointer">
                  <Settings className="mr-2 h-4 w-4" />
                  Account settings
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => router.push("/settings")} className="cursor-pointer">
                <Settings className="mr-2 h-4 w-4" />
                Settings
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => logout()} className="cursor-pointer">
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
