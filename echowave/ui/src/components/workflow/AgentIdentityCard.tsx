// Copyright 2026 Block, Inc. Licensed under Apache-2.0.
// Adapted from block/buzz AgentIdentityCard.tsx, commit ef2aa1ae38fadcc0bc22b8bf6ed96b35933146be.
// Modified for Decibyl: local utility import and initials avatar; no profile API or runtime dependencies.
// License and provenance: public/licenses/buzz-LICENSE.txt and buzz-NOTICE.txt.

import type { MouseEventHandler, ReactNode } from "react";

import { cn } from "@/lib/utils";


type AgentIdentityCardProps = {
  actions?: ReactNode;
  ariaLabel: string;
  avatar?: ReactNode;
  footerAccessory?: ReactNode;
  dataTestId: string;
  label: string;
  /**
   * Second line under the agent name, supplied by the directory.
   */
  subtitle?: string | null;
  onClick: MouseEventHandler<HTMLButtonElement>;
  /** Optional badge rendered below the label (e.g. "Restart required"). */
  statusBadge?: ReactNode;
};

export function AgentIdentityCard({
  actions,
  ariaLabel,
  avatar,
  dataTestId,
  footerAccessory,
  label,
  subtitle,
  onClick,
  statusBadge,
}: AgentIdentityCardProps) {

  return (
    <div
      className={cn(
        "group relative aspect-[4/5] w-full min-w-0 overflow-hidden rounded-2xl border border-border/70 bg-muted/50 text-left shadow-xs transition-colors hover:border-border hover:bg-muted/65",
      )}
      data-testid={dataTestId}
    >
      <button
        aria-label={ariaLabel}
        className="absolute inset-0 z-10 rounded-2xl focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
        onClick={onClick}
        type="button"
      />

      <div className="pointer-events-none relative z-20 flex h-full w-full min-w-0 flex-col items-center justify-center gap-5 px-4 pb-12 text-center">
        <div className="flex h-24 w-24 items-center justify-center">
          {avatar ?? <span className="flex h-20 w-20 items-center justify-center rounded-2xl bg-background text-3xl font-semibold" aria-hidden="true">{label.trim().slice(0, 2).toUpperCase()}</span>}
        </div>
      </div>

      {actions ? (
        <div className="absolute top-3 right-3 z-40">{actions}</div>
      ) : null}

      <div className="pointer-events-none absolute right-3 bottom-3 left-3 z-30 flex min-w-0 items-end gap-2 text-left text-sm leading-5">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="min-w-0 truncate font-semibold text-foreground tracking-normal">
            {label}
          </span>
          {subtitle ? (
            <span className="line-clamp-2 min-w-0 text-xs font-normal text-muted-foreground">
              {subtitle}
            </span>
          ) : null}
          {/* pointer-events-auto: the overlay button above has pointer-events-none
              on this container, but the status badge itself (a sibling of the button
              in z-order) needs hover so the restart diff tooltip can fire. */}
          {statusBadge ? (
            <div className="pointer-events-auto">{statusBadge}</div>
          ) : null}
        </div>
        {footerAccessory ? (
          <div className="shrink-0">{footerAccessory}</div>
        ) : null}
      </div>
    </div>
  );
}
