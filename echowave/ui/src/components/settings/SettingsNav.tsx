"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { type Feature, useFeature } from "@/lib/features";
import { cn } from "@/lib/utils";

import { activeSection, SETTINGS_GROUPS, SETTINGS_SECTIONS, type SettingsSection } from "./sections";

/** A section behind switches is listed only while one of them is on. The
 *  hooks run in a fixed order: every section asks for the same flags on
 *  every render. */
function useVisible(section: SettingsSection): boolean {
  const flags: Feature[] = section.flags ?? [];
  const first = useFeature(flags[0] ?? "free_mode");
  const second = useFeature(flags[1] ?? "free_mode");
  if (!flags.length) return true;
  return first || (flags.length > 1 && second);
}

function SectionItem({ section, current }: { section: SettingsSection; current: string | undefined }) {
  if (!useVisible(section)) return null;
  return (
    <li>
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
  );
}

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
                {SETTINGS_SECTIONS.filter((section) => section.group === group).map((section) => (
                  <SectionItem key={section.id} section={section} current={current} />
                ))}
              </ul>
            </div>
          ))}
        </div>
      </nav>
    </>
  );
}
