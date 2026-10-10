"use client";

/**
 * "Learning", one line on an agent's side panel: how many of its suggestions
 * the owner approved and rejected this week, and the workspace's switch for
 * keeping that feedback to improve its agents.
 *
 * Counts and a switch, nothing else: no text of what was kept and no prices.
 * The switch is the workspace's, not this agent's -- it is the same
 * everywhere -- and it says so, along with what turning it off does. Only a
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
import { Switch } from "@/components/ui/switch";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";

type Counts = { approved: number; rejected: number; useFeedback: boolean };

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
    });
  }, [workflowId]);

  useEffect(() => {
    if (!on || authLoading || !user || fetched.current) return;
    fetched.current = true;
    void load();
  }, [on, authLoading, user, load]);

  if (!on || counts === null) return null;

  const change = async (next: boolean) => {
    setSaving(true);
    const res = await setSettingsApiV1TrainingLoopSettingsPut({ body: { use_feedback: next } });
    setSaving(false);
    if (res.error) {
      toast.error(detailFromError(res.error, "Could not change that"));
      return;
    }
    setCounts({ ...counts, useFeedback: next });
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
          checked={counts.useFeedback}
          disabled={saving}
          onCheckedChange={(next) => void change(next)}
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
            For all your agents. Kept only in this workspace and never shared. Turning it off also clears what was
            kept so far.
          </p>
        </div>
      </div>
    </section>
  );
}

export default AgentLearning;
