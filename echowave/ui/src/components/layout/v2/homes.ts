import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Bot,
  Building2,
  CalendarClock,
  Database,
  Home,
  Settings,
  Users,
} from "lucide-react";

/**
 * The eight homes of the v2 shell (KAN-208, UI-1), behind `ui_shell_v2`.
 *
 * Every home maps onto a page that already exists; none is a new route. The
 * rail's copy lives here, in one place, for copy review.
 *
 * Mapping, and why:
 * - My Decibyl -> /overview: the current Decibyl home (greeting + composer).
 * - Company    -> /company: the agents as a company (Paperclip-style): org
 *                 chart, what needs you, spend, heartbeats and activity.
 * - Tasks      -> /tasks.
 * - Agents     -> /workflow: the list of every agent, folders and archive.
 * - Knowledge  -> /files.
 * - Activity   -> /usage: calls, with campaigns, reports, review, analytics
 *                 and missed calls lighting it too.
 * - Team       -> /settings#team: the people live in a card on Settings;
 *                 there is no page of their own yet.
 * - Settings   -> /settings, which also lights for the manage pages
 *                 (billing, apps & tools, deploy, compliance, marketplace).
 */

export type HomeId =
  | "home"
  | "company"
  | "tasks"
  | "agents"
  | "knowledge"
  | "activity"
  | "team"
  | "settings";

export type Home = {
  id: HomeId;
  title: string;
  url: string;
  icon: LucideIcon;
  /** Other path prefixes that light this home. */
  activePaths?: string[];
};

export const HOMES: readonly Home[] = [
  { id: "home", title: "My Decibyl", url: "/overview", icon: Home },
  { id: "company", title: "Company", url: "/company", icon: Building2 },
  { id: "tasks", title: "Tasks", url: "/tasks", icon: CalendarClock },
  { id: "agents", title: "Agents", url: "/workflow", icon: Bot, activePaths: ["/channels"] },
  { id: "knowledge", title: "Knowledge", url: "/files", icon: Database },
  {
    id: "activity",
    title: "Activity",
    url: "/usage",
    icon: Activity,
    activePaths: ["/campaigns", "/reports", "/review", "/analytics", "/missed-calls", "/recordings"],
  },
  { id: "team", title: "Team", url: "/settings#team", icon: Users },
  {
    id: "settings",
    title: "Settings",
    url: "/settings",
    icon: Settings,
    activePaths: [
      "/billing",
      "/tools",
      "/marketplace",
      "/privacy",
      "/api-keys",
      "/deploy",
      "/telephony-configurations",
      "/model-configurations",
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
 * /workflow/12/thread is still Agents and /settings is Settings, not Team
 * (Team is a card on Settings and never lights by path alone).
 */
export function activeHome(pathname: string): HomeId | undefined {
  let best: { id: HomeId; length: number } | undefined;
  for (const home of HOMES) {
    if (home.id === "team") continue;
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
