/** The tabs of Home, shared by every screen that is one of them. */
import type { PageTab } from "@/components/layout/PageHeader";

export const HOME_TABS: PageTab[] = [
  { href: "/overview", label: "Messages" },
  // Tasks are the scheduled ones -- the routines that fire on their own.
  // The board where people and bots file work for each other is Requests:
  // it was called Tasks, and two things called tasks in one product is one
  // word doing two jobs and nobody sure which they are looking at.
  { href: "/tasks", label: "Tasks", prefix: true },
  { href: "/requests", label: "Requests", prefix: true },
  { href: "/overview/memory", label: "Memory" },
  { href: "/overview/about", label: "About" },
];
