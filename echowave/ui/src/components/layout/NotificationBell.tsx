"use client";

/**
 * The bell.
 *
 * Every notice about money used to go out by email and nowhere else, so a
 * person sitting in the product found out about a low balance at the
 * moment a call was refused. This shows the same notices where they are,
 * counts the unread ones, and marks them read when the list is opened —
 * the inbox is the organization's, so a warning one colleague has seen is
 * seen.
 *
 * Polled rather than pushed: a notice a minute late is fine, a socket for
 * it is not worth having. Refetched on focus so a tab left open catches up.
 */

import { Bell } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type Item = {
  id: number;
  kind: string;
  title: string;
  body: string | null;
  link: string | null;
  created_at: string;
  read_at: string | null;
};

const POLL_MS = 60_000;

function ago(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function NotificationBell() {
  const { user, loading: authLoading } = useAuth();
  const [items, setItems] = useState<Item[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const started = useRef(false);

  const load = useCallback(async () => {
    const result = await client.get({ url: "/api/v1/notifications", query: { limit: 20 } });
    if (result.error || !result.data) return;
    const data = result.data as unknown as { items: Item[]; unread: number };
    setItems(data.items ?? []);
    setUnread(data.unread ?? 0);
  }, []);

  useEffect(() => {
    if (authLoading || !user || started.current) return;
    started.current = true;
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [authLoading, user, load]);

  const onOpenChange = async (next: boolean) => {
    setOpen(next);
    if (!next || unread === 0) return;
    // Opening the list is reading it. Optimistic, then confirmed.
    setUnread(0);
    setItems((current) =>
      current.map((item) => ({ ...item, read_at: item.read_at ?? new Date().toISOString() })),
    );
    await client.post({ url: "/api/v1/notifications/read", body: {} });
  };

  if (!user) return null;

  return (
    <Popover open={open} onOpenChange={(next) => void onOpenChange(next)}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="relative rounded-full"
              aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
              data-testid="notification-bell"
            >
              <Bell className="h-[18px] w-[18px]" />
              {unread > 0 && (
                <span
                  className="absolute right-1.5 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--accent-brand)] px-1 text-[10px] font-semibold leading-none text-white"
                  data-testid="notification-unread"
                >
                  {unread > 9 ? "9+" : unread}
                </span>
              )}
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>Notifications</TooltipContent>
      </Tooltip>
      <PopoverContent align="end" className="w-[360px] p-0">
        <div className="border-b border-border px-4 py-3">
          <p className="text-sm font-medium">Notifications</p>
        </div>
        <div className="max-h-96 overflow-y-auto">
          {items.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-muted-foreground">
              Nothing yet. Low credit, charges and account notices land here.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {items.map((item) => {
                const inner = (
                  <>
                    <p className={cn("text-sm", item.read_at ? "font-normal" : "font-medium")}>{item.title}</p>
                    {item.body && (
                      <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{item.body}</p>
                    )}
                    <p className="mt-1 text-[11px] text-muted-foreground">{ago(item.created_at)}</p>
                  </>
                );
                return (
                  <li key={item.id}>
                    {item.link ? (
                      <Link
                        href={item.link}
                        onClick={() => setOpen(false)}
                        className="block px-4 py-3 transition-colors hover:bg-muted/50"
                      >
                        {inner}
                      </Link>
                    ) : (
                      <div className="px-4 py-3">{inner}</div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

export default NotificationBell;
