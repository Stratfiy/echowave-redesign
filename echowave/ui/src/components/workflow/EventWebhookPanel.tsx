"use client";

/**
 * Where this bot sends what it did.
 *
 * The other direction from the triggers above. A trigger is a URL somebody
 * pastes into Shopify so an order rings this bot; this is a URL they paste
 * from n8n (or their own server) so the bot's outcomes reach whatever they
 * already run. Without it a bot is the end of a chain rather than a link in
 * one.
 *
 * The events offered come from the server, the same list the bell uses. A
 * checkbox invented here would be one somebody ticks and then waits forever
 * for -- and the endpoint accepts more kinds than this list shows, because
 * somebody wiring a pipeline by API knows what they want and curating is
 * this screen's job.
 *
 * Ticking nothing is not "send nothing": it is the default set, and the
 * screen says which events that means rather than leaving a blank field to
 * be interpreted.
 *
 * The secret is shown once, when it is created or rotated, and the copy is
 * the only one anybody gets. Said plainly, in the moment, because a value
 * that cannot be read back and is not marked as such is a support ticket.
 */

import { Copy, Loader2, Send, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    deleteEventWebhookApiV1WorkflowsWorkflowIdEventWebhookDelete,
    getBotNoticesApiV1WorkflowWorkflowIdNoticesGet,
    getEventWebhookApiV1WorkflowsWorkflowIdEventWebhookGet,
    saveEventWebhookApiV1WorkflowsWorkflowIdEventWebhookPut,
    testEventWebhookApiV1WorkflowsWorkflowIdEventWebhookTestPost,
} from "@/client/sdk.gen";
import type { NoticeOption } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromResult } from "@/lib/apiError";

export function EventWebhookPanel({ workflowId }: { workflowId: number }) {
    const [offered, setOffered] = useState<NoticeOption[]>([]);
    const [url, setUrl] = useState("");
    const [kinds, setKinds] = useState<Set<string>>(new Set());
    const [active, setActive] = useState(true);
    const [sending, setSending] = useState<string[]>([]);
    const [exists, setExists] = useState(false);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [note, setNote] = useState<string | null>(null);
    /** Shown once, on the save that minted it. Never fetched back. */
    const [secret, setSecret] = useState<string | null>(null);

    const load = useCallback(async () => {
        const [hook, notices] = await Promise.all([
            getEventWebhookApiV1WorkflowsWorkflowIdEventWebhookGet({
                path: { workflow_id: workflowId },
            }),
            getBotNoticesApiV1WorkflowWorkflowIdNoticesGet({
                path: { workflow_id: workflowId },
            }),
        ]);
        setLoading(false);
        // The generated client resolves on a 4xx rather than throwing, so an
        // unchecked result renders an empty form for a request that failed --
        // which reads as "this bot has no webhook".
        if (hook.error) {
            setError(detailFromResult(hook, "Could not read where this bot posts"));
            return;
        }
        if (!notices.error) setOffered(notices.data?.offered ?? []);
        const row = hook.data;
        if (!row) {
            setExists(false);
            return;
        }
        setExists(true);
        setUrl(row.url ?? "");
        setKinds(new Set(row.kinds ?? []));
        setActive(row.is_active ?? true);
        setSending(row.sending ?? []);
    }, [workflowId]);

    useEffect(() => {
        void load();
    }, [load]);

    const toggle = (kind: string) => {
        setKinds((was) => {
            const next = new Set(was);
            if (next.has(kind)) next.delete(kind);
            else next.add(kind);
            return next;
        });
    };

    const save = async (rotate = false) => {
        setBusy(true);
        setError(null);
        setNote(null);
        const result = await saveEventWebhookApiV1WorkflowsWorkflowIdEventWebhookPut({
            path: { workflow_id: workflowId },
            body: {
                url: url.trim(),
                kinds: [...kinds],
                is_active: active,
                rotate_secret: rotate,
            },
        });
        setBusy(false);
        if (result.error) {
            // The server's own words: it is the half that knows a URL points
            // at a private address, and paraphrasing that would lose the
            // reason.
            setError(detailFromResult(result, "Could not save that"));
            return;
        }
        setExists(true);
        setSending(result.data?.sending ?? []);
        if (result.data?.secret) setSecret(result.data.secret);
        else setNote("Saved.");
    };

    const test = async () => {
        setBusy(true);
        setError(null);
        setNote(null);
        const result = await testEventWebhookApiV1WorkflowsWorkflowIdEventWebhookTestPost({
            path: { workflow_id: workflowId },
        });
        setBusy(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not send a test"));
            return;
        }
        setNote(result.data?.detail || "Sent.");
    };

    const remove = async () => {
        if (!window.confirm("Stop sending this bot's events out?")) return;
        setBusy(true);
        setError(null);
        const result = await deleteEventWebhookApiV1WorkflowsWorkflowIdEventWebhookDelete({
            path: { workflow_id: workflowId },
        });
        setBusy(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not remove that"));
            return;
        }
        setExists(false);
        setUrl("");
        setKinds(new Set());
        setSecret(null);
        setNote("Removed. This bot posts nothing out now.");
    };

    if (loading) return null;

    return (
        <section className="space-y-3" data-testid="event-webhook-panel">
            <div>
                <h2 className="text-base font-semibold">Send its events somewhere</h2>
                <p className="text-sm text-muted-foreground">
                    Paste a URL from n8n, Zapier or your own server. When this bot files
                    an outcome or gets stuck, it POSTs there.
                </p>
            </div>

            {error && (
                <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                    {error}
                </p>
            )}
            {note && !secret && (
                <p className="text-sm text-muted-foreground">{note}</p>
            )}

            {secret && (
                <div className="rounded-md border border-border bg-muted/40 p-3">
                    <p className="text-sm font-medium">
                        Your signing secret — this is the only time it is shown.
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                        Your receiver uses it to check the X-Decibyl-Signature header, so
                        it knows a POST really came from us.
                    </p>
                    <div className="mt-2 flex items-center gap-2">
                        <code className="flex-1 break-all rounded bg-background px-2 py-1 text-xs">
                            {secret}
                        </code>
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                                void navigator.clipboard?.writeText(secret);
                            }}
                        >
                            <Copy className="mr-1 h-3.5 w-3.5" aria-hidden />
                            Copy
                        </Button>
                    </div>
                    <Button
                        size="sm"
                        variant="ghost"
                        className="mt-2"
                        onClick={() => setSecret(null)}
                    >
                        I have saved it
                    </Button>
                </div>
            )}

            <div className="space-y-3 rounded-lg border border-border bg-card p-4">
                <div>
                    <Label htmlFor="event-webhook-url">Where to post</Label>
                    <Input
                        id="event-webhook-url"
                        className="mt-1"
                        placeholder="https://your-n8n.example.com/webhook/decibyl"
                        value={url}
                        onChange={(e) => setUrl(e.target.value)}
                    />
                </div>

                <div>
                    <Label>What to send</Label>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                        Tick nothing and it sends the same events the bell does:{" "}
                        {sending.length ? sending.join(", ") : "the usual set"}.
                    </p>
                    <div className="mt-2 space-y-1.5">
                        {offered.map((option) => (
                            <label
                                key={option.kind}
                                className="flex items-start gap-2 text-sm"
                            >
                                <input
                                    type="checkbox"
                                    className="mt-1"
                                    checked={kinds.has(option.kind)}
                                    onChange={() => toggle(option.kind)}
                                />
                                <span>
                                    <span className="font-medium">{option.label}</span>
                                    {/* What it means, not just what it is called. */}
                                    <span className="block text-xs text-muted-foreground">
                                        {option.when}
                                    </span>
                                </span>
                            </label>
                        ))}
                    </div>
                </div>

                <label className="flex items-center gap-2 text-sm">
                    <input
                        type="checkbox"
                        checked={active}
                        onChange={(e) => setActive(e.target.checked)}
                    />
                    Sending is on
                </label>

                <div className="flex flex-wrap items-center gap-2">
                    {busy ? (
                        <Loader2
                            aria-label="Working"
                            className="h-4 w-4 animate-spin text-muted-foreground"
                        />
                    ) : null}
                    <Button size="sm" disabled={busy || !url.trim()} onClick={() => void save()}>
                        Save
                    </Button>
                    {exists && (
                        <>
                            <Button
                                size="sm"
                                variant="outline"
                                disabled={busy}
                                onClick={() => void test()}
                            >
                                <Send className="mr-1 h-3.5 w-3.5" aria-hidden />
                                Send a test
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                disabled={busy}
                                onClick={() => void save(true)}
                            >
                                New secret
                            </Button>
                            <Button
                                size="sm"
                                variant="ghost"
                                className="text-destructive hover:text-destructive"
                                disabled={busy}
                                onClick={() => void remove()}
                            >
                                <Trash2 className="mr-1 h-3.5 w-3.5" aria-hidden />
                                Remove
                            </Button>
                        </>
                    )}
                </div>
            </div>
        </section>
    );
}
