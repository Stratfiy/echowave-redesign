"use client";

/**
 * "≈ 6 credits/min" beside the Test button.
 *
 * The person about to press Test is the one person who should not have to
 * open Billing to learn what a minute costs. The number is the whole minute
 * from the agent's own model row -- the same figure the tiles at the top of
 * the editor show -- so this sentence and that row never disagree. It renders
 * nothing rather than a wrong number when the row cannot price the stack.
 */

import { useEffect, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import { useAuth } from "@/lib/auth";
import { formatCreditsLabel } from "@/lib/billing/format";

type Row = { cost?: { total_paise_per_minute?: number | null; unpriced?: string[] } | null };

export function ratePerMinute(row: Row | null | undefined): number | null {
  const cost = row?.cost;
  if (!cost || typeof cost.total_paise_per_minute !== "number") return null;
  // A total with a missing line is not a smaller price, it is a wrong one.
  if (cost.unpriced && cost.unpriced.length > 0) return null;
  return cost.total_paise_per_minute;
}

export function EstimatedRate({ workflowId }: { workflowId: number }) {
  const { user, loading } = useAuth();
  const [paise, setPaise] = useState<number | null>(null);
  const fetched = useRef<number | null>(null);

  useEffect(() => {
    if (loading || !user || fetched.current === workflowId) return;
    fetched.current = workflowId;
    void (async () => {
      const result = await client.get({ url: `/api/v1/workflow/${workflowId}/model-row` });
      if (result.error || !result.data) return;
      setPaise(ratePerMinute(result.data as Row));
    })();
  }, [loading, user, workflowId]);

  if (paise === null) return null;
  return (
    <span data-testid="estimated-rate">
      {" "}
      (about {formatCreditsLabel(paise)} a minute)
    </span>
  );
}

export default EstimatedRate;
