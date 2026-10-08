"use client";

import { Menu } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useSidebar } from "@/components/ui/sidebar";
import { useFeature } from "@/lib/features";
import { useSoftKeyboardOpen } from "@/lib/shell/useSoftKeyboard";
import { cn } from "@/lib/utils";

import { activeHome, HOMES, PROFILE_LINKS } from "./homes";

/**
 * The phone's way round the app: Chat and Today a thumb away at the bottom,
 * and Menu for the drawer (Recents, the workspace, Settings, Agents).
 *
 * On a phone the rail is a sheet, so before this the only way from Chat to
 * Today was the drawer behind a button in the top corner -- two taps and a
 * reach, for the two places the app is about.
 */
export function MobileTabBar() {
  const pathname = usePathname() ?? "";
  const { toggleSidebar, openMobile } = useSidebar();
  const studioOn = useFeature("studio");
  // shell_mobile (design "Layout and responsive behavior"): Chat and Today
  // only, with the drawer and the profile in the header (TopBar), 44px
  // targets, and the bar out of the way while the keyboard is up so it
  // never sits between the person and Send.
  const shellMobile = useFeature("shell_mobile");
  const keyboardOpen = useSoftKeyboardOpen();
  // The agent list and Settings are the profile menu's, not a home's: an
  // agent's own chat still lights Chat, the list of them lights nothing.
  const inProfileMenu = PROFILE_LINKS.some(
    (link) => pathname === link.url || (link.url === "/settings" && pathname.startsWith("/settings/"))
  );
  const current = inProfileMenu ? undefined : activeHome(pathname);
  const homes = HOMES.filter((home) => !home.flag || (home.flag === "studio" && studioOn));

  const item = cn(
    "flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 py-1.5 text-[11px] font-medium transition-colors",
    shellMobile && "motion-m1 min-h-11",
  );

  if (shellMobile && keyboardOpen) return null;

  return (
    <nav
      aria-label="Main"
      data-testid="mobile-tab-bar"
      className="flex shrink-0 items-stretch border-t border-border/70 bg-background pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      {homes.map((home) => {
        const active = current === home.id && !openMobile;
        const Icon = home.icon;
        return (
          <Link
            key={home.id}
            href={home.url}
            aria-current={active ? "page" : undefined}
            className={cn(item, active ? "text-foreground" : "text-muted-foreground")}
          >
            <Icon className="h-5 w-5" strokeWidth={active ? 2 : 1.7} aria-hidden="true" />
            {home.title}
          </Link>
        );
      })}
      {!shellMobile && (
      <button
        type="button"
        onClick={toggleSidebar}
        aria-expanded={openMobile}
        className={cn(item, openMobile ? "text-foreground" : "text-muted-foreground")}
      >
        <Menu className="h-5 w-5" strokeWidth={1.7} aria-hidden="true" />
        Menu
      </button>
      )}
    </nav>
  );
}
