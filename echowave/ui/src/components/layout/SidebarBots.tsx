"use client";

/**
 * The bots, in the rail, like a channel list.
 *
 * This is the change that makes the shell read as a workspace rather than a
 * control panel. A rail of seventeen feature doors says "configure something";
 * a rail whose largest section is the names of your bots, each with a dot
 * saying how it is doing, says "here is your team, pick one".
 *
 * It reads `/team/status` — the same endpoint the home screen's team panel and
 * the bot list use, already sorted worst-first, so the bot that has stopped
 * filing bookings is the one at the top of the rail rather than the one you
 * scroll to find.
 *
 * Deliberately a shortcut and not the source of truth: `/workflow` still lists
 * every bot, folders and archive included. That is why this renders nothing at
 * all when the roster is empty or the request fails — an empty "YOUR BOTS"
 * heading on a fresh account is noise, and the door to the full list is two
 * rows above it either way.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { teamStatusApiV1TeamStatusGet } from "@/client/sdk.gen";
import type { TeamMember } from "@/client/types.gen";
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

/** Same four tones as the home screen and the bot list. One vocabulary. */
const TONE_DOT: Record<string, string> = {
  attention: "bg-destructive",
  working: "bg-emerald-500",
  idle: "bg-muted-foreground/40",
  paused: "bg-amber-500",
};

/**
 * How many bots the rail will hold before it stops.
 *
 * The list is sorted worst-first, so a cap keeps the ones that need attention
 * and drops the quiet ones — the right end to lose. An account with forty bots
 * would otherwise push Billing and Settings off a laptop screen.
 */
export const RAIL_LIMIT = 8;

export function SidebarBots({ collapsed }: { collapsed: boolean }) {
  const pathname = usePathname();
  const { user, loading: authLoading } = useAuth();
  const [bots, setBots] = useState<TeamMember[]>([]);
  const started = useRef(false);

  useEffect(() => {
    // Wait for auth. The interceptor that attaches the token is registered
    // only once auth has loaded; a request sent before that is unauthenticated,
    // fails quietly, and this renders nothing -- which is exactly what the
    // deployed rail did on the loads where the fetch beat the interceptor.
    // The list was there one deploy and gone the next, and nothing was
    // broken but the order of two things. See ui/AGENTS.md.
    if (authLoading || !user || started.current) return;
    started.current = true;
    let cancelled = false;
    (async () => {
      try {
        const response = await teamStatusApiV1TeamStatusGet({ query: { hours: 24 } });
        if (!cancelled) setBots(response.data?.members ?? []);
      } catch {
        // The rail is a shortcut; /workflow is the list. Failing quietly here
        // costs a convenience, not a destination.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authLoading, user]);

  // Icon mode has no room for names, and a column of bare dots is a puzzle.
  if (collapsed || bots.length === 0) return null;

  const shown = bots.slice(0, RAIL_LIMIT);

  return (
    <SidebarGroup className="py-1">
      <SidebarGroupLabel className="h-7 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Your bots
      </SidebarGroupLabel>
      <SidebarMenu>
        {shown.map((bot) => {
          const href = `/workflow/${bot.workflow_id}`;
          return (
            <SidebarMenuItem key={bot.workflow_id}>
              <SidebarMenuButton asChild isActive={pathname === href} tooltip={bot.status}>
                {/* `title` as well as the status tooltip: a name long enough
                    to truncate is exactly the name somebody needs to read in
                    full, and the tooltip slot is already spent on what the bot
                    is doing. The browser's own is free and does not fight it. */}
                <Link href={href} title={bot.name}>
                  <span
                    aria-hidden="true"
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      TONE_DOT[bot.tone] ?? TONE_DOT.idle,
                    )}
                  />
                  <span className="truncate">{bot.name}</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          );
        })}
        {bots.length > shown.length ? (
          <SidebarMenuItem>
            <SidebarMenuButton asChild>
              <Link href="/workflow" className="text-muted-foreground">
                <span className="h-1.5 w-1.5 shrink-0" aria-hidden="true" />
                <span className="truncate">
                  {bots.length - shown.length} more
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        ) : null}
      </SidebarMenu>
    </SidebarGroup>
  );
}
