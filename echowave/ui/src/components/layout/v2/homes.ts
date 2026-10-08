import type { LucideIcon } from "lucide-react";
import { CalendarCheck, MessageCircle, Wand2 } from "lucide-react";

import type { Feature } from "@/lib/features";

/**
 * The homes of the v2 shell (KAN-208, UI-1), the app's only shell.
 *
 * Two, as the product handoff decides (section 19): Chat and Today. Decibyl
 * is one assistant, so the rail answers two questions -- "talk to it" and
 * "what needs my attention" -- and everything else is a secondary view:
 *
 * - Chat   -> /overview: Decibyl's home (composer, starters, history). The
 *             Recents list under the homes reopens any conversation, with
 *             Decibyl or with an agent.
 * - Today  -> /tasks, with its tabs: routines, contacts, what was handed
 *             over, and Activity (calls, missed calls, review, analytics,
 *             usage), which lights it too.
 * - Studio -> /studio, only with the `studio` flag.
 *
 * Agents and Settings are in the profile menu (AccountMenu). Agents are
 * helpers reached from Chat -- an @mention, or a recent conversation -- and
 * their list stays one click away for whoever manages them. Every page the
 * rail used to name still lights a home or a Settings section.
 */

export type HomeId = "chat" | "today" | "studio";

export type Home = {
  id: HomeId;
  title: string;
  url: string;
  icon: LucideIcon;
  /** Other path prefixes that light this home. */
  activePaths?: string[];
  /** Shown only while this feature is on for the workspace. */
  flag?: Feature;
  /** Pages one tap away on this home's tab strip -- reachable, so the
   *  navigation test counts them, without each needing a rail row. */
  reaches?: string[];
};

export const HOMES: readonly Home[] = [
  // Learning progress (/learning) opens from its conversation (screen 14).
  { id: "chat", title: "Chat", url: "/overview", icon: MessageCircle, activePaths: ["/workflow", "/channels", "/learning"] },
  {
    id: "today",
    title: "Today",
    url: "/tasks",
    icon: CalendarCheck,
    reaches: ["/schedules", "/contacts", "/deliverables", "/usage"],
    activePaths: [
      "/schedules",
      "/contacts",
      "/deliverables",
      "/usage",
      "/activity",
      "/campaigns",
      "/reports",
      "/review",
      "/analytics",
      "/missed-calls",
    ],
  },
  { id: "studio", title: "Studio", url: "/studio", icon: Wand2, flag: "studio" },
];

/** Where Settings and the agent list live now: the profile menu. Paths here
 *  light no home; the menu is how they are reached. */
export const PROFILE_LINKS = [
  { title: "Agents", url: "/workflow" },
  { title: "Settings", url: "/settings" },
] as const;

/** Customer copy for the rail, in one place. */
export const RAIL_COPY = {
  navLabel: "Homes",
  recents: "Recents",
  trialLabel: "Trial · invite-only",
  daysLeft: (n: number) => (n === 1 ? "1 day left" : `${n} days left`),
  trialEnded: "Trial ended",
  credits: "Credits",
  state: {
    live: "Live",
    needs_you: "Needs you",
    idle: "Idle",
  },
} as const;

function matches(pathname: string, path: string): boolean {
  const bare = path.split("#")[0];
  return pathname === bare || pathname.startsWith(`${bare}/`);
}

/**
 * The home a pathname belongs to: the longest matching prefix wins, so
 * /workflow/12/thread is still Agents, and /billing is Settings.
 */
export function activeHome(pathname: string): HomeId | undefined {
  let best: { id: HomeId; length: number } | undefined;
  for (const home of HOMES) {
    for (const path of [home.url, ...(home.activePaths ?? [])]) {
      const bare = path.split("#")[0];
      if (matches(pathname, bare) && (!best || bare.length > best.length)) {
        best = { id: home.id, length: bare.length };
      }
    }
  }
  return best?.id;
}

export type ColleagueState = "live" | "needs_you" | "idle";

/**
 * A colleague's dot, from what /team/status already says: `tone` "attention"
 * means something is waiting on a person (a failure or a pending yes), a live
 * agent is otherwise live, and everything else -- paused, draft, quiet -- is
 * idle.
 */
export function colleagueState(member: { is_live: boolean; tone: string }): ColleagueState {
  if (member.tone === "attention") return "needs_you";
  if (member.is_live) return "live";
  return "idle";
}
