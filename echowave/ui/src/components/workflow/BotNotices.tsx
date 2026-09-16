"use client";

/**
 * Choosing what this bot interrupts you about.
 *
 * The bell and its inbox have existed for a while, and everything in them
 * is money or the account -- a low balance, an auto top-up, a number's
 * rent. Nothing in them was about what a bot *did*, which is the thing an
 * owner wants told while they are not looking at the screen.
 *
 * The offer comes from the server rather than being written out here. The
 * list is the set of events the runtime actually emits, and a checkbox this
 * screen invented would be one somebody ticks and then waits forever for.
 *
 * Each row says what it means, not just what it is called. "needs_secret"
 * is not a thing anybody can decide about; "it is waiting on a credential"
 * is.
 */

import { Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    getBotNoticesApiV1WorkflowWorkflowIdNoticesGet,
    setBotNoticesApiV1WorkflowWorkflowIdNoticesPut,
} from "@/client/sdk.gen";
import type { NoticeOption } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";

export function BotNotices({ workflowId }: { workflowId: number }) {
    const [offered, setOffered] = useState<NoticeOption[]>([]);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [saved, setSaved] = useState(false);

    const load = useCallback(async () => {
        const result = await getBotNoticesApiV1WorkflowWorkflowIdNoticesGet({
            path: { workflow_id: workflowId },
        });
        setLoading(false);
        // The generated client resolves on a 4xx rather than throwing, so an
        // unchecked result renders an empty list for a request that failed --
        // which reads as "this bot can tell you nothing".
        if (result.error) {
            setError(detailFromResult(result, "Could not read what this bot tells you"));
            return;
        }
        setError(null);
        setOffered(result.data?.offered ?? []);
        setSelected(new Set(result.data?.selected ?? []));
    }, [workflowId]);

    useEffect(() => {
        void load();
    }, [load]);

    const toggle = (kind: string) => {
        setSaved(false);
        setSelected((current) => {
            const next = new Set(current);
            if (next.has(kind)) next.delete(kind);
            else next.add(kind);
            return next;
        });
    };

    const save = async () => {
        setSaving(true);
        setError(null);
        const result = await setBotNoticesApiV1WorkflowWorkflowIdNoticesPut({
            path: { workflow_id: workflowId },
            body: { kinds: [...selected] },
        });
        setSaving(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not save that"));
            return;
        }
        // Taken from the response, not from what was sent: the server decides
        // the order and drops anything it will not honour, and a screen that
        // showed the request would claim a setting the server refused.
        setSelected(new Set(result.data?.selected ?? []));
        setSaved(true);
    };

    if (loading) {
        return <p className="text-sm text-muted-foreground">Loading…</p>;
    }

    return (
        <section className="space-y-3" data-testid="bot-notices">
            <div>
                <h2 className="text-base font-semibold">Tell me when</h2>
                <p className="text-sm text-muted-foreground">
                    What this bot puts in your notifications. Everything it does is on
                    its History either way — this is only what interrupts you.
                </p>
            </div>

            {error ? (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            ) : null}

            <ul className="space-y-2">
                {offered.map((option) => (
                    <li key={option.kind}>
                        <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-border p-3 transition-colors hover:bg-muted/40">
                            <input
                                type="checkbox"
                                className="mt-0.5 h-4 w-4"
                                checked={selected.has(option.kind)}
                                onChange={() => toggle(option.kind)}
                            />
                            <span>
                                <span className="block text-sm font-medium">
                                    {option.label}
                                </span>
                                <span className="block text-xs text-muted-foreground">
                                    {option.when}
                                </span>
                            </span>
                        </label>
                    </li>
                ))}
            </ul>

            <div className="flex items-center gap-2">
                <Button size="sm" disabled={saving} onClick={() => void save()}>
                    {saving ? (
                        <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
                    ) : null}
                    Save
                </Button>
                {saved ? (
                    <span className="text-xs text-muted-foreground">Saved.</span>
                ) : null}
                {selected.size === 0 ? (
                    // Said out loud, because an empty list is a real choice and
                    // looks identical to a screen that failed to load one.
                    <span className="text-xs text-muted-foreground">
                        This bot will not notify you about anything.
                    </span>
                ) : null}
            </div>
        </section>
    );
}
