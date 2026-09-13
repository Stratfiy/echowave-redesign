"use client";

/**
 * The bot's own header: who it is, how it is doing, and what it has.
 *
 * Opening a bot dropped you straight into a canvas. The bot has a run history,
 * a tool list, an eval set and a settings screen — all of them real routes,
 * none of them reachable from the screen you land on. A canvas with no chrome
 * says "you are editing a diagram"; a name, a status line and a row of tabs
 * says "this is a teammate, here is its work".
 *
 * The shape is Slack's, from their own product rather than their marketing
 * page: identity on one line, a plain left-aligned tab strip directly under
 * it. Their bot screens carry Messages · History · Tasks; ours carry the tabs
 * whose surfaces exist. Messages and Tasks need an endpoint that can *read*
 * agent_events — today `agent_timeline.py` only writes them — so they are
 * absent rather than present-and-dead. A tab that goes nowhere is worse than
 * a tab that is not there yet.
 *
 * When Messages lands it becomes the index route and the canvas becomes a
 * link beside the strip, which is where the spec ends up. Until then the
 * canvas is the first tab, because it is still where you arrive.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { teamStatusApiV1TeamStatusGet } from "@/client/sdk.gen";
import type { TeamMember } from "@/client/types.gen";
import { cn } from "@/lib/utils";

/** The same four tones as the rail, the bot list and the home panel. */
const TONE_DOT: Record<string, string> = {
  attention: "bg-destructive",
  working: "bg-emerald-500",
  idle: "bg-muted-foreground/40",
  paused: "bg-amber-500",
};

export interface BotTab {
  label: string;
  /** Appended to /workflow/{id}; "" is the bot's index route. */
  segment: string;
}

/** Only surfaces that exist. See the note above about absent vs dead tabs. */
export const BOT_TABS: readonly BotTab[] = [
  { label: "Canvas", segment: "" },
  { label: "History", segment: "/runs" },
  { label: "Tools", segment: "/tools" },
  { label: "Evals", segment: "/evals" },
  { label: "Settings", segment: "/settings" },
] as const;

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

export function BotRibbon({ workflowId, name }: { workflowId: number; name: string }) {
  const pathname = usePathname();
  const [member, setMember] = useState<TeamMember | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await teamStatusApiV1TeamStatusGet({ query: { hours: 24 } });
        if (cancelled) return;
        const found = (response.data?.members ?? []).find(
          (m) => m.workflow_id === workflowId,
        );
        setMember(found ?? null);
      } catch {
        // The status line is context, not navigation. Losing it must not cost
        // the tabs, which are the reason this header exists.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workflowId]);

  const base = `/workflow/${workflowId}`;

  return (
    <div className="border-b border-border bg-background px-4 pt-3">
      <div className="flex items-center gap-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted text-xs font-semibold text-muted-foreground">
          {initials(name)}
        </div>
        <div className="min-w-0">
          <h1 className="truncate text-base font-semibold leading-tight">{name}</h1>
          {member ? (
            <p className="flex items-center gap-1.5 truncate text-xs text-muted-foreground">
              <span
                aria-hidden="true"
                className={cn(
                  "h-1.5 w-1.5 shrink-0 rounded-full",
                  TONE_DOT[member.tone] ?? TONE_DOT.idle,
                )}
              />
              {member.status}
            </p>
          ) : null}
        </div>
      </div>

      <nav aria-label="Bot" className="mt-2 flex items-center gap-4 overflow-x-auto">
        {BOT_TABS.map((tab) => {
          const href = `${base}${tab.segment}`;
          // Exact match for the index tab: every other route starts with it,
          // so a prefix test would light Canvas on every screen.
          const active = tab.segment === "" ? pathname === href : pathname.startsWith(href);
          return (
            <Link
              key={tab.label}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "whitespace-nowrap border-b-2 pb-2 text-sm transition-colors",
                active
                  ? "border-[var(--accent-brand)] font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
