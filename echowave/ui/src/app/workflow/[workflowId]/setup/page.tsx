"use client";

/**
 * The form that stands in for the canvas.
 *
 * The product claim is that a non-technical seller gets an agent live in ten
 * minutes without opening a node editor. This page is that claim: the
 * questions come from the agent's own prompts, and answering them is the whole
 * job.
 *
 * The questions are derived server-side rather than listed here, so an agent
 * built from a template nobody has classified still produces a usable form
 * instead of an empty one.
 */

import { ArrowLeft, Loader2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  getAgentSetupApiV1WorkflowWorkflowIdSetupGet,
  updateWorkflowApiV1WorkflowWorkflowIdPut,
} from "@/client/sdk.gen";
import type { SetupFieldResponse } from "@/client/types.gen";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

export default function AgentSetupPage() {
  const params = useParams();
  const router = useRouter();
  const { user, loading: authLoading, getAccessToken } = useAuth();
  const hasFetched = useRef(false);
  const workflowId = Number(params.workflowId);

  const [name, setName] = useState("");
  const [fields, setFields] = useState<SetupFieldResponse[] | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const token = await getAccessToken();
    const res = await getAgentSetupApiV1WorkflowWorkflowIdSetupGet({
      headers: { Authorization: `Bearer ${token}` },
      path: { workflow_id: workflowId },
    });
    if (res.error || !res.data) {
      toast.error(detailFromError(res.error, "Could not load this agent's setup"));
      return;
    }
    setName(res.data.workflow_name);
    setFields(res.data.fields);
    setValues(
      Object.fromEntries(res.data.fields.map((f) => [f.name, f.value ?? ""])),
    );
  }, [getAccessToken, workflowId]);

  // The auth interceptor that attaches the bearer token is only registered
  // once auth has loaded. Fetching before that sends an unauthenticated
  // request that fails silently — the form would just stay on its skeleton
  // with nothing in the console to say why.
  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    void load();
  }, [authLoading, user, load]);

  // Required and still blank. Recomputed from what is typed rather than from
  // the server's answer, so the button releases as soon as the last box is
  // filled instead of after a round trip.
  const missing = (fields ?? []).filter(
    (f) => f.required && !(values[f.name] ?? "").trim(),
  );

  async function save() {
    setSaving(true);
    try {
      const token = await getAccessToken();
      const res = await updateWorkflowApiV1WorkflowWorkflowIdPut({
        headers: { Authorization: `Bearer ${token}` },
        path: { workflow_id: workflowId },
        body: { template_context_variables: values },
      });
      if (res.error) {
        toast.error(detailFromError(res.error, "Could not save. Try again."));
        return;
      }
      toast.success("Saved");
      router.push(`/workflow/${workflowId}`);
    } catch {
      toast.error("Could not save. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl p-6">
      <Link
        href={`/workflow/${workflowId}`}
        className="mb-6 inline-flex items-center text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="mr-1 h-4 w-4" />
        Back to agent
      </Link>

      <h1 className="text-2xl font-semibold">Set up {name || "this agent"}</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        The agent says these out loud. Write them the way you would say them.
      </p>

      {fields === null ? (
        <div className="mt-8 space-y-6">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : fields.length === 0 ? (
        <p className="mt-8 rounded-lg border border-border p-8 text-center text-sm text-muted-foreground">
          This agent has nothing to fill in — it is ready to take calls.
        </p>
      ) : (
        <>
          <div className="mt-8 space-y-6">
            {fields.map((field) => (
              <div key={field.name} className="space-y-1.5">
                <Label htmlFor={field.name}>
                  {field.label}
                  {!field.required ? (
                    <span className="ml-2 text-xs font-normal text-muted-foreground">
                      optional
                    </span>
                  ) : null}
                </Label>
                <Input
                  id={field.name}
                  value={values[field.name] ?? ""}
                  onChange={(e) =>
                    setValues((v) => ({ ...v, [field.name]: e.target.value }))
                  }
                />
                {field.hint ? (
                  <p className="text-xs text-muted-foreground">{field.hint}</p>
                ) : null}
              </div>
            ))}
          </div>

          <div className="mt-8 flex items-center gap-3">
            <Button onClick={() => void save()} disabled={saving || missing.length > 0}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Save
            </Button>
            {missing.length > 0 ? (
              <p className="text-sm text-muted-foreground">
                {/* Named rather than counted. "2 fields remaining" makes
                    someone hunt for which two. */}
                Still needed: {missing.map((f) => f.label).join(", ")}
              </p>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}
