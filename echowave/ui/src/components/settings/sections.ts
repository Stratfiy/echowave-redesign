/**
 * Settings, one place for everything that is set up once and then left
 * alone (October 2026 redesign). Each section is its own page under
 * /settings; the old addresses redirect (lib/redesignRedirects.ts).
 *
 * Kept as data so the nav, the rail and the tests read one list.
 */

import type { Feature } from "@/lib/features";

export type SettingsGroup = "You" | "Assistant" | "Identity" | "Advanced";

export type SettingsSection<G extends string = SettingsGroup> = {
  id: string;
  title: string;
  href: string;
  group: G;
  /** Where the section opens on a phone, when that differs (General: the
   *  phone's /settings is the list of sections itself). */
  mobileHref?: string;
  /** Other path prefixes that light this section (detail pages left where they were). */
  activePaths?: string[];
  /** Shown only while one of these switches is on (launch streams). */
  flags?: Feature[];
};

export const SETTINGS_GROUPS: readonly SettingsGroup[] = ["You", "Assistant", "Identity", "Advanced"];

export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { id: "general", title: "General", href: "/settings", group: "You", mobileHref: "/settings/general", activePaths: ["/settings/general"] },
  { id: "team", title: "Team", href: "/settings/team", group: "You" },
  // Screen 20 (stream today): the person's own daily brief.
  { id: "daily-brief", title: "Daily brief", href: "/settings/daily-brief", group: "You", flags: ["daily_brief"] },
  { id: "notifications", title: "Notifications", href: "/settings/notifications", group: "You", flags: ["identity_notifications"] },
  { id: "models", title: "Models", href: "/settings/models", group: "Assistant", activePaths: ["/integrations"] },
  { id: "knowledge", title: "Files", href: "/settings/knowledge", group: "Assistant" },
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
  { id: "connections", title: "Connections", href: "/settings/connections", group: "Identity", flags: ["identity_connections"] },
  {
    id: "identity",
    title: "Decibyl identity",
    href: "/settings/identity",
    group: "Identity",
    flags: ["identity_email", "identity_phone"],
  },
  { id: "advanced", title: "Advanced", href: "/settings/advanced", group: "Advanced" },
  { id: "developer", title: "Developer", href: "/settings/developer", group: "Advanced", activePaths: ["/deploy"] },
  { id: "compliance", title: "Compliance", href: "/settings/compliance", group: "Advanced", activePaths: ["/do-not-call"] },
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

// ---------------------------------------------------------------------------
// The Settings shell (screen 17; launch stream `settings`, SETTINGS.md).
// Behind `settings_shell`: off, the list above is Settings exactly as it was.
// ---------------------------------------------------------------------------


/** Personal, Connections, Privacy and Advanced; then the workspace's own
 *  sections under a heading that carries the workspace's name. */
export type ShellGroup = "Personal" | "Connections" | "Privacy" | "Advanced" | "Workspace";

export const SHELL_GROUPS: readonly ShellGroup[] = ["Personal", "Connections", "Privacy", "Advanced", "Workspace"];

export type ShellSection = SettingsSection<ShellGroup> & {
  /** One line under the title in search results and on the phone list. */
  blurb: string;
  /** Shown only while this switch is on. */
  feature?: Feature;
  /** Shown only to the workspace's admins; the server enforces it anyway. */
  adminOnly?: boolean;
};

export const SHELL_SECTIONS: readonly ShellSection[] = [
  { id: "account", title: "Account", href: "/settings", mobileHref: "/settings/account", activePaths: ["/settings/account"], group: "Personal", blurb: "Name, email, timezone, appearance and today's allowances." },
  { id: "personalization", title: "Personalization", href: "/settings/personalization", group: "Personal", blurb: "Reply language, answer length and your instructions." },
  { id: "voice", title: "Voice and language", href: "/settings/voice", group: "Personal", blurb: "Spoken language, voice, speed, captions and microphone." },
  { id: "memory", title: "Memory", href: "/settings/memory", group: "Personal", blurb: "What Decibyl remembers, where it came from, and sharing.", feature: "memory_manager" },
  { id: "saved", title: "Saved items", href: "/settings/saved", group: "Personal", blurb: "Replies and notes you kept, and search.", feature: "saved_items" },
  { id: "notifications", title: "Notifications", href: "/settings/notifications", group: "Personal", blurb: "How and when Decibyl lets you know.", flags: ["identity_notifications"] },
  { id: "apps", title: "Apps and tools", href: "/settings/apps", group: "Connections", blurb: "Mail, calendar and the apps Decibyl may read or act in.", activePaths: ["/tools", "/marketplace"] },
  { id: "channels", title: "Channels", href: "/settings/channels", group: "Connections", blurb: "WhatsApp, Telegram, Slack and Teams, to message Decibyl.", activePaths: ["/channels"] },
  { id: "connections", title: "Connections", href: "/settings/connections", group: "Connections", blurb: "What each app and channel may do for you, and revoking it.", flags: ["identity_connections"] },
  { id: "identity", title: "Decibyl identity", href: "/settings/identity", group: "Connections", blurb: "Your Decibyl email address and phone verification.", flags: ["identity_email", "identity_phone"] },
  {
    id: "phone-number",
    title: "Phone numbers",
    href: "/settings/phone-number",
    group: "Connections",
    blurb: "Numbers, verification and who answers.",
    activePaths: ["/numbers", "/telephony-configurations", "/verified-numbers"],
  },
  { id: "privacy", title: "Privacy and security", href: "/settings/privacy", group: "Privacy", blurb: "Two-step sign-in, your data, export and deletion, retention.", feature: "privacy_center" },
  { id: "models", title: "Models", href: "/settings/models", group: "Advanced", blurb: "The brain, hearing and voice every agent inherits.", activePaths: ["/integrations"] },
  { id: "skills", title: "Skills", href: "/settings/skills", group: "Advanced", blurb: "Skills your agents can use, with versions and access.", activePaths: ["/marketplace/skills"] },
  { id: "developer", title: "Developer", href: "/settings/developer", group: "Advanced", blurb: "API keys, webhooks and delivery logs.", activePaths: ["/deploy"], adminOnly: true },
  { id: "advanced", title: "Tools and tracing", href: "/settings/advanced", group: "Advanced", blurb: "Tool credentials, MCP access and call tracing.", adminOnly: true },
  { id: "workspace", title: "Workspace defaults", href: "/settings/workspace", mobileHref: "/settings/workspace", activePaths: ["/settings/general"], group: "Workspace", blurb: "The team's timezone, test number, approvals and apps." },
  { id: "team", title: "Team", href: "/settings/team", group: "Workspace", blurb: "Who is in the workspace, and their roles." },
  { id: "company", title: "Company", href: "/settings/company", group: "Workspace", blurb: "Business details, GST and invoices." },
  { id: "knowledge", title: "Files", href: "/settings/knowledge", group: "Workspace", blurb: "Files your agents answer from." },
  { id: "compliance", title: "Compliance", href: "/settings/compliance", group: "Workspace", blurb: "Do-not-call, retention, consent and the workspace's data.", activePaths: ["/do-not-call"] },
];

/** Everyday words people type, mapped to the place that answers them
 *  (screen 17: "Settings search uses everyday synonyms, including mic,
 *  memory and email"). The section's own title always matches too. */
export const SETTINGS_SEARCH: readonly { label: string; section: string; href: string; words: readonly string[] }[] = [
  { label: "Your name", section: "account", href: "/settings#name", words: ["name", "call me", "nickname", "profile"] },
  { label: "Sign-in email", section: "account", href: "/settings#email", words: ["email", "e-mail", "mail address", "login", "sign in"] },
  { label: "Timezone", section: "account", href: "/settings#timezone", words: ["timezone", "time zone", "clock", "ist", "local time"] },
  { label: "Light or dark", section: "account", href: "/settings#appearance", words: ["dark mode", "light mode", "theme", "appearance", "colours", "colors"] },
  { label: "Simple mode", section: "account", href: "/settings#simple-mode", words: ["simple mode", "large text", "bigger text", "easy mode", "elderly", "parents"] },
  { label: "Notifications", section: "notifications", href: "/settings/notifications", words: ["notifications", "alerts", "push", "quiet hours", "do not disturb", "reminders"] },
  { label: "What apps may do", section: "connections", href: "/settings/connections", words: ["revoke", "permissions", "access", "disconnect", "consent"] },
  { label: "Your Decibyl email and phone", section: "identity", href: "/settings/identity", words: ["my email address", "decibyl email", "alias", "my number", "phone verification"] },
  { label: "Today's allowances", section: "account", href: "/settings#allowances", words: ["limit", "allowance", "quota", "usage", "how many left"] },
  { label: "Reply language", section: "personalization", href: "/settings/personalization#language", words: ["language", "hindi", "tamil", "telugu", "kannada", "bengali", "marathi", "reply language", "bhasha"] },
  { label: "Answer length", section: "personalization", href: "/settings/personalization#length", words: ["short answers", "long answers", "length", "brief", "detailed", "verbose"] },
  { label: "Your instructions", section: "personalization", href: "/settings/personalization#instructions", words: ["instructions", "custom instructions", "tone", "style", "how to reply", "prompt"] },
  { label: "Microphone", section: "voice", href: "/settings/voice#microphone", words: ["mic", "microphone", "mike", "audio input", "can't hear me", "permission"] },
  { label: "Voice", section: "voice", href: "/settings/voice#voice", words: ["voice", "speaker", "female voice", "male voice", "accent", "sound"] },
  { label: "Speaking speed", section: "voice", href: "/settings/voice#speed", words: ["speed", "slow down", "faster", "pace", "rate"] },
  { label: "Captions", section: "voice", href: "/settings/voice#captions", words: ["captions", "subtitles", "transcript while talking"] },
  { label: "Memory", section: "memory", href: "/settings/memory", words: ["memory", "remember", "forget", "what you know about me", "facts", "learned"] },
  { label: "Temporary conversation", section: "memory", href: "/settings/memory#temporary", words: ["temporary", "incognito", "private chat", "don't remember", "off the record"] },
  { label: "Share a memory with the team", section: "memory", href: "/settings/memory", words: ["share", "team memory", "tell the team"] },
  { label: "Saved items", section: "saved", href: "/settings/saved", words: ["saved", "bookmark", "kept", "pinned", "favourites", "favorites", "search"] },
  { label: "Two-step sign-in", section: "privacy", href: "/settings/privacy#security", words: ["mfa", "2fa", "two factor", "two-step", "authenticator", "otp", "password", "security"] },
  { label: "Download my data", section: "privacy", href: "/settings/privacy#export", words: ["export", "download my data", "copy of my data", "backup"] },
  { label: "Delete my data", section: "privacy", href: "/settings/privacy#delete", words: ["delete account", "delete my data", "erase", "remove me", "close account"] },
  { label: "How long things are kept", section: "privacy", href: "/settings/privacy#retention", words: ["retention", "how long", "kept for", "recordings", "history"] },
  { label: "Mail and calendar", section: "apps", href: "/settings/apps", words: ["gmail", "outlook", "calendar", "google", "connect app", "integration", "zoho"] },
  { label: "WhatsApp and chat apps", section: "channels", href: "/settings/channels", words: ["whatsapp", "telegram", "slack", "teams", "message decibyl"] },
  { label: "Phone numbers", section: "phone-number", href: "/settings/phone-number", words: ["phone", "number", "kyc", "verification", "calls", "sim"] },
  { label: "Models", section: "models", href: "/settings/models", words: ["model", "brain", "ai", "claude", "sarvam", "speech to text", "text to speech", "stt", "tts", "api key"] },
  { label: "Skills", section: "skills", href: "/settings/skills", words: ["skills", "abilities", "install skill"] },
  { label: "API keys and webhooks", section: "developer", href: "/settings/developer", words: ["api", "api key", "webhook", "developer", "token"] },
  { label: "Tool credentials and tracing", section: "advanced", href: "/settings/advanced", words: ["credentials", "mcp", "langfuse", "tracing", "secrets"] },
  { label: "Team members", section: "team", href: "/settings/team", words: ["team", "invite", "members", "colleagues", "roles", "admin"] },
  { label: "Company details", section: "company", href: "/settings/company", words: ["company", "business", "gst", "gstin", "invoice", "address", "pan"] },
  { label: "Documents", section: "knowledge", href: "/settings/knowledge", words: ["documents", "files", "pdf", "knowledge", "upload", "faq"] },
  { label: "Do not call", section: "compliance", href: "/settings/compliance", words: ["dnd", "do not call", "consent", "trai", "dpdp", "compliance"] },
  { label: "Workspace timezone and test number", section: "workspace", href: "/settings/workspace", words: ["workspace timezone", "test number", "team timezone", "approvals", "who approves"] },
];

/** The sections a person sees: switched on, and admin-only ones for admins. */
export function visibleShellSections(isOn: (feature: Feature) => boolean, isAdmin: boolean): ShellSection[] {
  return SHELL_SECTIONS.filter(
    (s) =>
      (!s.feature || isOn(s.feature)) &&
      (!s.flags?.length || s.flags.some((flag) => isOn(flag))) &&
      (!s.adminOnly || isAdmin),
  );
}

/** Every switch the shell's list depends on, in a fixed order (the nav asks
 *  for each one on every render). */
export const SHELL_FLAGS: readonly Feature[] = Array.from(
  new Set(SHELL_SECTIONS.flatMap((s) => [...(s.feature ? [s.feature] : []), ...(s.flags ?? [])])),
);

export type SettingsSearchHit = { label: string; section: ShellSection; href: string; matched: string };

function norm(text: string): string {
  return text.toLowerCase().replace(/&/g, " and ").normalize("NFKD").replace(/[^\p{L}\p{M}\p{N}\s]/gu, " ").replace(/\s+/g, " ").trim();
}

/** Settings search: titles, blurbs and everyday words, in the sections this
 *  person can see. A word that names a hidden section finds nothing rather
 *  than a page they cannot open. */
export function searchSettings(query: string, sections: readonly ShellSection[]): SettingsSearchHit[] {
  const q = norm(query);
  if (!q) return [];
  const byId = new Map(sections.map((s) => [s.id, s]));
  const hits: (SettingsSearchHit & { score: number })[] = [];
  const seen = new Set<string>();
  const add = (hit: SettingsSearchHit, score: number) => {
    if (seen.has(hit.href)) return;
    seen.add(hit.href);
    hits.push({ ...hit, score });
  };
  for (const section of sections) {
    const title = norm(section.title);
    if (title.startsWith(q)) add({ label: section.title, section, href: section.href, matched: section.title }, 0);
    else if (title.includes(q)) add({ label: section.title, section, href: section.href, matched: section.title }, 1);
  }
  for (const entry of SETTINGS_SEARCH) {
    const section = byId.get(entry.section);
    if (!section) continue;
    const word = entry.words.find((w) => norm(w) === q) ?? entry.words.find((w) => norm(w).startsWith(q) || q.startsWith(norm(w)));
    if (word) add({ label: entry.label, section, href: entry.href, matched: word }, norm(word) === q ? 0 : 2);
    else if (norm(entry.label).includes(q)) add({ label: entry.label, section, href: entry.href, matched: entry.label }, 2);
  }
  for (const section of sections) {
    if (norm(section.blurb).includes(q)) add({ label: section.title, section, href: section.href, matched: section.blurb }, 3);
  }
  return hits.sort((a, b) => a.score - b.score).map((hit) => ({ label: hit.label, section: hit.section, href: hit.href, matched: hit.matched }));
}

/** The shell section a pathname belongs to: the longest matching prefix. */
export function activeShellSection(pathname: string, sections: readonly ShellSection[] = SHELL_SECTIONS): string | undefined {
  let best: { id: string; length: number } | undefined;
  for (const section of sections) {
    for (const path of [section.href, ...(section.activePaths ?? [])]) {
      if (under(pathname, path) && (!best || path.length > best.length)) {
        best = { id: section.id, length: path.length };
      }
    }
  }
  return best?.id;
}
