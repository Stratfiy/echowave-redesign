import { MessageCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { recentsApiV1TimelineRecentsGet } from "@/client/sdk.gen";
import { type Avatar } from "@/components/avatar/avatar";
import { BlobFace } from "@/components/brand/BlobFace";
import { useAuth } from "@/lib/auth";

import { RAIL_COPY } from "./homes";

export type RecentItem = {
  kind: "decibyl" | "agent";
  key: string;
  title: string;
  subtitle: string | null;
  href: string;
  workflow_id: number | null;
  avatar: Avatar | null;
};

/** How many conversations the rail holds. */
export const RECENTS_LIMIT = 12;

/**
 * The rail's Recents: the conversations somebody was last in -- chats with
 * Decibyl and with agents, newest first, the way ChatGPT's sidebar works.
 * It replaced a list of every agent, which repeated the Agents page one
 * click above it. Read again on every page change, so a chat that was just
 * started is there when you look for it.
 */
export function RecentsList({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  const { user, loading: authLoading } = useAuth();
  const [items, setItems] = useState<RecentItem[] | null>(null);

  useEffect(() => {
    if (authLoading || !user) return;
    let cancelled = false;
    void recentsApiV1TimelineRecentsGet({ query: { limit: RECENTS_LIMIT } }).then((res) => {
      if (cancelled || res.error) return;
      setItems(((res.data as { items?: RecentItem[] } | undefined)?.items ?? []) as RecentItem[]);
    });
    return () => {
      cancelled = true;
    };
  }, [authLoading, user, pathname]);

  if (!items || items.length === 0) return null;

  return (
    <section aria-labelledby="v2-recents-label" className="v2-section">
      <div className="v2-eyebrow-row">
        <h2 id="v2-recents-label" className="v2-eyebrow">
          {RAIL_COPY.recents}
        </h2>
      </div>
      <ul className="v2-roster" data-testid="v2-recents">
        {items.map((item) => {
          const active = item.kind === "agent" && (pathname === item.href || pathname.startsWith(`${item.href}/`));
          return (
            <li key={item.key}>
              <Link href={item.href} title={item.title} aria-current={active ? "page" : undefined} onClick={onNavigate}>
                {item.kind === "agent" && item.workflow_id !== null ? (
                  <BlobFace seed={item.workflow_id} avatar={item.avatar} size={26} />
                ) : (
                  <span className="grid h-[26px] w-[26px] shrink-0 place-items-center text-muted-foreground" aria-hidden="true">
                    <MessageCircle className="h-4 w-4" strokeWidth={1.7} />
                  </span>
                )}
                <span className="v2-roster-text">
                  <span className="v2-roster-name">{item.title}</span>
                  {item.subtitle && <span className="v2-roster-sub">{item.subtitle}</span>}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
