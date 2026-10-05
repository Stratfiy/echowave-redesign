"use client";

import { ChevronLeft, ChevronRight, LogOut, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import React from "react";

import { OrganizationSwitcher } from "@/components/layout/OrganizationSwitcher";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sidebar, SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

import { getVisibleNavSections, STAFF_SECTION, visibleShellManage } from "../navigation";
import { ColleagueRoster } from "./ColleagueRoster";
import { activeHome, HOMES, RAIL_COPY } from "./homes";
import { TrialBox } from "./TrialBox";
import { useRailData } from "./useRailData";

/**
 * The v2 rail (KAN-208, UI-1), shown in place of AppSidebar when
 * `ui_shell_v2` is on: brand and workspace, the eight homes, the colleague
 * roster and the trial box, per the founder-approved mock.
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
  const onNavigate = () => {
    if (isMobile) setOpenMobile(false);
  };

  const needsYou = colleagues.filter((c) => c.tone === "attention").length;
  const counts: Partial<Record<string, number>> = {
    home: needsYou > 0 ? needsYou : undefined,
    agents: colleagues.length > 0 ? colleagues.length : undefined,
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
          </div>
        )}

        <nav aria-label={RAIL_COPY.navLabel} className="v2-homes">
          <ul>
            {HOMES.map((home) => {
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
                    {collapsed && <Icon className="h-4 w-4" aria-hidden="true" />}
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
            <ColleagueRoster colleagues={colleagues} pathname={pathname} onNavigate={onNavigate} />
            {/* TODO: Projects section, once a projects feature exists. */}
          </div>
        )}

        <div className="v2-foot">
          {!collapsed && <TrialBox trial={trial} creditsPaise={creditsPaise} />}
          <div className={cn("v2-foot-row", collapsed && "v2-foot-col")}>
            <AccountMenu collapsed={collapsed} onNavigate={onNavigate} />
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
 * The person, and every manage page the eight homes do not name (billing,
 * apps & tools, deploy, compliance, marketplace), so no existing route is
 * lost from the rail. Role filtering is the old rail's own.
 */
function AccountMenu({ collapsed, onNavigate }: { collapsed: boolean; onNavigate: () => void }) {
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
  const manage = visibleShellManage(sections);
  const staffUrls = new Set(STAFF_SECTION.items.map((item) => item.url));
  const staff = sections.flatMap((section) => section.items).filter((item) => staffUrls.has(item.url));

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" aria-label="Account menu" className={cn("v2-account", collapsed && "v2-account-collapsed")}>
          <span className="v2-initials">{initials}</span>
          {!collapsed && <span className="v2-account-name">{user?.displayName || identity || "You"}</span>}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" className="max-h-[70vh] w-64 overflow-y-auto">
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
