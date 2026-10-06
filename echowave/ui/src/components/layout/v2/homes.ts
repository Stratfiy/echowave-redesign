import type { LucideIcon } from "lucide-react";
import { Activity, Bot, CalendarClock, Home, Settings, Wand2 } from "lucide-react";

import type { Feature } from "@/lib/features";

/**
 * The homes of the v2 shell (KAN-208, UI-1), the app's only shell.
 *
 * Every home maps onto a page that already exists; none is a new route. The
 * rail's copy lives here, in one place, for copy review.
 *
 * Four homes and Settings: the rail answers "where is my work", and
 * everything that is set up once and then left alone (company, knowledge,
 * channels, team, apps, deploy, phone numbers, keys) is under Settings. Nine
 * homes, a channel list and a roster read as a control panel, not as an
 * assistant you hand jobs to.
 *
 * Mapping, and why:
 * - Home       -> /overview: Decibyl's home (greeting + composer).
 * - Tasks      -> /tasks, with its tabs: schedules, contacts, handed over.
 * - Agents     -> /workflow: the list of every agent, folders and archive;
 *                 channels light it too, as places agents talk.
 * - Studio     -> /studio, only with the `studio` flag: agents and a site
 *                 for them from one chat.
 * - Activity   -> /usage: calls, with campaigns, reports, review, analytics
 *                 and missed calls lighting it too.
 * - Settings   -> /settings, which also lights for every page it holds.
 */

export type HomeId = "home" | "tasks" | "agents" | "studio" | "activity" | "settings";

export type Home = {
  id: HomeId;
  title: string;
  url: string;
  icon: LucideIcon;
  /** Other path prefixes that light this home. */
  activePaths?: string[];
  /** Shown only while this feature is on for the workspace. */
  flag?: Feature;
};

export const HOMES: readonly Home[] = [
  { id: "home", title: "Home", url: "/overview", icon: Home },
  {
    id: "tasks",
    title: "Tasks",
    url: "/tasks",
    icon: CalendarClock,
    activePaths: ["/schedules", "/contacts", "/deliverables"],
  },
  { id: "agents", title: "Agents", url: "/workflow", icon: Bot, activePaths: ["/channels"] },
  { id: "studio", title: "Studio", url: "/studio", icon: Wand2, flag: "studio" },
  {
    id: "activity",
    title: "Activity",
    url: "/usage",
    icon: Activity,
    activePaths: ["/campaigns", "/reports", "/review", "/analytics", "/missed-calls", "/recordings"],
  },
  {
    id: "settings",
    title: "Settings",
    url: "/settings",
    icon: Settings,
    activePaths: [
      "/billing",
      "/tools",
      "/integrations",
      "/marketplace",
      "/deploy",
      "/do-not-call",
      "/recordings",
      "/telephony-configurations",
      "/numbers",
      "/verified-numbers",
    ],
  },
];

/** Customer copy for the rail, in one place. Agents are "colleagues" here. */
export const RAIL_COPY = {
  navLabel: "Homes",
  colleagues: "Colleagues",
  addColleague: "Add a colleague",
  moreColleagues: (n: number) => `${n} more`,
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
