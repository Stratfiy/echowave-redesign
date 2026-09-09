"use client";

import {
  AlertTriangle,
  ArrowUpCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  UserRound,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import posthog from "posthog-js";
import React, { useEffect, useRef, useState } from "react";

import { BrandLogo } from "@/components/BrandLogo";
import { SidebarTeamSwitcher } from "@/components/layout/SidebarTeamSwitcher";
import { Button } from "@/components/ui/button";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
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
  getActiveNavUrl,
  getVisibleNavSections,
  type SidebarNavItem,
} from "./navigation";

const TELEPHONY_WARNING_COPY = "Action required";

export function AppSidebar() {
  const pathname = usePathname();
  const { state, isMobile, setOpenMobile } = useSidebar();
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

  // Version info from app config context
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
    <Sidebar collapsible="icon" variant="floating" className="app-sidebar-dock">
      <SidebarHeader className="px-2 py-3 notranslate" translate="no">
        <div className="flex items-center justify-between">
          <div className={cn("flex items-center gap-2", isCollapsed && "hidden")}>
            <Link
              href="/"
              className="notranslate flex items-center gap-2 px-1"
              translate="no"
            >
              <BrandLogo mark className="h-6" />
              {versionInfo && (
                <span
                  className="notranslate text-xs font-normal text-muted-foreground"
                  translate="no"
                >
                  v{versionInfo.ui}
                </span>
              )}
            </Link>
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

          <SidebarTrigger className={cn("hover:bg-accent", isCollapsed && "mx-auto")}>
            {isCollapsed ? (
              <ChevronRight className="h-4 w-4" />
            ) : (
              <ChevronLeft className="h-4 w-4" />
            )}
          </SidebarTrigger>
        </div>

        {provider === "stack" && (
          <div className={cn("mt-3 notranslate", isCollapsed && "hidden")} translate="no">
            <SidebarTeamSwitcher />
          </div>
        )}
      </SidebarHeader>

      {/* pb-2 plus an opaque footer below: the nav list is taller than a 900px
          viewport once MANAGE has six entries, so the last item scrolls under
          the footer. Without a background on the footer it showed through and
          the final entry read as clipped rather than as scrolled. */}
      <SidebarContent className={cn("notranslate gap-1 pb-2 group-data-[collapsible=icon]:overflow-y-auto", isCollapsed && "px-0")} translate="no">
        {navSections.map((section) => (
          <SidebarGroup
            key={section.label ?? "overview"}
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
        ))}
      </SidebarContent>

      <SidebarFooter
        className={cn("bg-sidebar p-3 notranslate", isCollapsed && "p-2")}
        translate="no"
      >
        <div className={cn("flex", isCollapsed ? "justify-center" : "justify-stretch [&>button]:w-full")}>
          {setupCallButton}
        </div>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
