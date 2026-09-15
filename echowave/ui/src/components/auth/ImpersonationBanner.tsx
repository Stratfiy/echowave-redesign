"use client";

import { UserCog } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";

// Mirrors IMPERSONATION_MARKER in app/impersonate/session-cookies.ts, which
// is server-only and not imported here so the shell bundles no route code.
const MARKER = "decibyl-impersonating";

function readMarker(): string | null {
  try {
    const hit = document.cookie
      .split("; ")
      .find((pair) => pair.startsWith(`${MARKER}=`));
    return hit ? decodeURIComponent(hit.slice(MARKER.length + 1)) : null;
  } catch {
    return null;
  }
}

/**
 * The bar a staffer sees for the whole time they act as a customer (KAN-82):
 * that this is not their own account, whose it is, and one button out. Never
 * dismissable -- the finding was a borrowed session with no visible end.
 */
export function ImpersonationBanner() {
  const [who, setWho] = useState<string | null>(null);
  useEffect(() => {
    setWho(readMarker());
  }, []);
  if (who === null) return null;
  return (
    <form
      method="POST"
      action="/impersonate/stop"
      role="status"
      className="flex flex-wrap items-center gap-3 border-b border-border bg-[var(--tint-amber)] px-6 py-2 text-sm"
    >
      <UserCog className="h-4 w-4 shrink-0 text-foreground/70" aria-hidden />
      <span className="min-w-0">
        Acting as{" "}
        <strong className="font-medium">
          {who === "1" ? "a customer" : who}
        </strong>{" "}
        — everything you do here is theirs and is logged. Ends by itself in an
        hour.
      </span>
      <Button
        type="submit"
        size="sm"
        variant="outline"
        className="ml-auto bg-card"
      >
        Stop impersonating
      </Button>
    </form>
  );
}
