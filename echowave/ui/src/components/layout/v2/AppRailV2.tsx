"use client";

import { ArrowUpCircle, Bot, ChevronLeft, ChevronRight, LifeBuoy, LogOut, Settings, UserRound, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import React from "react";

import { OrganizationSwitcher } from "@/components/layout/OrganizationSwitcher";
import { SidebarTeamSwitcher } from "@/components/layout/SidebarTeamSwitcher";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sidebar, SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { SETUP_CALL_LABEL, SETUP_CALL_URL } from "@/constants/setupCall";
import { useAppConfig } from "@/context/AppConfigContext";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { useLatestReleaseVersion } from "@/hooks/useLatestReleaseVersion";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { cn } from "@/lib/utils";

import { getVisibleNavSections, STAFF_SECTION, visibleShellManage } from "../navigation";
import { activeHome, HOMES, RAIL_COPY } from "./homes";
import { RecentsList } from "./RecentsList";
import { TrialBox } from "./TrialBox";
import { useRailData } from "./useRailData";

/**
 * The rail (KAN-208, UI-1), the app's only navigation: brand and workspace,
 * four homes, the colleague roster and the trial box. Everything set up once
 * and then left alone (company, knowledge, channels, team, apps, deploy,
 * billing, settings) is in the account menu at the foot, so the rail holds
 * only where the work is. It took over from the old sidebar, and with it the
 * old sidebar's extras: the Stack team switcher, the setup-call link and the
 * self-hosted update notice.
 *
 * Built on the same shadcn Sidebar as the old rail, so a phone gets the same
 * sheet and the same trigger in the top bar.
 *
 * TODO(KAN-208 follow-up): a "Projects" section goes under the roster once a
 * projects feature exists. There is none yet, so there is no section.
 */
export function AppRailV2() {
  const pathname = usePathname() ?? "";
  const { state, isMobile, setOpenMobile } = useSidebar();
  const collapsed = !isMobile && state === "collapsed";
  const { colleagues, trial, creditsPaise } = useRailData();
  const current = activeHome(pathname);
  const { provider } = useAuth();
  const studioOn = useFeature("studio");
  const freeMode = useFeature("free_mode");
  const homes = HOMES.filter((home) => !home.flag || (home.flag === "studio" && studioOn));
  const { config } = useAppConfig();
  // Self-hosted only: cloud is updated for the customer.
  const release = useLatestReleaseVersion(config?.uiVersion, { enabled: config?.deploymentMode === "oss" });
  const onNavigate = () => {
    if (isMobile) setOpenMobile(false);
  };

  const needsYou = colleagues.filter((c) => c.tone === "attention").length;
  // What needs a person is Today's question, so its count is there.
  const counts: Partial<Record<string, number>> = {
    today: needsYou > 0 ? needsYou : undefined,
  };

  return (
    <Sidebar collapsible="icon" variant="sidebar" className="app-sidebar v2-sidebar">
      <div className="shell-v2-scope v2-rail notranslate" translate="no" data-collapsed={collapsed || undefined}>
        <div className="v2-brand-row">
          <Link href="/overview" className="v2-brand" onClick={onNavigate} aria-label="Decibyl">
            <span className="v2-mark" aria-hidden="true">d</span>
            {!collapsed && <span>Decibyl</span>}
          </Link>
          {isMobile && (
            <button type="button" aria-label="Close navigation" onClick={() => setOpenMobile(false)} className="v2-icon-button">
              <X className="h-5 w-5" />
            </button>
          )}
        </div>
        {!collapsed && (
          <div className="v2-workspace">
            <OrganizationSwitcher collapsed={false} />
            {provider === "stack" && <SidebarTeamSwitcher />}
          </div>
        )}

        <nav aria-label={RAIL_COPY.navLabel} className="v2-homes">
          <ul>
            {homes.map((home) => {
              const active = current === home.id;
              const count = counts[home.id];
              const Icon = home.icon;
              return (
                <li key={home.id}>
                  <Link
                    href={home.url}
                    aria-current={active ? "page" : undefined}
                    onClick={onNavigate}
                    title={collapsed ? home.title : undefined}
                  >
                    <Icon className="h-[18px] w-[18px] shrink-0" strokeWidth={1.7} aria-hidden="true" />
                    <span className={cn(collapsed && "sr-only")}>{home.title}</span>
                    {!collapsed && count !== undefined && <span className="v2-count">{count}</span>}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        {!collapsed && (
          <div className="v2-scroll">
            <RecentsList pathname={pathname} onNavigate={onNavigate} />
            {/* TODO: Projects section, once a projects feature exists. */}
          </div>
        )}

        <div className="v2-foot">
          {!collapsed && release.isBehind && release.latest && (
            <a
              href="https://docs.decibyl.ai/deployment/update"
              target="_blank"
              rel="noopener noreferrer"
              className="v2-trial flex items-center gap-1.5"
              title={`Latest: ${release.latest}`}
            >
              <ArrowUpCircle className="h-3.5 w-3.5" aria-hidden="true" />
              Update available ({release.latest})
            </a>
          )}
          {/* Free while we are early: no trial, no credits to count. */}
          {!collapsed && !freeMode && <TrialBox trial={trial} creditsPaise={creditsPaise} />}
          <div className={cn("v2-foot-row", collapsed && "v2-foot-col")}>
            <AccountMenu collapsed={collapsed} onNavigate={onNavigate} />
            <a
              href={SETUP_CALL_URL}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Get help: ${SETUP_CALL_LABEL}`}
              title={SETUP_CALL_LABEL}
              className="v2-icon-button"
            >
              <LifeBuoy className="h-4 w-4" />
            </a>
            {!isMobile && (
              <SidebarTrigger className="v2-icon-button" aria-label={collapsed ? "Open the panel" : "Fold the panel away"}>
                {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
              </SidebarTrigger>
            )}
          </div>
        </div>
      </div>
    </Sidebar>
  );
}

/**
 * The person, and every page the homes do not name (company, knowledge,
 * channels, marketplace, apps & tools, deploy, billing, settings and team),
 * so no existing route is lost from the rail. Role filtering is the old
 * rail's own.
 */
export function AccountMenu({
  collapsed,
  onNavigate,
  side = "top",
  compact = false,
}: {
  collapsed: boolean;
  onNavigate: () => void;
  /** "bottom" when the menu opens from the phone header (shell_mobile). */
  side?: "top" | "bottom";
  /** Initials only, at a 44px target: the phone header's profile button. */
  compact?: boolean;
}) {
  const { user, logout } = useAuth();
  const roles = useAccessRoles();
  const identity =
    user?.displayName ||
    (user as { primaryEmail?: string } | undefined)?.primaryEmail ||
    (user as { email?: string } | undefined)?.email ||
    "";
  const initials = identity
    ? identity
        .split(/[\s@._-]+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((part) => part[0]?.toUpperCase() ?? "")
        .join("")
    : "?";
  const sections = getVisibleNavSections({
    isStaff: roles.isStaff,
    isOrganizationAdmin: roles.isOrganizationAdmin,
    isSuperadmin: roles.staffRole === "superadmin",
  });
  const help = useFeature("support_help");
  const supportInbox = useFeature("support_inbox");
  const freeMode = useFeature("free_mode");
  // Free while we are early: nothing to pay, so no Billing in the menu.
  const manage = visibleShellManage(sections).filter((entry) => !(freeMode && entry.url === "/billing"));
  const staffUrls = new Set(STAFF_SECTION.items.map((item) => item.url));
  const staff = sections.flatMap((section) => section.items).filter((item) => staffUrls.has(item.url));

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        {compact ? (
          <button
            type="button"
            aria-label="Profile and settings"
            className="motion-m1 flex h-11 w-11 items-center justify-center rounded-full"
            data-testid="header-profile"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
              {/* No name or email to take letters from: a person, not "?". */}
              {initials === "?" ? <UserRound aria-hidden className="h-4 w-4" /> : initials}
            </span>
          </button>
        ) : (
        <button type="button" aria-label="Account menu" className={cn("v2-account", collapsed && "v2-account-collapsed")}>
          <span className="v2-initials">{initials}</span>
          {!collapsed && <span className="v2-account-name">{user?.displayName || identity || "You"}</span>}
        </button>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align={compact ? "end" : "start"} side={side} className="max-h-[70vh] w-64 overflow-y-auto">
        {/* Settings and the agent list: off the rail, never out of reach. */}
        <DropdownMenuItem asChild>
          <Link href="/settings" onClick={onNavigate}>
            <Settings className="mr-2 h-4 w-4" />
            Settings
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/workflow" onClick={onNavigate}>
            <Bot className="mr-2 h-4 w-4" />
            Agents
          </Link>
        </DropdownMenuItem>
        {/* Help (screen 28): ask support and follow the answer. */}
        {help && (
          <DropdownMenuItem asChild>
            <Link href="/help" onClick={onNavigate}>
              <LifeBuoy className="mr-2 h-4 w-4" />
              Help
            </Link>
          </DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        {manage.map((entry) => (
          <React.Fragment key={entry.title}>
            {entry.url ? (
              <DropdownMenuItem asChild>
                <Link href={entry.url} onClick={onNavigate}>
                  <entry.icon className="mr-2 h-4 w-4" />
                  {entry.title}
                </Link>
              </DropdownMenuItem>
            ) : (
              <>
                <DropdownMenuLabel>{entry.title}</DropdownMenuLabel>
                {(entry.children ?? []).map((child) => (
                  <DropdownMenuItem key={child.url} asChild>
                    <Link href={child.url} onClick={onNavigate} className="pl-4">
                      {child.title}
                    </Link>
                  </DropdownMenuItem>
                ))}
              </>
            )}
          </React.Fragment>
        ))}
        {staff.length > 0 && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Staff</DropdownMenuLabel>
            {/* The support inbox (screen 32): support and superadmin alike. */}
            {supportInbox && (
              <DropdownMenuItem asChild>
                <Link href="/superadmin/support" onClick={onNavigate}>
                  <LifeBuoy className="mr-2 h-4 w-4" />
                  Support inbox
                </Link>
              </DropdownMenuItem>
            )}
            {staff.map((item) => (
              <DropdownMenuItem key={item.url} asChild>
                <Link href={item.url} onClick={onNavigate}>
                  <item.icon className="mr-2 h-4 w-4" />
                  {item.title}
                </Link>
              </DropdownMenuItem>
            ))}
          </>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={() => {
            onNavigate();
            logout();
          }}
          className="cursor-pointer"
        >
          <LogOut className="mr-2 h-4 w-4" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
