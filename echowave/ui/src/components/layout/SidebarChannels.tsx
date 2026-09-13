"use client";

/**
 * The channels, above the bots, like every workspace product.
 *
 * A channel is where a person and several bots talk: you type, you address one
 * by its handle, and what it does afterwards lands in the same thread. The
 * backend for that shipped a while before any of it was reachable — the
 * endpoints existed, the worker answered, and there was no door. This is the
 * door.
 *
 * Above `SidebarBots` on purpose. A bot on its own is a thing you configure; a
 * channel is a place you work, and the reference products all lead with the
 * places. Both stay, because opening one bot is still the right move when you
 * want to change how it behaves rather than ask it for something.
 *
 * Renders nothing when there are no channels. An empty CHANNELS heading on a
 * fresh account is noise, and `/workflow` is the door to making one.
 */

import { Hash, Plus } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { listFoldersApiV1FolderGet } from "@/client/sdk.gen";
import type { FolderResponse } from "@/client/types.gen";
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useAuth } from "@/lib/auth";

/**
 * How many the rail will hold. Same reasoning as the bot cap: an account with
 * forty channels would otherwise push Billing and Settings off a laptop
 * screen, and `/workflow` lists them all.
 */
export const CHANNEL_LIMIT = 8;

export function SidebarChannels({ collapsed }: { collapsed: boolean }) {
  const pathname = usePathname();
  const { user, loading: authLoading } = useAuth();
  const [channels, setChannels] = useState<FolderResponse[]>([]);
  const started = useRef(false);

  useEffect(() => {
    // Wait for auth -- same reason as SidebarBots, and the same bug: this was
    // written by copying that component, guard omitted and all. See
    // ui/AGENTS.md.
    if (authLoading || !user || started.current) return;
    started.current = true;
    let cancelled = false;
    (async () => {
      try {
        const response = await listFoldersApiV1FolderGet();
        if (!cancelled) setChannels(response.data ?? []);
      } catch {
        // A shortcut, not a destination: /workflow lists the channels too.
        // Failing quietly costs a convenience.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authLoading, user]);

  // Icon mode has no room for names, and a column of bare hashes is a puzzle.
  if (collapsed) return null;

  const shown = channels.slice(0, CHANNEL_LIMIT);

  return (
    <SidebarGroup className="py-1">
      {/* The heading is a door and carries its own action, the way the
          reference does it: the label opens the full list, the plus makes a
          new one. Shown even with nothing under it -- an account with no
          channels needs the plus more than one with eight. */}
      <SidebarGroupLabel className="h-7 justify-between text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        <Link href="/workflow" className="hover:text-foreground">
          Channels
        </Link>
        <Link
          href="/workflow"
          aria-label="New channel"
          title="New channel"
          className="rounded p-0.5 hover:bg-sidebar-accent hover:text-foreground"
        >
          <Plus aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </SidebarGroupLabel>
      <SidebarMenu>
        {shown.map((channel) => {
          const href = `/channels/${channel.id}`;
          return (
            <SidebarMenuItem key={channel.id}>
              <SidebarMenuButton asChild isActive={pathname === href}>
                <Link href={href}>
                  <Hash aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{channel.name}</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          );
        })}
        {channels.length > shown.length ? (
          <SidebarMenuItem>
            <SidebarMenuButton asChild>
              <Link href="/workflow" className="text-muted-foreground">
                <span className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span className="truncate">
                  {channels.length - shown.length} more
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        ) : null}
      </SidebarMenu>
    </SidebarGroup>
  );
}

export default SidebarChannels;
