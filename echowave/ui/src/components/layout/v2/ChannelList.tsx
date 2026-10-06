"use client";

/**
 * The channels, in the v2 rail: where a person and several agents talk.
 *
 * Moved from the old sidebar (SidebarChannels) when v2 became the only shell,
 * so the door to /channels is not lost with it. Above the colleagues, as the
 * old one sat above the bots: a channel is a place you work, an agent a thing
 * you open to change how it behaves.
 *
 * The heading opens the full list; the plus starts a new chat. Shown even
 * with no channels, because an account with none needs the plus most.
 */

import { Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { listFoldersApiV1FolderGet } from "@/client/sdk.gen";
import type { FolderResponse } from "@/client/types.gen";
import { NewChatDialog } from "@/components/layout/NewChatDialog";
import { useAuth } from "@/lib/auth";

/** How many the rail holds before "n more"; /channels lists them all. */
export const CHANNEL_LIMIT = 6;

export function ChannelList({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  const { user, loading: authLoading } = useAuth();
  const [channels, setChannels] = useState<FolderResponse[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const [newChatOpen, setNewChatOpen] = useState(false);
  const started = useRef(false);

  useEffect(() => {
    if (authLoading || !user || started.current) return;
    started.current = true;
    let cancelled = false;
    void (async () => {
      try {
        const response = await listFoldersApiV1FolderGet();
        if (cancelled) return;
        if (response.error) {
          setState("failed");
          return;
        }
        setChannels(response.data ?? []);
        setState("ready");
      } catch {
        if (!cancelled) setState("failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authLoading, user]);

  const shown = channels.slice(0, CHANNEL_LIMIT);
  const hidden = channels.length - shown.length;

  return (
    <section aria-labelledby="v2-channels-label" className="v2-section">
      <div className="v2-eyebrow-row">
        <h2 id="v2-channels-label" className="v2-eyebrow">
          <Link href="/channels" onClick={onNavigate}>
            Channels
          </Link>
        </h2>
        <button type="button" aria-label="New chat" title="New chat" className="v2-icon-link" onClick={() => setNewChatOpen(true)}>
          <Plus aria-hidden="true" className="h-3.5 w-3.5" />
        </button>
        {newChatOpen && <NewChatDialog open onOpenChange={setNewChatOpen} />}
      </div>
      <ul className="v2-roster">
        {shown.map((channel) => {
          const href = `/channels/${channel.id}`;
          return (
            <li key={channel.id}>
              <Link href={href} aria-current={pathname === href ? "page" : undefined} onClick={onNavigate}>
                <span aria-hidden="true" className="v2-roster-sub">
                  #
                </span>
                <span className="v2-roster-name">{channel.name}</span>
              </Link>
            </li>
          );
        })}
        {state !== "loading" && channels.length === 0 && (
          <li className="v2-roster-sub px-2 py-1">
            {state === "failed" ? "Could not load your channels." : "No channels yet. The plus starts one."}
          </li>
        )}
        {hidden > 0 && (
          <li>
            <Link href="/channels" className="v2-roster-more" onClick={onNavigate}>
              {hidden} more
            </Link>
          </li>
        )}
      </ul>
    </section>
  );
}
