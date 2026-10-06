/**
 * Settings, one place for everything that is set up once and then left
 * alone (October 2026 redesign). Each section is its own page under
 * /settings; the old addresses redirect (lib/redesignRedirects.ts).
 *
 * Kept as data so the nav, the rail and the tests read one list.
 */

export type SettingsSection = {
  id: string;
  title: string;
  href: string;
  /** Other path prefixes that light this section (detail pages left where they were). */
  activePaths?: string[];
};

export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { id: "general", title: "General", href: "/settings" },
  { id: "team", title: "Team", href: "/settings/team" },
  {
    id: "phone-number",
    title: "Phone numbers",
    href: "/settings/phone-number",
    activePaths: ["/numbers", "/telephony-configurations", "/verified-numbers"],
  },
  { id: "api-keys", title: "API keys", href: "/settings/api-keys", activePaths: ["/integrations"] },
  { id: "apps", title: "Apps and tools", href: "/settings/apps", activePaths: ["/tools", "/marketplace"] },
  { id: "knowledge", title: "Knowledge", href: "/settings/knowledge" },
  { id: "channels", title: "Channels", href: "/settings/channels", activePaths: ["/channels"] },
  { id: "company", title: "Company", href: "/settings/company" },
  { id: "developer", title: "Developer", href: "/settings/developer", activePaths: ["/deploy"] },
  { id: "compliance", title: "Compliance", href: "/settings/compliance", activePaths: ["/do-not-call"] },
];

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
