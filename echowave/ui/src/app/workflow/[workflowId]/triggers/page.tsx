/**
 * What rings this bot from outside (KAN-137).
 *
 * The operator writes a sentence — "when a Shopify order over Rs 5,000 comes
 * in, check stock and message the customer" — and the builder model turns it
 * into a plan: a name, the fields the event should carry, a filter, and
 * what the bot is told each time. If the sentence leaves out something the
 * plan needs, the screen asks; it never errors. Only a plan with no open
 * questions can be saved, and the server enforces the same rule.
 *
 * Each saved trigger is a URL and a secret to paste into the sending system.
 * There is no field picker and no condition editor: that is n8n, and the
 * whole point is that the sentence is the configuration.
 */

"use client";

import { Copy, Loader2, Play, RefreshCw, Trash2, Zap } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AgentHeader } from "@/app/workflow/[workflowId]/components/AgentHeader";
import { AgentTabs } from "@/app/workflow/[workflowId]/components/AgentTabs";
import {
    compileTriggerApiV1WorkflowsWorkflowIdTriggersCompilePost,
    createTriggerApiV1WorkflowsWorkflowIdTriggersPost,
    deleteTriggerApiV1WorkflowsWorkflowIdTriggersTriggerIdDelete,
    getWorkflowApiV1WorkflowFetchWorkflowIdGet,
    listTriggersApiV1WorkflowsWorkflowIdTriggersGet,
    rotateSecretApiV1WorkflowsWorkflowIdTriggersTriggerIdRotateSecretPost,
    setActiveApiV1WorkflowsWorkflowIdTriggersTriggerIdActivePost,
    testTriggerApiV1WorkflowsWorkflowIdTriggersTriggerIdTestPost,
} from "@/client/sdk.gen";
import type {
    TriggerCompileResponse,
    TriggerResponse,
} from "@/client/types.gen";
import SpinLoader from "@/components/SpinLoader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const EXAMPLES = [
    "When a Shopify order over Rs 5,000 comes in, check stock and message the customer",
    "When a Razorpay payment fails, send the customer a WhatsApp with a fresh link",
    "When a Google Form is submitted, reply to the person and add them to the sheet",
];

async function copy(text: string) {
    try {
        await navigator.clipboard.writeText(text);
    } catch {
        /* the button is a convenience; the text is on screen */
    }
}

export default function AgentTriggersPage() {
    const params = useParams();
    const workflowId = Number(params.workflowId);
    const { user, loading: authLoading, redirectToLogin } = useAuth();
    const hasFetched = useRef(false);

    const [name, setName] = useState("");
    const [triggers, setTriggers] = useState<TriggerResponse[]>([]);
    const [maxPerWorkflow, setMaxPerWorkflow] = useState(10);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // The sentence, the questions it raised, and the plan once they are answered.
    const [sentence, setSentence] = useState("");
    const [answers, setAnswers] = useState<Record<string, string>>({});
    const [plan, setPlan] = useState<TriggerCompileResponse | null>(null);
    const [compiling, setCompiling] = useState(false);
    const [saving, setSaving] = useState(false);

    const [testing, setTesting] = useState<number | null>(null);
    const [testNote, setTestNote] = useState<Record<number, string>>({});
    const [samples, setSamples] = useState<Record<number, string>>({});

    useEffect(() => {
        if (!authLoading && !user) redirectToLogin();
    }, [authLoading, user, redirectToLogin]);

    const reload = useCallback(async () => {
        const result = await listTriggersApiV1WorkflowsWorkflowIdTriggersGet({
            path: { workflow_id: workflowId },
        });
        if (result.error) {
            setError(detailFromResult(result, "Could not load triggers"));
            return;
        }
        setTriggers(result.data?.triggers ?? []);
        setMaxPerWorkflow(result.data?.max_per_workflow ?? 10);
    }, [workflowId]);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        const load = async () => {
            const workflowResult = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({
                path: { workflow_id: workflowId },
            });
            if (workflowResult.error) {
                setError(detailFromResult(workflowResult, "Could not load this agent"));
                setLoading(false);
                return;
            }
            setName(workflowResult.data?.name ?? "");
            await reload();
            setLoading(false);
        };
        load();
    }, [authLoading, user, workflowId, reload]);

    const compile = useCallback(async () => {
        if (!sentence.trim()) return;
        setCompiling(true);
        setError(null);
        const result = await compileTriggerApiV1WorkflowsWorkflowIdTriggersCompilePost({
            path: { workflow_id: workflowId },
            body: { sentence, answers },
        });
        setCompiling(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not read that sentence"));
            return;
        }
        setPlan(result.data ?? null);
    }, [sentence, answers, workflowId]);

    const save = useCallback(async () => {
        if (!plan || !plan.ready) return;
        setSaving(true);
        setError(null);
        const result = await createTriggerApiV1WorkflowsWorkflowIdTriggersPost({
            path: { workflow_id: workflowId },
            body: {
                name: plan.name,
                sentence,
                instruction: plan.instruction,
                fields: plan.fields ?? [],
                filter: plan.filter ?? [],
            },
        });
        setSaving(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not save the trigger"));
            return;
        }
        setPlan(null);
        setSentence("");
        setAnswers({});
        await reload();
    }, [plan, sentence, workflowId, reload]);

    const toggle = useCallback(
        async (trigger: TriggerResponse, active: boolean) => {
            const result = await setActiveApiV1WorkflowsWorkflowIdTriggersTriggerIdActivePost({
                path: { workflow_id: workflowId, trigger_id: trigger.id },
                query: { active },
            });
            if (result.error) {
                setError(detailFromResult(result, "Could not change the trigger"));
                return;
            }
            await reload();
        },
        [workflowId, reload],
    );

    const rotate = useCallback(
        async (trigger: TriggerResponse) => {
            const result =
                await rotateSecretApiV1WorkflowsWorkflowIdTriggersTriggerIdRotateSecretPost({
                    path: { workflow_id: workflowId, trigger_id: trigger.id },
                });
            if (result.error) {
                setError(detailFromResult(result, "Could not rotate the secret"));
                return;
            }
            await reload();
        },
        [workflowId, reload],
    );

    const remove = useCallback(
        async (trigger: TriggerResponse) => {
            if (!window.confirm(`Remove "${trigger.name}"? The sender will get 404 from now on.`))
                return;
            const result = await deleteTriggerApiV1WorkflowsWorkflowIdTriggersTriggerIdDelete({
                path: { workflow_id: workflowId, trigger_id: trigger.id },
            });
            if (result.error) {
                setError(detailFromResult(result, "Could not remove the trigger"));
                return;
            }
            await reload();
        },
        [workflowId, reload],
    );

    const test = useCallback(
        async (trigger: TriggerResponse) => {
            let payload: Record<string, unknown> = {};
            const raw = (samples[trigger.id] ?? "").trim();
            if (raw) {
                try {
                    payload = JSON.parse(raw);
                } catch {
                    setTestNote((n) => ({ ...n, [trigger.id]: "That sample is not valid JSON." }));
                    return;
                }
            }
            setTesting(trigger.id);
            const result = await testTriggerApiV1WorkflowsWorkflowIdTriggersTriggerIdTestPost({
                path: { workflow_id: workflowId, trigger_id: trigger.id },
                body: { payload },
            });
            setTesting(null);
            if (result.error) {
                setTestNote((n) => ({
                    ...n,
                    [trigger.id]: detailFromResult(result, "Could not start the test"),
                }));
                return;
            }
            const missing = result.data?.missing_fields ?? [];
            const note =
                (result.data?.detail ?? "") +
                (missing.length
                    ? ` The sample is missing: ${missing.join(", ")} — the bot will ask.`
                    : "");
            setTestNote((n) => ({ ...n, [trigger.id]: note }));
        },
        [samples, workflowId],
    );

    if (authLoading || loading) return <SpinLoader />;

    const atCap = triggers.length >= maxPerWorkflow;

    return (
        <>
            <AgentHeader workflowId={workflowId} name={name} />
            <AgentTabs workflowId={workflowId} />

            <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6">
                <h2 className="text-lg font-semibold">Triggers</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    What rings this bot from outside. Say when it should act and
                    what it should do; you get a web address to paste into the
                    system that sees the event. One credit per event it acts on;
                    events it ignores are free.
                </p>

                {error && (
                    <div className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        {error}
                    </div>
                )}

                {/* --- new trigger ------------------------------------------------ */}
                <Card className="mt-6">
                    <CardContent className="p-5">
                        <Label htmlFor="trigger-sentence" className="text-sm font-medium">
                            New trigger
                        </Label>
                        <Textarea
                            id="trigger-sentence"
                            className="mt-2"
                            rows={3}
                            placeholder={EXAMPLES[0]}
                            value={sentence}
                            disabled={atCap}
                            onChange={(e) => {
                                setSentence(e.target.value);
                                setPlan(null);
                                setAnswers({});
                            }}
                        />
                        {!sentence && !atCap && (
                            <div className="mt-2 flex flex-wrap gap-2">
                                {EXAMPLES.map((example) => (
                                    <button
                                        key={example}
                                        type="button"
                                        className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground hover:text-foreground"
                                        onClick={() => setSentence(example)}
                                    >
                                        {example.slice(0, 48)}…
                                    </button>
                                ))}
                            </div>
                        )}
                        {atCap && (
                            <p className="mt-2 text-xs text-muted-foreground">
                                This agent has {maxPerWorkflow} triggers, which is the
                                most it can keep track of. Remove one to add another.
                            </p>
                        )}

                        {plan && !plan.ready && (
                            <div className="mt-4 space-y-3 rounded-md border border-border bg-muted/30 p-4">
                                <p className="text-sm font-medium">
                                    A couple of things before this can be saved
                                </p>
                                {(plan.questions ?? []).map((q) => (
                                    <div key={q.field}>
                                        <Label htmlFor={`answer-${q.field}`} className="text-sm">
                                            {q.question}
                                        </Label>
                                        <Input
                                            id={`answer-${q.field}`}
                                            className="mt-1"
                                            value={answers[q.field] ?? ""}
                                            onChange={(e) =>
                                                setAnswers((a) => ({
                                                    ...a,
                                                    [q.field]: e.target.value,
                                                }))
                                            }
                                        />
                                    </div>
                                ))}
                            </div>
                        )}

                        {plan && plan.ready && (
                            <div className="mt-4 space-y-2 rounded-md border border-border bg-muted/30 p-4 text-sm">
                                <p>
                                    <span className="font-medium">{plan.name}</span>
                                </p>
                                <p className="text-muted-foreground">
                                    Acts when: {plan.filter_summary || "every event"}
                                </p>
                                {(plan.fields ?? []).length > 0 && (
                                    <p className="text-muted-foreground">
                                        Expects:{" "}
                                        {(plan.fields ?? [])
                                            .map((f) => (f.required ? `${f.name}*` : f.name))
                                            .join(", ")}
                                    </p>
                                )}
                                <p className="whitespace-pre-wrap">{plan.instruction}</p>
                                {plan.note && (
                                    <p className="text-xs text-muted-foreground">{plan.note}</p>
                                )}
                            </div>
                        )}

                        <div className="mt-4 flex flex-wrap gap-2">
                            <Button
                                variant={plan?.ready ? "outline" : "default"}
                                disabled={compiling || !sentence.trim() || atCap}
                                onClick={compile}
                            >
                                {compiling ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : (
                                    <Zap className="mr-2 h-4 w-4" />
                                )}
                                {plan && !plan.ready ? "Answer and continue" : plan ? "Redo" : "Work it out"}
                            </Button>
                            {plan?.ready && (
                                <Button disabled={saving} onClick={save}>
                                    {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                    Save trigger
                                </Button>
                            )}
                        </div>
                    </CardContent>
                </Card>

                {/* --- existing --------------------------------------------------- */}
                {triggers.length === 0 ? (
                    <p className="mt-6 text-sm text-muted-foreground">
                        No triggers yet. The first one takes a sentence.
                    </p>
                ) : (
                    <div className="mt-6 space-y-4">
                        {triggers.map((trigger) => (
                            <Card key={trigger.id}>
                                <CardContent className="p-5">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0">
                                            <p className="font-medium">{trigger.name}</p>
                                            <p className="mt-0.5 text-xs text-muted-foreground">
                                                Acts when {trigger.filter_summary || "every event"}
                                                {" · "}
                                                {trigger.fired_count === 0
                                                    ? "never fired"
                                                    : `fired ${trigger.fired_count} time${trigger.fired_count === 1 ? "" : "s"}`}
                                            </p>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <Label
                                                htmlFor={`active-${trigger.id}`}
                                                className="text-xs text-muted-foreground"
                                            >
                                                {trigger.is_active ? "On" : "Paused"}
                                            </Label>
                                            <Switch
                                                id={`active-${trigger.id}`}
                                                checked={trigger.is_active}
                                                onCheckedChange={(on) => toggle(trigger, on)}
                                            />
                                        </div>
                                    </div>

                                    <p className="mt-3 text-sm text-muted-foreground italic">
                                        “{trigger.sentence || trigger.instruction}”
                                    </p>

                                    <div className="mt-4 grid gap-2 text-xs">
                                        <div className="flex items-center gap-2">
                                            <span className="w-16 shrink-0 text-muted-foreground">
                                                POST to
                                            </span>
                                            <code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1">
                                                {trigger.url}
                                            </code>
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                aria-label="Copy address"
                                                onClick={() => copy(trigger.url)}
                                            >
                                                <Copy className="h-3.5 w-3.5" />
                                            </Button>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <span className="w-16 shrink-0 text-muted-foreground">
                                                Secret
                                            </span>
                                            <code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1">
                                                {trigger.secret}
                                            </code>
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                aria-label="Copy secret"
                                                onClick={() => copy(trigger.secret)}
                                            >
                                                <Copy className="h-3.5 w-3.5" />
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                aria-label="New secret"
                                                onClick={() => rotate(trigger)}
                                            >
                                                <RefreshCw className="h-3.5 w-3.5" />
                                            </Button>
                                        </div>
                                        <p className="text-muted-foreground">
                                            Send the secret as the{" "}
                                            <code>X-Trigger-Secret</code> header, or add{" "}
                                            <code>?key=…</code> to the address if the sender
                                            cannot set headers.
                                        </p>
                                    </div>

                                    <details className="mt-4">
                                        <summary className="cursor-pointer text-xs text-muted-foreground">
                                            Try it with a sample event
                                        </summary>
                                        <Textarea
                                            className="mt-2 font-mono text-xs"
                                            rows={4}
                                            placeholder='{"total": 6200, "customer": {"phone": "98…"}}'
                                            value={samples[trigger.id] ?? ""}
                                            onChange={(e) =>
                                                setSamples((s) => ({
                                                    ...s,
                                                    [trigger.id]: e.target.value,
                                                }))
                                            }
                                        />
                                        <div className="mt-2 flex items-center gap-3">
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                disabled={testing === trigger.id}
                                                onClick={() => test(trigger)}
                                            >
                                                {testing === trigger.id ? (
                                                    <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                                                ) : (
                                                    <Play className="mr-2 h-3.5 w-3.5" />
                                                )}
                                                Fire once
                                            </Button>
                                            {testNote[trigger.id] && (
                                                <span className="text-xs text-muted-foreground">
                                                    {testNote[trigger.id]}
                                                </span>
                                            )}
                                        </div>
                                    </details>

                                    <div className="mt-4 flex justify-end">
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            className="text-destructive"
                                            onClick={() => remove(trigger)}
                                        >
                                            <Trash2 className="mr-2 h-3.5 w-3.5" />
                                            Remove
                                        </Button>
                                    </div>
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                )}
            </div>
        </>
    );
}
