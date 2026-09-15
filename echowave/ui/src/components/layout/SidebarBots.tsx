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

import { Plus } from "lucide-react";
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
import { isUnread } from "@/lib/botSeen";
import { cn } from "@/lib/utils";

/** Same four tones as the home screen and the bot list. One vocabulary. */
const TONE_DOT: Record<string, string> = {
  attention: "bg-destructive",
  working: "bg-emerald-500",
  idle: "bg-sidebar-foreground/30",
  paused: "bg-amber-500",
};

/** Two letters from a name, for the tile beside it. The same tile the Home
 *  screen already draws ("KL", "ND"), so a bot looks like one thing on two
 *  screens rather than a dot here and a monogram there. */
export function initials(name: string): string {
  const words = (name || "").trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

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
        const response = await teamStatusApiV1TeamStatusGet({
          query: { hours: 24 },
        });
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
  if (collapsed) return null;

  const shown = bots.slice(0, RAIL_LIMIT);

  return (
    <SidebarGroup className="py-1">
      {/* Label opens the full list, plus hires a new one. Shown even with
          nothing under it: the plus is the door a fresh account needs. */}
      <SidebarGroupLabel className="h-8 justify-between text-[15px] font-normal text-sidebar-foreground/70">
        <Link href="/workflow" className="hover:text-sidebar-foreground">
          Your bots
        </Link>
        <Link
          href="/workflow/create"
          aria-label="Add a bot"
          title="Add a bot"
          className="rounded p-0.5 hover:bg-sidebar-accent hover:text-sidebar-foreground"
        >
          <Plus aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </SidebarGroupLabel>
      <SidebarMenu>
        {shown.map((bot) => {
          // The thread, not the editor. A bot in the workspace panel is a
          // teammate you talk to; how it is configured is a tab away once you
          // are there. Landing on the model form was the single thing that
          // made this read as a builder rather than a team.
          const href = `/workflow/${bot.workflow_id}/thread`;
          const active = pathname.startsWith(`/workflow/${bot.workflow_id}`);
          const unread = !active && isUnread(bot.workflow_id, bot.last_at);
          return (
            <SidebarMenuItem key={bot.workflow_id}>
              <SidebarMenuButton
                asChild
                isActive={active}
                tooltip={bot.status}
                className="h-11"
              >
                {/* `title` as well as the status tooltip: a name long enough
                    to truncate is exactly the name somebody needs to read in
                    full, and the tooltip slot is already spent on what the bot
                    is doing. The browser's own is free and does not fight it. */}
                <Link href={href} title={bot.name}>
                  <span
                    aria-hidden="true"
                    className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-sidebar-foreground/15 text-[10px] font-semibold text-sidebar-foreground"
                  >
                    {initials(bot.name)}
                  </span>
                  {/* Two lines, like a chat list: the name, and the last
                      thing said or done. The rail read as a directory with
                      one line; a teammate you talk to has a last message. */}
                  <span className="flex min-w-0 flex-1 flex-col leading-tight">
                    <span className={cn("truncate", unread && "font-semibold")}>
                      {bot.name}
                    </span>
                    {bot.last_line && (
                      <span className="truncate text-[11px] font-normal text-sidebar-foreground/60">
                        {bot.last_actor === "human" ? "You: " : ""}
                        {bot.last_line}
                      </span>
                    )}
                  </span>
                  {unread ? (
                    // Unread, in the brand colour: something happened since
                    // this person last opened the bot here.
                    <span
                      aria-label="Unread"
                      className="h-2 w-2 shrink-0 rounded-full bg-[var(--accent-brand)]"
                    />
                  ) : (
                    <span
                      aria-hidden="true"
                      className={cn(
                        "h-1.5 w-1.5 shrink-0 rounded-full",
                        TONE_DOT[bot.tone] ?? TONE_DOT.idle,
                      )}
                    />
                  )}
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          );
        })}
        {bots.length > shown.length ? (
          <SidebarMenuItem>
            <SidebarMenuButton asChild>
              <Link href="/workflow" className="text-sidebar-foreground/60">
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
