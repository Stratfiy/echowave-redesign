"use client";

import {
  AlertTriangle,
  ArrowUpCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Database,
  UserRound,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import posthog from "posthog-js";
import React, { useEffect, useRef, useState } from "react";

import { OrganizationSwitcher } from "@/components/layout/OrganizationSwitcher";
import { SidebarBots } from "@/components/layout/SidebarBots";
import { SidebarChannels } from "@/components/layout/SidebarChannels";
import { SidebarTeamSwitcher } from "@/components/layout/SidebarTeamSwitcher";
import { Button } from "@/components/ui/button";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
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
  type NavContextId,
  type SidebarNavItem,
} from "./navigation";

const TELEPHONY_WARNING_COPY = "Action required";

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { state, isMobile, setOpenMobile, setOpen } = useSidebar();
  const { provider } = useAuth();
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
  const versionInfo = config ? { ui: config.uiVersion, api: config.apiVersion } : null;

  // Check for updates only on self-hosted (OSS) deployments — cloud is managed for the user.
  const { latest: latestRelease, isBehind, isLatest } = useLatestReleaseVersion(
    versionInfo?.ui,
    { enabled: config?.deploymentMode === "oss" },
  );

  // Staff-ness and organization standing are server facts, read from the
  // server rather than inferred from anything the browser already holds. A
  // failure leaves both unprivileged: showing a staff link to a customer is
  // worse than making a reviewer reload.
  const roles = useAccessRoles();

  const navSections = getVisibleNavSections(roles);
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
  const activeSection = navSections.find(section => section.items.some(item => item.url === activeUrl))?.label;
  const [closedSections, setClosedSections] = useState<string[]>(["MONITOR", "DEVELOPERS", "WORKSPACE"]);
  useEffect(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem("decibyl.sidebar.closedSections") ?? "null");
      if (Array.isArray(saved) && saved.every(value => typeof value === "string")) setClosedSections(saved);
    } catch { /* Storage may be unavailable in private browsing. */ }
  }, []);
  useEffect(() => {
    if (activeSection) setClosedSections(current => current.filter(label => label !== activeSection));
  }, [pathname, activeSection]);
  const toggleSection = (label: string) => {
    const next = closedSections.includes(label)
      ? closedSections.filter(section => section !== label)
      : [...closedSections, label];
    setClosedSections(next);
    try { localStorage.setItem("decibyl.sidebar.closedSections", JSON.stringify(next)); } catch { /* Optional preference. */ }
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
            <p className="text-amber-600 dark:text-amber-400">{TELEPHONY_WARNING_COPY}</p>
          )}
        </div>
      ),
    };
    const warningIndicator = (
      <AlertTriangle
        aria-label="Action required on a telephony configuration"
        className={cn(
          "text-amber-500",
          isCollapsed ? "absolute -right-0.5 -top-0.5 h-3 w-3" : "ml-auto h-3.5 w-3.5"
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
          "rounded-md transition-colors hover:bg-accent hover:text-accent-foreground",
          isItemActive &&
            "bg-[var(--accent-brand-soft)] font-medium text-foreground hover:bg-[var(--accent-brand-soft)] hover:text-foreground"
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
              isItemActive ? "text-primary" : "text-muted-foreground"
            )}
          />
          <span
            className={cn("notranslate min-w-0 flex-1 truncate", isCollapsed && "sr-only")}
            translate="no"
          >
            {item.title}
          </span>
          {showWarningDot && (
            isCollapsed ? (
              warningIndicator
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  {warningIndicator}
                </TooltipTrigger>
                <TooltipContent side="right">
                  <p>{TELEPHONY_WARNING_COPY}</p>
                </TooltipContent>
              </Tooltip>
            )
          )}
        </Link>
      </SidebarMenuButton>
    );
  };

  // The only thing in the footer, and it used to read "Hire an Expert" —
  // an offer to sell an agency, permanently visible, on a product sold on not
  // needing one. Now it books a setup call instead: same help, no contradiction.
  // Expanded: a labelled pill filling the row. Collapsed: icon-only.
  const setupCallButton = isCollapsed ? (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          size="icon"
          className="h-7 w-7 rounded-full"
          asChild
          aria-label={SETUP_CALL_LABEL}
        >
          <a
            href={SETUP_CALL_URL}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() =>
              posthog.capture(PostHogEvent.HIRE_EXPERT_OPENED, { source: "sidebar" })
            }
          >
            <UserRound className="h-3.5 w-3.5" />
          </a>
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right">
        <p>{SETUP_CALL_LABEL}</p>
      </TooltipContent>
    </Tooltip>
  ) : (
    <Button size="sm" className="h-7 gap-1.5 rounded-full px-3 text-xs" asChild>
      <a
        href={SETUP_CALL_URL}
        target="_blank"
        rel="noopener noreferrer"
        onClick={() =>
          posthog.capture(PostHogEvent.HIRE_EXPERT_OPENED, { source: "sidebar" })
        }
      >
        <UserRound className="h-3.5 w-3.5" />
        {SETUP_CALL_LABEL}
      </a>
    </Button>
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
      <SidebarContent className="notranslate h-full flex-row gap-0 p-0" translate="no">
        {(
          <div
            role="tablist"
            aria-label="Workspace"
            /* Dark, because this is chrome rather than content.
             *
             * The five contexts were a light strip inside the panel, which made
             * them read as the panel's first group rather than as the frame
             * around it — and a frame the same colour as what it frames is not
             * doing the one job a frame has. Slack's rail is aubergine in light
             * mode for the same reason.
             *
             * It also puts the mark at the top of the rail rather than above
             * the panel, so the brand sits on the chrome and the workspace name
             * gets the panel to itself. */
            className={cn(
              "flex w-14 shrink-0 flex-col items-center gap-1 self-stretch bg-rail py-2 text-rail-foreground",
              // Collapsed it is the whole sidebar, so it rounds on both sides
              // rather than butting up against a panel that is not there.
              isCollapsed ? "w-full rounded-[inherit]" : "rounded-l-[inherit]",
            )}
          >
            <Link
              href="/"
              aria-label="Decibyl"
              className="mb-1 flex h-9 w-9 items-center justify-center rounded-lg bg-rail-accent text-rail-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rail-accent"
            >
              <span className="text-sm font-semibold leading-none">d</span>
            </Link>
            {NAV_CONTEXTS.map((context) => {
              const Icon = context.icon;
              const selected = context.id === activeContext;
              return (
                <button
                  key={context.id}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  aria-label={context.title}
                  title={context.title}
                  onClick={() => {
                    setPickedContext(context.id);
                    // Collapsed, the panel this names is not on screen. Picking
                    // a context you cannot then see is a click that does
                    // nothing, so opening is part of the same gesture.
                    if (isCollapsed) setOpen(true);
                    // Home is the one context whose panel has no link to its
                    // own landing -- the row was removed so the panel could
                    // be the workspace rather than a menu -- so the button
                    // itself goes there. The others stay browse-only: picking
                    // Setup to look at what is in it, without leaving the
                    // page you are reading, is a thing people do.
                    if (context.id === "home" && pathname !== "/overview") {
                      router.push("/overview");
                    }
                  }}
                  className={cn(
                    "flex w-11 flex-col items-center gap-0.5 rounded-md px-1 py-1.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rail-accent",
                    selected
                      ? "bg-rail-accent text-rail-accent-foreground"
                      // 80% rather than a muted token: muted-foreground is read
                      // against the light panel and disappears on the rail.
                      : "text-rail-foreground/70 hover:bg-white/10 hover:text-rail-foreground",
                  )}
                >
                  <Icon aria-hidden="true" className="h-[18px] w-[18px]" />
                  {/* The label, not just the icon. Slack ships icons+text by
                      default and offers icons-only as a setting; five unlabelled
                      glyphs is a memory test on a product somebody uses once a
                      week. */}
                  <span className="text-[10px] leading-none">{context.title}</span>
                </button>
              );
            })}
            {/* The rail's foot: fold the panel away, and -- folded -- the one
                door the panel foot would otherwise hold. Slack keeps its
                account control here too; this is the frame's bottom, not
                the panel's, so it stays put when the panel scrolls. */}
            <div className="mt-auto flex flex-col items-center gap-1 pt-2">
              {isCollapsed && setupCallButton}
              <SidebarTrigger
                className="h-9 w-9 rounded-md text-rail-foreground/70 hover:bg-white/10 hover:text-rail-foreground"
                aria-label={isCollapsed ? "Open the panel" : "Fold the panel away"}
              >
                {isCollapsed ? (
                  <ChevronRight className="h-4 w-4" />
                ) : (
                  <ChevronLeft className="h-4 w-4" />
                )}
              </SidebarTrigger>
            </div>
          </div>
        )}

        <div className={cn("flex min-w-0 flex-1 flex-col", isCollapsed && "hidden")}>
        {/* The workspace name heads the panel, the way Slack heads its
            sidebar with the workspace: it names everything below it, and the
            menu behind it is where you switch to another account or rename
            this one. */}
        <div className="border-b border-sidebar-border px-2 py-2">
          <OrganizationSwitcher collapsed={isCollapsed} />
          {provider === "stack" && (
            <div className="mt-2 notranslate" translate="no">
              <SidebarTeamSwitcher />
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1 overflow-y-auto px-1 py-1">
        {contextSections.map((section) => (
          <React.Fragment key={section.label ?? "overview"}>
          <SidebarGroup
            className="py-1"
          >
            {section.label && (
              <SidebarGroupLabel
                asChild
                className={cn(
                  "notranslate h-7 text-xs font-semibold uppercase tracking-wider text-muted-foreground",
                  isCollapsed && "hidden"
                )}
                translate="no"
              >
                <button
                  type="button"
                  aria-expanded={!closedSections.includes(section.label)}
                  aria-controls={`nav-${section.label}`}
                  onClick={() => toggleSection(section.label!)}
                  className="w-full justify-between hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {section.label}
                  <ChevronDown aria-hidden="true" className={cn("h-3 w-3 transition-transform", closedSections.includes(section.label) && "-rotate-90")} />
                </button>
              </SidebarGroupLabel>
            )}
            <SidebarMenu
              id={section.label ? `nav-${section.label}` : undefined}
              hidden={!isCollapsed && !!section.label && closedSections.includes(section.label)}
              className={cn(!isCollapsed && !!section.label && closedSections.includes(section.label) && "hidden")}
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
        {/* The Home panel. Channels first, then the bots: a channel is a
            place you work, a bot on its own is a thing you configure, and
            every workspace product leads with the places. Rendered outside
            the section loop because Home has no sections any more -- see
            contextSections. */}
        {!isCollapsed && activeContext === "home" && (
          <>
            <SidebarChannels collapsed={isCollapsed} />
            <SidebarBots collapsed={isCollapsed} />
          </>
        )}
        </div>
        {/* The panel's foot: what every bot draws on, as a row like the rows
            above it rather than a card of its own colour, and the one call
            to action. Opaque, so the list scrolls under it cleanly. */}
        <div className="border-t border-sidebar-border bg-sidebar px-2 py-2">
          {activeContext === "home" && (
            <Link
              href="/files"
              className="mb-1 flex h-8 items-center gap-2 rounded-md px-2 text-[15px] hover:bg-sidebar-accent"
            >
              <Database aria-hidden="true" className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="truncate">Company knowledge</span>
            </Link>
          )}
          <div className="[&>a]:w-full [&>button]:w-full">{setupCallButton}</div>
          <div className="mt-2 flex items-center gap-2 px-2">
              {isBehind && latestRelease && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <a
                      href="https://docs.decibyl.ai/deployment/update"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded-md border bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium leading-none text-amber-900 transition-opacity hover:opacity-80 dark:bg-amber-950 dark:text-amber-200"
                    >
                      <ArrowUpCircle className="h-3 w-3" />
                      Update
                    </a>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">
                    <p>Latest: {latestRelease} - click to see the update guide</p>
                  </TooltipContent>
                </Tooltip>
              )}
              {isLatest && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="inline-flex items-center rounded-md border bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium leading-none text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200">
                      Latest
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">
                    <p>You&apos;re running the latest release</p>
                  </TooltipContent>
                </Tooltip>
              )}
          </div>
        </div>
        </div>
      </SidebarContent>

      <SidebarRail />
    </Sidebar>
  );
}
