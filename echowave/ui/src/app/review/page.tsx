"use client";

/**
 * The review inbox: calls worth listening to, worst first.
 *
 * Post-call QA grades every call; the grades sat on the run page, one call
 * at a time, so nobody read them. This is ten minutes a morning: the bad
 * ones from the last week with the one line QA wrote, a link to the
 * recording, and nothing else.
 */

import { ArrowUpRight, ClipboardCheck } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import { EmptyState } from "@/components/EmptyState";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatCreditsLabel } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

type Item = {
  run_id: number;
  workflow_id: number;
  agent_name: string;
  created_at: string | null;
  billable_seconds: number | null;
  charged_paise: number | null;
  call_type: string | null;
  summary: string | null;
  score: number | null;
  sentiment: string | null;
  tags: string[];
  needs_attention: boolean;
};

type Review = { days: number; graded: number; needs_attention: number; items: Item[] };

function ago(iso: string | null): string {
  if (!iso) return "";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 3600) return `${Math.max(1, Math.floor(seconds / 60))}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function ScorePill({ score }: { score: number | null }) {
  if (score === null) return <span className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">unscored</span>;
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums",
        score >= 7 ? "bg-emerald-100 text-emerald-800" : score >= 4 ? "bg-amber-100 text-amber-800" : "bg-red-100 text-red-800",
      )}
    >
      {score}/10
    </span>
  );
}

export default function ReviewPage() {
  const { user, loading: authLoading } = useAuth();
  const [days, setDays] = useState(7);
  const [attentionOnly, setAttentionOnly] = useState(true);
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const started = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    const result = await client.get({
      url: "/api/v1/organizations/usage/review",
      query: { days, attention_only: attentionOnly, limit: 100 },
    });
    setLoading(false);
    if (result.error || !result.data) {
      setError(detailFromResult(result, "Could not load the review queue."));
      return;
    }
    setError(null);
    setReview(result.data as unknown as Review);
  }, [days, attentionOnly]);

  useEffect(() => {
    if (authLoading || !user) return;
    started.current = true;
    void load();
  }, [authLoading, user, load]);

  return (
    <>
      <PageHeader
        title="Review"
        description="Calls worth listening to, worst first. Graded after every call."
        actions={
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={attentionOnly} onCheckedChange={setAttentionOnly} id="attention-only" />
              <span>Needs attention only</span>
            </label>
            <div className="flex rounded-full border border-border p-0.5">
              {[7, 30].map((d) => (
                <Button
                  key={d}
                  size="sm"
                  variant={days === d ? "default" : "ghost"}
                  className="h-7 rounded-full px-3 text-xs"
                  onClick={() => setDays(d)}
                >
                  {d} days
                </Button>
              ))}
            </div>
          </div>
        }
      />
      <PageBody>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {review && (
          <p className="mb-4 text-sm text-muted-foreground" data-testid="review-summary">
            {review.graded} calls graded in the last {review.days} days · {review.needs_attention} need attention
          </p>
        )}
        {!loading && review && review.items.length === 0 ? (
          <EmptyState
            icon={ClipboardCheck}
            title={attentionOnly ? "Nothing needs attention" : "No graded calls yet"}
            description={
              attentionOnly
                ? "Every graded call in this period scored well. Switch the filter off to read them all."
                : "Grades appear a few seconds after each call ends."
            }
          />
        ) : (
          <ul className="divide-y divide-border rounded-xl border border-border bg-card" data-testid="review-list">
            {(review?.items ?? []).map((item) => (
              <li key={item.run_id} className="flex flex-col gap-2 p-4 sm:flex-row sm:items-start sm:gap-4">
                <div className="flex shrink-0 items-center gap-2 sm:w-40">
                  <ScorePill score={item.score} />
                  {item.sentiment && <span className="text-xs capitalize text-muted-foreground">{item.sentiment}</span>}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm">{item.summary ?? "No summary for this call."}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {item.agent_name} · {ago(item.created_at)}
                    {item.billable_seconds !== null && ` · ${Math.round(item.billable_seconds)}s`}
                    {item.charged_paise !== null && ` · ${formatCreditsLabel(item.charged_paise)}`}
                  </p>
                  {item.tags.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {item.tags.slice(0, 6).map((tag) => (
                        <span key={tag} className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">{tag}</span>
                      ))}
                    </div>
                  )}
                </div>
                <Link
                  href={`/workflow/${item.workflow_id}/run/${item.run_id}`}
                  className="inline-flex shrink-0 items-center gap-1 text-xs font-medium underline-offset-4 hover:underline"
                >
                  Listen <ArrowUpRight className="h-3 w-3" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </PageBody>
    </>
  );
}
