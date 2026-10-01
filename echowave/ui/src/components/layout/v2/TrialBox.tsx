import Link from "next/link";

import { formatCredits } from "@/lib/billing/format";

import { RAIL_COPY } from "./homes";
import type { TrialInfo } from "./useRailData";

type TrialBoxProps = {
  trial: TrialInfo | null;
  creditsPaise: number | null;
};

/**
 * The foot of the v2 rail: days left on the trial with a meter, and the
 * credits. With no trial it is credits only; with neither, nothing -- an
 * empty box is not a state worth drawing.
 */
export function TrialBox({ trial, creditsPaise }: TrialBoxProps) {
  const hasCredits = creditsPaise !== null;
  if (!trial && !hasCredits) return null;

  const daysLeft = trial?.daysLeft ?? null;
  const days = trial?.days ?? null;
  const pct =
    daysLeft !== null && days !== null && days > 0
      ? Math.max(0, Math.min(100, Math.round((daysLeft / days) * 100)))
      : null;
  const trialText =
    trial && (!trial.active || daysLeft === 0)
      ? RAIL_COPY.trialEnded
      : daysLeft !== null
        ? RAIL_COPY.daysLeft(daysLeft)
        : null;

  return (
    <Link href="/billing" className="v2-trial" data-testid="v2-trial-box">
      {trial && (
        <div data-testid="v2-trial-days">
          <div className="v2-trial-row">
            <span>{RAIL_COPY.trialLabel}</span>
            {trialText && <b>{trialText}</b>}
          </div>
          {pct !== null && (
            <div
              className="v2-meter"
              role="meter"
              aria-label="Trial days left"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={pct}
            >
              <i style={{ width: `${pct}%` }} />
            </div>
          )}
        </div>
      )}
      {hasCredits && (
        <div className="v2-trial-row">
          <span>{RAIL_COPY.credits}</span>
          <b>{formatCredits(creditsPaise)}</b>
        </div>
      )}
    </Link>
  );
}
