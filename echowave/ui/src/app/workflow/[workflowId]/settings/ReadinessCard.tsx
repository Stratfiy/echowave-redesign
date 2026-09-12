"use client";

/**
 * What this agent still needs before it can do its job.
 *
 * Deliberately not a setup wizard. A wizard is completed once and then lies:
 * a token revoked three months later leaves a green tick over an agent that
 * has silently stopped filing anything. This is read live every time the
 * screen opens, so one list answers both "what is left to set up" and "why did
 * it stop working" — which are the same question to the business and different
 * questions to whoever fixes it.
 *
 * Renders nothing when the agent needs nothing outside itself. A checklist of
 * items that cannot fail teaches an operator to stop reading it.
 */

import { AlertTriangle, Check, ExternalLink } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    agentReadinessApiV1WorkflowWorkflowIdReadinessGet,
    startConnectingApiV1ConnectorsSlugConnectPost,
} from "@/client/sdk.gen";
import type { ReadinessItem } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

function Row({ item, onChanged }: { item: ReadinessItem; onChanged: () => void }) {
    const [busy, setBusy] = useState(false);

    const connect = async () => {
        setBusy(true);
        try {
            const response = await startConnectingApiV1ConnectorsSlugConnectPost({
                path: { slug: item.app },
            });
            const url = response.data?.connect_url;
            if (url) {
                // A new tab, not a redirect: they are mid-way through setting an
                // agent up and should come back to where they were.
                window.open(url, "_blank", "noopener");
                onChanged();
            }
        } catch {
            // The row stays as it was; the list re-reads on the next open.
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="flex items-start justify-between gap-3 border-b py-3 last:border-0">
            <div className="space-y-0.5">
                <div className="flex items-center gap-2 text-sm">
                    {item.status === "ready" ? (
                        <Check className="h-4 w-4 text-emerald-600" />
                    ) : (
                        <AlertTriangle
                            className={
                                item.status === "failing"
                                    ? "h-4 w-4 text-amber-600"
                                    : "h-4 w-4 text-destructive"
                            }
                        />
                    )}
                    <span className="font-medium">{item.label}</span>
                </div>
                <p className="pl-6 text-xs text-muted-foreground">
                    {item.status === "missing"
                        ? "Not connected yet."
                        : item.status === "failing"
                          ? `Connected, but ${item.recent_failures} calls failed this week. It may need reconnecting.`
                          : "Working."}{" "}
                    Used by {item.needed_by.join(", ")}.
                </p>
            </div>

            {item.connectable && item.status !== "ready" ? (
                <Button size="sm" variant="outline" onClick={connect} disabled={busy}>
                    {item.status === "failing" ? "Reconnect" : "Connect"}
                    <ExternalLink className="ml-1 h-3 w-3" />
                </Button>
            ) : null}
        </div>
    );
}

export function ReadinessCard({ workflowId }: { workflowId: number }) {
    const [items, setItems] = useState<ReadinessItem[] | null>(null);
    const [ready, setReady] = useState(true);

    const load = useCallback(async () => {
        try {
            const response = await agentReadinessApiV1WorkflowWorkflowIdReadinessGet({
                path: { workflow_id: workflowId },
            });
            setItems(response.data?.items ?? []);
            setReady(response.data?.ready ?? true);
        } catch {
            setItems([]);
        }
    }, [workflowId]);

    useEffect(() => {
        void load();
    }, [load]);

    if (!items || items.length === 0) return null;

    return (
        <Card id="readiness">
            <CardHeader>
                <CardTitle className="text-base">
                    {ready ? "Everything it needs is connected" : "Before this can work"}
                </CardTitle>
                <CardDescription>
                    The outside apps this agent depends on, during a call and after one.
                </CardDescription>
            </CardHeader>
            <CardContent className="py-0">
                {items.map((item) => (
                    <Row key={item.app} item={item} onChanged={() => void load()} />
                ))}
            </CardContent>
        </Card>
    );
}
