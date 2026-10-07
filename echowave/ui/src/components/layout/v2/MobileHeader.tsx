"use client";

/**
 * The phone's header under `shell_mobile` (design: "Below 768 px use
 * Chat/Today bottom navigation and profile in header").
 *
 * The drawer (Recents, the workspace) behind a menu button on the left, the
 * page's home in the middle, and the profile -- Settings, Agents and every
 * advanced tool the person may use -- on the right. The top safe-area inset
 * is padded so nothing sits under a notch. Desktop keeps the TopBar.
 */

import { Menu } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useSidebar } from "@/components/ui/sidebar";

import { AccountMenu } from "./AppRailV2";
import { activeHome, HOMES } from "./homes";

export function MobileHeader() {
  const pathname = usePathname() ?? "";
  const { toggleSidebar, openMobile, setOpenMobile } = useSidebar();
  const home = HOMES.find((h) => h.id === activeHome(pathname));
  return (
    <header
      className="flex shrink-0 items-center gap-1 border-b border-border/70 bg-background px-1 pt-[env(safe-area-inset-top)] md:hidden"
      data-testid="mobile-header"
    >
      <button
        type="button"
        onClick={toggleSidebar}
        aria-expanded={openMobile}
        aria-label="Open navigation: recents and workspace"
        className="motion-m1 flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground hover:bg-accent"
      >
        <Menu className="h-5 w-5" aria-hidden="true" />
      </button>
      <Link href={home?.url ?? "/overview"} className="flex min-h-11 min-w-0 flex-1 items-center truncate px-1 text-[15px] font-semibold">
        {home?.title ?? "Decibyl"}
      </Link>
      <AccountMenu collapsed={false} onNavigate={() => setOpenMobile(false)} side="bottom" compact />
    </header>
  );
}

export default MobileHeader;
