"use client";

/**
 * The card after a test call: what the agent understood, how the call
 * went, what it cost, and where to go next.
 *
 * Post-call QA already runs on every call and writes its verdict on the
 * run; nobody saw it without opening the run page. This polls the run for
 * a minute after the call ends — QA is an inference and takes a few
 * seconds — and shows the verdict where the person still is, which turns
 * a test into a reason to edit the prompt.
 */

import { ArrowUpRight, Loader2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { getWorkflowRunApiV1WorkflowWorkflowIdRunsRunIdGet } from "@/client/sdk.gen";
import { formatCreditsLabel } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

type Verdict = {
  summary: string | null;
  score: number | null;
  sentiment: string | null;
  tags: string[];
};

type Run = {
  annotations?: Record<string, unknown> | null;
  charged_paise?: number | null;
  billable_seconds?: number | null;
  is_completed?: boolean;
};

/** Pull one verdict out of the QA annotations, whatever shape they took. */
export function verdictFromAnnotations(annotations: Record<string, unknown> | null | undefined): Verdict | null {
  if (!annotations) return null;
  const candidates: Array<Record<string, unknown>> = [];
  for (const value of Object.values(annotations)) {
    if (!value || typeof value !== "object") continue;
    const block = value as Record<string, unknown>;
    const results = block.node_results;
    if (results && typeof results === "object") {
      for (const node of Object.values(results as Record<string, unknown>)) {
        if (node && typeof node === "object") candidates.push(node as Record<string, unknown>);
      }
    } else if ("summary" in block || "score" in block) {
      candidates.push(block);
    }
  }
  const pick = candidates.find((c) => typeof c.summary === "string" && (c.summary as string).trim()) ?? candidates[0];
  if (!pick) return null;
  const tags = Array.isArray(pick.tags)
    ? (pick.tags as unknown[]).map((t) => (typeof t === "string" ? t : (t as { tag?: string })?.tag ?? "")).filter(Boolean)
    : [];
  return {
    summary: typeof pick.summary === "string" && pick.summary.trim() ? pick.summary : null,
    score: typeof pick.score === "number" ? pick.score : null,
    sentiment: typeof pick.overall_sentiment === "string" ? pick.overall_sentiment : null,
    tags,
  };
}

const POLL_MS = 3000;
const POLL_FOR_MS = 60_000;

export function PostCallSummary({ workflowId, runId, className }: { workflowId: number; runId: number; className?: string }) {
  const [run, setRun] = useState<Run | null>(null);
  const [settled, setSettled] = useState(false);

  useEffect(() => {
    let stopped = false;
    const started = Date.now();
    const tick = async () => {
      const result = await getWorkflowRunApiV1WorkflowWorkflowIdRunsRunIdGet({
        path: { workflow_id: workflowId, run_id: runId },
      });
      if (stopped) return;
      const data = (result.data ?? null) as Run | null;
      if (data) setRun(data);
      const verdict = verdictFromAnnotations(data?.annotations);
      const done = Boolean(verdict?.summary) && typeof data?.charged_paise === "number";
      if (done || Date.now() - started > POLL_FOR_MS) {
        setSettled(true);
        return;
      }
      window.setTimeout(() => void tick(), POLL_MS);
    };
    void tick();
    return () => {
      stopped = true;
    };
  }, [workflowId, runId]);

  const verdict = verdictFromAnnotations(run?.annotations);
  const seconds = run?.billable_seconds ?? null;
  const charged = run?.charged_paise ?? null;

  return (
    <div className={cn("rounded-xl border border-border bg-card p-4 text-sm", className)} data-testid="post-call-summary">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-medium">How that call went</p>
          <p className="text-xs text-muted-foreground">
            {seconds !== null ? `${Math.round(seconds)}s` : "—"}
            {charged !== null ? ` · ${formatCreditsLabel(charged)}` : settled ? "" : " · costing…"}
          </p>
        </div>
        {verdict?.score !== null && verdict?.score !== undefined && (
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums",
              verdict.score >= 7 ? "bg-emerald-100 text-emerald-800" : verdict.score >= 4 ? "bg-amber-100 text-amber-800" : "bg-red-100 text-red-800",
            )}
            data-testid="post-call-score"
          >
            {verdict.score}/10
          </span>
        )}
      </div>
      {verdict?.summary ? (
        <p className="mt-2 text-sm">{verdict.summary}</p>
      ) : settled ? (
        <p className="mt-2 text-xs text-muted-foreground">No review for this call. Open it to read the transcript.</p>
      ) : (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          Reviewing the transcript…
        </p>
      )}
      {(verdict?.sentiment || (verdict?.tags.length ?? 0) > 0) && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {verdict?.sentiment && (
            <span className="rounded-full border border-border px-2 py-0.5 text-[11px] capitalize text-muted-foreground">{verdict.sentiment}</span>
          )}
          {verdict?.tags.slice(0, 5).map((tag) => (
            <span key={tag} className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">{tag}</span>
          ))}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-3 text-xs">
        <Link href={`/workflow/${workflowId}/run/${runId}`} className="inline-flex items-center gap-1 font-medium underline-offset-4 hover:underline">
          Open the call <ArrowUpRight className="h-3 w-3" />
        </Link>
        <Link href={`/workflow/${workflowId}/settings`} className="inline-flex items-center gap-1 font-medium underline-offset-4 hover:underline">
          Fix the prompt <ArrowUpRight className="h-3 w-3" />
        </Link>
      </div>
    </div>
  );
}

export default PostCallSummary;
