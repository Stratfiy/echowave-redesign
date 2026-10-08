"use client";

import { UserCog } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { stopImpersonationApiV1ImpersonationStopPost } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";

// Mirrors IMPERSONATION_MARKER in app/impersonate/session-cookies.ts, which
// is server-only and not imported here so the shell bundles no route code.
const MARKER = "decibyl-impersonating";

/** Whether a staffer is acting as this account right now. Exported for the
 *  prompts that must never be answered on a customer's behalf. */
export function isImpersonating(): boolean {
  return readMarker() !== null;
}

/** The longest the way out waits for the audit write. Leaving must never be
 *  held hostage by the API: past this, the session ends unaudited-at-stop and
 *  still expires on its own within the hour. */
export const STOP_AUDIT_TIMEOUT_MS = 2_000;

/**
 * Tell the API the impersonation is ending (ADMIN-2, A4), so the audit log
 * has `impersonation_stopped` beside `impersonation_started`. Called with the
 * borrowed session, which is the only one the browser holds at this point.
 * Never throws and never takes longer than the timeout.
 */
export async function recordImpersonationStop(
  timeoutMs: number = STOP_AUDIT_TIMEOUT_MS,
): Promise<boolean> {
  try {
    const result = await Promise.race([
      stopImpersonationApiV1ImpersonationStopPost(),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs)),
    ]);
    return Boolean(result && !result.error);
  } catch {
    return false;
  }
}

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
  const [stopping, setStopping] = useState(false);
  const recorded = useRef(false);
  useEffect(() => {
    setWho(readMarker());
  }, []);
  if (who === null) return null;
  // `ro:` marks a read-only view (app/impersonate/session-cookies.ts).
  const readOnly = who.startsWith("ro:");
  const name = readOnly ? who.slice(3) : who;

  // Record the stop first, then let the form do what it always did: the
  // /impersonate/stop route clears the borrowed session and sends the
  // staffer to sign in. form.submit() does not re-fire onSubmit.
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    if (recorded.current) return;
    event.preventDefault();
    const form = event.currentTarget;
    setStopping(true);
    void recordImpersonationStop().finally(() => {
      recorded.current = true;
      form.submit();
    });
  };

  return (
    <form
      method="POST"
      action="/impersonate/stop"
      onSubmit={onSubmit}
      role="status"
      className="flex flex-wrap items-center gap-3 border-b border-border bg-[var(--tint-amber)] px-6 py-2 text-sm"
    >
      <UserCog className="h-4 w-4 shrink-0 text-foreground/70" aria-hidden />
      {readOnly ? (
        <span className="min-w-0">
          Viewing as{" "}
          <strong className="font-medium">
            {name === "1" ? "a customer" : name}
          </strong>
          , read-only — nothing can be changed from here, and this view is
          logged. Ends by itself in an hour.
        </span>
      ) : (
        <span className="min-w-0">
          Acting as{" "}
          <strong className="font-medium">
            {name === "1" ? "a customer" : name}
          </strong>{" "}
          — everything you do here is theirs and is logged. Ends by itself in an
          hour.
        </span>
      )}
      <Button
        type="submit"
        size="sm"
        variant="outline"
        className="ml-auto bg-card"
        disabled={stopping}
      >
        {stopping ? "Stopping…" : readOnly ? "End view" : "Stop impersonating"}
      </Button>
    </form>
  );
}
