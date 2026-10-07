"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAppConfig } from "@/context/AppConfigContext";
import { useOrgFeatures } from "@/context/OrgConfigContext";
import type { Feature } from "@/lib/features";
import { cn } from "@/lib/utils";

import { activeSection, SETTINGS_GROUPS, visibleSections } from "./sections";

/** True on the Settings root, where a phone shows the list of sections. */
export function isSettingsRoot(pathname: string): boolean {
  return pathname === "/settings" || pathname === "/settings/";
}

/**
 * The sections of Settings, grouped.
 *
 * Desktop: a list down the left with group headings, the way ChatGPT's
 * settings read. Phone: /settings is the grouped list itself, full screen,
 * and every section page starts with a way back to it -- no sideways row to
 * hunt through.
 */
export function SettingsNav() {
  const pathname = usePathname() ?? "";
  const current = activeSection(pathname);
  const root = isSettingsRoot(pathname);
  const { config } = useAppConfig();
  const orgFeatures = useOrgFeatures();
  const sections = visibleSections(
    (feature: Feature) => Boolean(config?.features?.[feature]) || Boolean(orgFeatures?.[feature]),
  );
  return (
    <>
      {!root && (
        <Link
          href="/settings"
          className="flex items-center gap-1 px-4 pt-4 text-sm text-muted-foreground md:hidden"
          data-testid="settings-back"
        >
          <ChevronLeft className="h-4 w-4" aria-hidden="true" /> Settings
        </Link>
      )}
      <nav
        aria-label="Settings sections"
        className={cn("shrink-0 md:block md:w-48", root ? "block w-full" : "hidden")}
      >
        <div className="px-4 pt-4 md:px-3 md:pt-6">
          {SETTINGS_GROUPS.map((group) => (
            <div key={group} className="mb-4 md:mb-3">
              <h2 className="px-2.5 pb-1 text-xs text-muted-foreground">{group}</h2>
              <ul className="divide-y divide-[var(--line)] rounded-2xl bg-[var(--paper-2)] md:divide-y-0 md:rounded-none md:bg-transparent">
                {sections.filter((section) => section.group === group).map((section) => (
                  <li key={section.id}>
                    {/* Phone link: full-width row to the section's own page. */}
                    <Link
                      href={section.mobileHref ?? section.href}
                      className="flex items-center justify-between px-4 py-3 text-[15px] md:hidden"
                    >
                      {section.title}
                      <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                    </Link>
                    <Link
                      href={section.href}
                      aria-current={current === section.id ? "page" : undefined}
                      className={cn(
                        "hidden whitespace-nowrap rounded-[10px] px-2.5 py-1.5 text-sm text-foreground transition-colors hover:bg-[var(--line)] md:block",
                        current === section.id && "bg-[var(--line)] font-medium",
                      )}
                    >
                      {section.title}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </nav>
    </>
  );
}
