/** The tabs of Home, shared by every screen that is one of them. */
import type { PageTab } from "@/components/layout/PageHeader";

export const HOME_TABS: PageTab[] = [
  { href: "/overview", label: "Messages" },
  { href: "/tasks", label: "Tasks", prefix: true },
  { href: "/overview/memory", label: "Memory" },
  { href: "/overview/about", label: "About" },
];
