"use client";

/**
 * Evals: scripted callers this agent is rerun against after every edit.
 *
 * Ten callers you wrote once, rerun in a minute, red or green. Each run is
 * a text session on the agent — same prompts, tools and model as a call,
 * no phone minute — graded by phrase rules first and the agent's own model
 * second. The reason a prompt edit can be trusted.
 */

import { ChevronDown, ChevronRight, FlaskConical, Loader2, Play, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { AgentHeader } from "@/app/workflow/[workflowId]/components/AgentHeader";
import { AgentTabs } from "@/app/workflow/[workflowId]/components/AgentTabs";
import { client } from "@/client/client.gen";
import { getWorkflowApiV1WorkflowFetchWorkflowIdGet } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type Result = {
  id: number;
  status: "queued" | "running" | "passed" | "failed" | "error";
  verdict: string | null;
  transcript: Array<{ role: string; text: string }>;
  workflow_run_id: number | null;
  finished_at: string | null;
};
type Case = {
  id: number;
  name: string;
  persona: string;
  goal: string;
  must_say: string[];
  must_not_say: string[];
  max_turns: number;
  latest: Result | null;
};
type Listing = { cases: Case[]; passed: number; failed: number; running: number };

const STATUS_STYLE: Record<Result["status"], string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-muted text-muted-foreground",
  passed: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
  error: "bg-amber-100 text-amber-800",
};

function StatusPill({ result }: { result: Result | null }) {
  if (!result) return <span className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">not run</span>;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium capitalize", STATUS_STYLE[result.status])}>
      {(result.status === "queued" || result.status === "running") && <Loader2 className="h-3 w-3 animate-spin" />}
      {result.status}
    </span>
  );
}

export default function EvalsPage() {
  const workflowId = Number(useParams<{ workflowId: string }>().workflowId);
  const { user, loading: authLoading } = useAuth();
  const [name, setName] = useState("");
  const [listing, setListing] = useState<Listing | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState({ name: "", persona: "", goal: "", must_say: "", must_not_say: "", max_turns: 6 });
  const fetched = useRef(false);

  const load = useCallback(async () => {
    const result = await client.get({ url: `/api/v1/workflow/${workflowId}/evals` });
    if (result.error || !result.data) return;
    setListing(result.data as unknown as Listing);
  }, [workflowId]);

  useEffect(() => {
    if (authLoading || !user || fetched.current) return;
    fetched.current = true;
    void (async () => {
      const wf = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({ path: { workflow_id: workflowId } });
      setName(wf.data?.name ?? "");
      await load();
    })();
  }, [authLoading, user, workflowId, load]);

  // While anything is queued or running, keep the list fresh.
  useEffect(() => {
    if (!listing || listing.running === 0) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [listing, load]);

  const runAll = async () => {
    setBusy(true);
    const result = await client.post({ url: `/api/v1/workflow/${workflowId}/evals/run`, body: {} });
    setBusy(false);
    if (result.error) {
      toast.error(detailFromResult(result, "Could not start the run."));
      return;
    }
    toast.success("Running. Each case is a text session on the agent.");
    await load();
  };

  const add = async () => {
    setBusy(true);
    const result = await client.post({
      url: `/api/v1/workflow/${workflowId}/evals`,
      body: {
        name: draft.name,
        persona: draft.persona,
        goal: draft.goal,
        must_say: draft.must_say.split("\n").map((s) => s.trim()).filter(Boolean),
        must_not_say: draft.must_not_say.split("\n").map((s) => s.trim()).filter(Boolean),
        max_turns: draft.max_turns,
      },
    });
    setBusy(false);
    if (result.error) {
      toast.error(detailFromResult(result, "Could not add the case."));
      return;
    }
    setDraft({ name: "", persona: "", goal: "", must_say: "", must_not_say: "", max_turns: 6 });
    setAdding(false);
    await load();
  };

  const remove = async (id: number) => {
    const result = await client.delete({ url: `/api/v1/workflow/${workflowId}/evals/${id}` });
    if (result.error) {
      toast.error(detailFromResult(result, "Could not delete the case."));
      return;
    }
    await load();
  };

  return (
    <>
      <AgentHeader workflowId={workflowId} name={name} />
      <AgentTabs workflowId={workflowId} />
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Evals</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Scripted callers, rerun after every edit. Each run is a text conversation with the
              agent, paid like one, graded by your rules first and the model second.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setAdding((v) => !v)} data-testid="eval-add">
              <Plus className="h-4 w-4" /> Add caller
            </Button>
            <Button size="sm" onClick={() => void runAll()} disabled={busy || !listing || listing.cases.length === 0} data-testid="eval-run-all">
              <Play className="h-4 w-4" /> Run all
            </Button>
          </div>
        </div>

        {listing && listing.cases.length > 0 && (
          <p className="mt-3 text-xs text-muted-foreground" data-testid="eval-tally">
            {listing.passed} passed · {listing.failed} failed · {listing.running} running · {listing.cases.length} callers
          </p>
        )}

        {adding && (
          <div className="mt-4 space-y-3 rounded-xl border border-border bg-card p-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="eval-name">Name</Label>
                <Input id="eval-name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Refund after a failed delivery" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="eval-persona">Who is calling</Label>
                <Textarea id="eval-persona" rows={3} value={draft.persona} onChange={(e) => setDraft({ ...draft, persona: e.target.value })} placeholder="A driver on the highway, in a hurry, speaks Hindi and English." />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="eval-goal">What they want</Label>
                <Textarea id="eval-goal" rows={3} value={draft.goal} onChange={(e) => setDraft({ ...draft, goal: e.target.value })} placeholder="Open the lock; they have the invoice number but no OTP yet." />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="eval-must">Agent must say (one per line)</Label>
                <Textarea id="eval-must" rows={2} value={draft.must_say} onChange={(e) => setDraft({ ...draft, must_say: e.target.value })} placeholder="press 3#" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="eval-mustnot">Agent must not say (one per line)</Label>
                <Textarea id="eval-mustnot" rows={2} value={draft.must_not_say} onChange={(e) => setDraft({ ...draft, must_not_say: e.target.value })} placeholder="master password" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="eval-turns">Turns at most</Label>
                <Input id="eval-turns" type="number" min={1} max={12} value={draft.max_turns} onChange={(e) => setDraft({ ...draft, max_turns: Number(e.target.value) || 6 })} className="w-24" />
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setAdding(false)}>Cancel</Button>
              <Button size="sm" onClick={() => void add()} disabled={busy || !draft.name.trim() || !draft.persona.trim() || !draft.goal.trim()} data-testid="eval-save">
                Save caller
              </Button>
            </div>
          </div>
        )}

        {listing && listing.cases.length === 0 && !adding ? (
          <div className="mt-8 rounded-xl border border-dashed border-border p-8 text-center">
            <FlaskConical className="mx-auto h-7 w-7 text-muted-foreground" />
            <p className="mt-3 text-sm font-medium">No callers yet</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Write the three calls this agent must get right. Rerun them after every prompt change.
            </p>
          </div>
        ) : (
          <ul className="mt-4 divide-y divide-border rounded-xl border border-border bg-card" data-testid="eval-list">
            {(listing?.cases ?? []).map((c) => {
              const expanded = open === c.id;
              return (
                <li key={c.id} className="p-4">
                  <div className="flex items-start gap-3">
                    <button type="button" onClick={() => setOpen(expanded ? null : c.id)} className="mt-0.5 text-muted-foreground" aria-label={expanded ? "Collapse" : "Expand"}>
                      {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                    </button>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-medium">{c.name}</p>
                        <StatusPill result={c.latest} />
                      </div>
                      <p className="mt-0.5 text-xs text-muted-foreground">{c.goal}</p>
                      {c.latest?.verdict && (c.latest.status === "passed" || c.latest.status === "failed" || c.latest.status === "error") && (
                        <p className="mt-1 text-xs">{c.latest.verdict}</p>
                      )}
                    </div>
                    <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" onClick={() => void remove(c.id)} aria-label="Delete caller">
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                  {expanded && (
                    <div className="mt-3 space-y-2 pl-7 text-xs">
                      <p><span className="text-muted-foreground">Caller:</span> {c.persona}</p>
                      {c.must_say.length > 0 && <p><span className="text-muted-foreground">Must say:</span> {c.must_say.join(" · ")}</p>}
                      {c.must_not_say.length > 0 && <p><span className="text-muted-foreground">Must not say:</span> {c.must_not_say.join(" · ")}</p>}
                      {c.latest && c.latest.transcript.length > 0 && (
                        <div className="rounded-lg border border-border bg-background p-3">
                          {c.latest.transcript.map((t, i) => (
                            <p key={i} className={cn("py-0.5", t.role === "caller" ? "text-muted-foreground" : "")}>
                              <span className="font-medium capitalize">{t.role}:</span> {t.text}
                            </p>
                          ))}
                          {c.latest.workflow_run_id && (
                            <Link href={`/workflow/${workflowId}/run/${c.latest.workflow_run_id}`} className="mt-2 inline-block font-medium underline-offset-4 hover:underline">
                              Open the run
                            </Link>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </>
  );
}
