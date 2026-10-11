"use client";

/**
 * "Learning", one line on an agent's side panel: how many of its suggestions
 * the owner approved and rejected this week, and the workspace's switch for
 * keeping that feedback to improve its agents.
 *
 * Counts and a switch, nothing else: no text of what was kept and no prices.
 * The switch is the workspace's, not this agent's -- it is the same
 * everywhere -- and it says so. Turning it off is a choice of two, never a
 * default: "Stop collecting" keeps what was kept and collects nothing new;
 * "Stop and delete" also archives what was kept (out of export and training,
 * held only as long as the law requires, never hard-deleted). Only a
 * workspace admin can change it; anyone else is told so and it stays as it was.
 *
 * Shown only while `training_loop` is on.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  agentSummaryApiV1TrainingLoopAgentsWorkflowIdSummaryGet,
  setSettingsApiV1TrainingLoopSettingsPut,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";

type Counts = { approved: number; rejected: number; useFeedback: boolean; archived: number };

export function learningLine(approved: number, rejected: number): string {
  if (approved === 0 && rejected === 0) return "No suggestions answered yet this week";
  const part = (n: number, verb: string) => `${n} ${n === 1 ? "suggestion" : "suggestions"} ${verb}`;
  return `${part(approved, "approved")}, ${rejected} rejected this week`;
}

export function AgentLearning({ workflowId }: { workflowId: number }) {
  const on = useFeature("training_loop");
  const { user, loading: authLoading } = useAuth();
  const [counts, setCounts] = useState<Counts | null>(null);
  const [saving, setSaving] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const fetched = useRef(false);

  const load = useCallback(async () => {
    const res = await agentSummaryApiV1TrainingLoopAgentsWorkflowIdSummaryGet({
      path: { workflow_id: workflowId },
    });
    if (res.error || !res.data) return;
    setCounts({
      approved: res.data.approved_this_week,
      rejected: res.data.rejected_this_week,
      useFeedback: res.data.use_feedback,
      archived: 0,
    });
  }, [workflowId]);

  useEffect(() => {
    if (!on || authLoading || !user || fetched.current) return;
    fetched.current = true;
    void load();
  }, [on, authLoading, user, load]);

  if (!on || counts === null) return null;

  const change = async (next: boolean, deletePast = false) => {
    setSaving(true);
    const res = await setSettingsApiV1TrainingLoopSettingsPut({
      body: next ? { use_feedback: true } : { use_feedback: false, delete_past: deletePast },
    });
    setSaving(false);
    if (res.error) {
      toast.error(detailFromError(res.error, "Could not change that"));
      setChoosing(false);
      return;
    }
    setChoosing(false);
    setCounts({
      ...counts,
      useFeedback: next,
      archived: res.data?.archived ?? counts.archived,
    });
  };

  // Turning it on is one click; turning it off asks which kind of off.
  const toggle = (next: boolean) => {
    if (next) void change(true);
    else setChoosing(true);
  };

  return (
    <section aria-labelledby="agent-learning" className="space-y-2" data-testid="agent-learning">
      <h3 id="agent-learning" className="text-[13px] font-normal text-muted-foreground">
        Learning
      </h3>
      <p className="text-[13px]" data-testid="learning-line">
        {learningLine(counts.approved, counts.rejected)}
      </p>
      <div className="flex items-start gap-3">
        <Switch
          id={`learning-consent-${workflowId}`}
          checked={counts.useFeedback && !choosing}
          disabled={saving}
          onCheckedChange={toggle}
          aria-describedby={`learning-consent-note-${workflowId}`}
        />
        <div className="min-w-0 space-y-0.5">
          <label htmlFor={`learning-consent-${workflowId}`} className="block text-[13px]">
            Use my feedback to improve my agents
          </label>
          <p
            id={`learning-consent-note-${workflowId}`}
            className="text-[12px] leading-snug text-muted-foreground"
          >
            For all your agents. Kept only in this workspace and never shared.
          </p>
        </div>
      </div>
      {choosing && (
        <div className="space-y-2 rounded-md border border-border p-2" role="group" aria-label="Turn off" data-testid="learning-choice">
          <p className="text-[12px] leading-snug text-muted-foreground">
            <strong className="font-medium text-foreground">Stop collecting</strong> keeps what was kept so far and
            collects nothing new. <strong className="font-medium text-foreground">Stop and delete</strong> also
            archives what was kept: it is left out of exports and training, and held only as long as the law
            requires.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={saving} onClick={() => void change(false, false)}>
              Stop collecting
            </Button>
            <Button size="sm" variant="destructive" disabled={saving} onClick={() => void change(false, true)}>
              Stop and delete
            </Button>
            <Button size="sm" variant="ghost" disabled={saving} onClick={() => setChoosing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {!counts.useFeedback && !choosing && (
        <p className="text-[12px] leading-snug text-muted-foreground" data-testid="learning-off">
          Nothing new is being collected.
          {counts.archived > 0 ? ` ${counts.archived} kept earlier are archived.` : ""}{" "}
          <button
            type="button"
            className="underline underline-offset-4"
            disabled={saving}
            onClick={() => void change(false, true)}
          >
            Archive what was kept
          </button>
        </p>
      )}
    </section>
  );
}

export default AgentLearning;
