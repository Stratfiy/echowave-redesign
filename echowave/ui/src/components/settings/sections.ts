/**
 * Settings, one place for everything that is set up once and then left
 * alone (October 2026 redesign). Each section is its own page under
 * /settings; the old addresses redirect (lib/redesignRedirects.ts).
 *
 * Kept as data so the nav, the rail and the tests read one list.
 */

import type { Feature } from "@/lib/features";

export type SettingsGroup = "You" | "Assistant" | "Identity" | "Advanced";

export type SettingsSection = {
  id: string;
  title: string;
  href: string;
  group: SettingsGroup;
  /** Where the section opens on a phone, when that differs (General: the
   *  phone's /settings is the list of sections itself). */
  mobileHref?: string;
  /** Other path prefixes that light this section (detail pages left where they were). */
  activePaths?: string[];
  /** Listed only while this switch is on (a section a launch stream is still
   *  proving). Off, the list is exactly what it was. */
  feature?: Feature;
};

export const SETTINGS_GROUPS: readonly SettingsGroup[] = ["You", "Assistant", "Identity", "Advanced"];

export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { id: "general", title: "General", href: "/settings", group: "You", mobileHref: "/settings/general", activePaths: ["/settings/general"] },
  { id: "team", title: "Team", href: "/settings/team", group: "You" },
  { id: "voice", title: "Voice and language", href: "/settings/voice", group: "You", feature: "voice_language_settings" },
  { id: "models", title: "Models", href: "/settings/models", group: "Assistant", activePaths: ["/integrations"] },
  { id: "knowledge", title: "Knowledge", href: "/settings/knowledge", group: "Assistant" },
  { id: "apps", title: "Apps and tools", href: "/settings/apps", group: "Assistant", activePaths: ["/tools", "/marketplace"] },
  { id: "channels", title: "Channels", href: "/settings/channels", group: "Assistant", activePaths: ["/channels"] },
  {
    id: "phone-number",
    title: "Phone numbers",
    href: "/settings/phone-number",
    group: "Identity",
    activePaths: ["/numbers", "/telephony-configurations", "/verified-numbers"],
  },
  { id: "company", title: "Company", href: "/settings/company", group: "Identity" },
  { id: "advanced", title: "Advanced", href: "/settings/advanced", group: "Advanced" },
  { id: "developer", title: "Developer", href: "/settings/developer", group: "Advanced", activePaths: ["/deploy"] },
  { id: "compliance", title: "Compliance", href: "/settings/compliance", group: "Advanced", activePaths: ["/do-not-call"] },
];

/** The sections a person sees: every one without a switch, and those whose
 *  switch is on. */
export function visibleSections(isOn: (feature: Feature) => boolean): SettingsSection[] {
  return SETTINGS_SECTIONS.filter((section) => !section.feature || isOn(section.feature));
}

function under(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** The section a pathname belongs to: the longest matching prefix wins, so
 *  /settings/team is Team and /settings is General. */
export function activeSection(pathname: string): string | undefined {
  let best: { id: string; length: number } | undefined;
  for (const section of SETTINGS_SECTIONS) {
    for (const path of [section.href, ...(section.activePaths ?? [])]) {
      if (under(pathname, path) && (!best || path.length > best.length)) {
        best = { id: section.id, length: path.length };
      }
    }
  }
  return best?.id;
}
