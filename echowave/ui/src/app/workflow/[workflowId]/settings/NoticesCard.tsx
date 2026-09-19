"use client";

/**
 * Which of this bot's events are worth interrupting somebody for.
 *
 * The bell and its inbox have been there a while and everything in them is
 * about money — a low balance, an auto top-up, a number's rent. Nothing in
 * them is about what a bot *did*, which is what an owner actually wants told
 * while they are not looking at the screen. The runtime has been reading a
 * per-bot selection for a while (`services/workflow/bot_notices.py`, with the
 * routes to go with it); nothing on any screen let anybody choose it, so
 * every bot has been running on the defaults and nobody could say so.
 *
 * The offer comes from the server rather than being listed here, so a kind
 * the runtime cannot emit can never appear as a checkbox, and a kind added
 * there shows up here without a second edit. An offered checkbox that can
 * never fire is worse than no checkbox.
 *
 * Saves on the tick. This is one boolean per row with no relationship
 * between rows, so a Save button would only be a second thing to press and a
 * way to lose the change by leaving.
 */

import { AlertTriangle, Bell, Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    getBotNoticesApiV1WorkflowWorkflowIdNoticesGet,
    setBotNoticesApiV1WorkflowWorkflowIdNoticesPut,
} from "@/client/sdk.gen";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";

type Offer = { kind: string; label: string; when: string; default: boolean };

export function NoticesCard({ workflowId }: { workflowId: number }) {
    const [offered, setOffered] = useState<Offer[]>([]);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            const response = await getBotNoticesApiV1WorkflowWorkflowIdNoticesGet({
                path: { workflow_id: workflowId },
            });
            if (cancelled) return;
            if (response.error || !response.data) {
                setError("Could not read what this bot tells you.");
            } else {
                const body = response.data as unknown as {
                    offered: Offer[];
                    selected: string[];
                };
                setOffered(body.offered ?? []);
                setSelected(new Set(body.selected ?? []));
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [workflowId]);

    const toggle = useCallback(
        async (kind: string, on: boolean) => {
            // Optimistic, and put back if the server refuses: a switch that
            // waits on a round trip reads as broken on a slow connection.
            const before = new Set(selected);
            const next = new Set(selected);
            if (on) next.add(kind);
            else next.delete(kind);
            setSelected(next);
            setSaving(kind);
            setError(null);

            const response = await setBotNoticesApiV1WorkflowWorkflowIdNoticesPut({
                path: { workflow_id: workflowId },
                body: { kinds: [...next] },
            });
            if (response.error) {
                setSelected(before);
                setError("That did not save. Try again.");
            }
            setSaving(null);
        },
        [selected, workflowId],
    );

    return (
        <Card id="notices">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Bell className="h-4 w-4 text-[var(--accent-brand)]" aria-hidden />
                    Tell me when
                </CardTitle>
                <CardDescription>
                    What this bot rings the bell for. The rest still lands in its
                    thread — this is only what is worth interrupting you for.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-1">
                {loading && (
                    <p className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                        Reading…
                    </p>
                )}

                {error && (
                    <p
                        className="flex items-center gap-2 py-2 text-sm text-destructive"
                        role="alert"
                    >
                        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
                        {error}
                    </p>
                )}

                {offered.map((offer) => (
                    <label
                        key={offer.kind}
                        className="flex cursor-pointer items-start justify-between gap-4 rounded-lg px-2 py-2.5 transition-colors hover:bg-muted/40"
                    >
                        <span className="min-w-0">
                            <span className="block text-sm font-medium">{offer.label}</span>
                            <span className="block text-sm text-muted-foreground">
                                {offer.when}
                            </span>
                        </span>
                        <Switch
                            checked={selected.has(offer.kind)}
                            disabled={saving !== null}
                            onCheckedChange={(on) => void toggle(offer.kind, on)}
                            aria-label={offer.label}
                        />
                    </label>
                ))}

                {!loading && !error && offered.length === 0 && (
                    <p className="py-2 text-sm text-muted-foreground">
                        Nothing to choose from yet.
                    </p>
                )}
            </CardContent>
        </Card>
    );
}

export default NoticesCard;
