"use client";

/**
 * "≈ 6 credits/min" beside the Test button.
 *
 * The person about to press Test is the one person who should not have to
 * open Billing to learn what a minute costs. The number is the agent's
 * Simple choice priced by the same service the picker uses, so the picker
 * and this sentence never disagree. It renders nothing rather than a wrong
 * number when the agent is on a stack the picker cannot price.
 */

import { useEffect, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import { useAuth } from "@/lib/auth";
import { formatCreditsLabel } from "@/lib/billing/format";

type Variant = { tier?: string; paise_per_minute: number | null };
type Bundle = { key?: string; code?: string; id?: string; variants?: Variant[] };
type Options = { bundles?: Bundle[]; selected?: { bundle?: string; tier?: string } | null };

export function ratePerMinute(options: Options | null | undefined): number | null {
  if (!options?.selected?.bundle || !options.bundles) return null;
  const bundle = options.bundles.find(
    (b) => (b.key ?? b.code ?? b.id) === options.selected?.bundle,
  );
  const variants = bundle?.variants ?? [];
  const variant =
    variants.find((v) => (v.tier ?? "") === (options.selected?.tier ?? "")) ?? variants[0];
  return typeof variant?.paise_per_minute === "number" ? variant.paise_per_minute : null;
}

export function EstimatedRate({ workflowId }: { workflowId: number }) {
  const { user, loading } = useAuth();
  const [paise, setPaise] = useState<number | null>(null);
  const fetched = useRef<number | null>(null);

  useEffect(() => {
    if (loading || !user || fetched.current === workflowId) return;
    fetched.current = workflowId;
    void (async () => {
      const result = await client.get({
        url: "/api/v1/agent-options",
        query: { workflow_id: workflowId },
      });
      if (result.error || !result.data) return;
      setPaise(ratePerMinute(result.data as Options));
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
