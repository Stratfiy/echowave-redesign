"use client";

/**
 * The owner's way to close the workspace and have its data deleted.
 *
 * Seven days between asking and deleting, shown as a date, with a cancel
 * button for the whole of it: closing is irreversible, and one click from a
 * settings page is exactly where a mistaken or hostile one happens. Confirmed
 * by typing the workspace's name, because a button alone is too easy to press.
 *
 * Only owners see it. Anyone else gets a 403 from the server and the card
 * draws nothing -- the server decides, not this component.
 */

import { AlertTriangle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromResult } from "@/lib/apiError";

const URL = "/api/v1/privacy/workspace/closure";

type Scheduled = { id: number; requested_at: string; deletes_at: string };
type Closure = {
    scheduled: Scheduled | null;
    confirmation_phrase: string;
    grace_days: number;
};

function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString("en-IN", {
        day: "numeric",
        month: "long",
        year: "numeric",
    });
}

export function DeleteWorkspaceCard() {
    const [state, setState] = useState<Closure | null>(null);
    const [typed, setTyped] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        const response = await client.get({ url: URL });
        if (response.data) setState(response.data as unknown as Closure);
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    if (!state) return null;

    const schedule = async () => {
        setBusy(true);
        setError(null);
        const response = await client.post({ url: URL, body: { confirm: typed } });
        setBusy(false);
        if (response.error) {
            setError(detailFromResult(response, "Could not schedule the deletion."));
            return;
        }
        setTyped("");
        await load();
    };

    const cancel = async () => {
        setBusy(true);
        setError(null);
        const response = await client.delete({ url: URL });
        setBusy(false);
        if (response.error) {
            setError(detailFromResult(response, "Could not cancel the deletion."));
            return;
        }
        await load();
    };

    return (
        <Card className="border-destructive/40">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-destructive">
                    <AlertTriangle className="h-5 w-5" aria-hidden />
                    Delete this workspace
                </CardTitle>
                <CardDescription>
                    Deletes every call recording and transcript, contact, knowledge
                    file, connected app and your agents&apos; memory, and signs everyone
                    out of this workspace. Invoices and payment records are kept, as
                    the law requires. Encrypted backups expire within 30 days.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                {error && (
                    <div
                        role="alert"
                        className="rounded-[var(--radius-control)] border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
                    >
                        {error}
                    </div>
                )}

                {state.scheduled ? (
                    <div className="space-y-3">
                        <p className="text-sm" aria-live="polite">
                            This workspace will be deleted on{" "}
                            <strong>{formatDate(state.scheduled.deletes_at)}</strong>.
                            Your agents keep working until then.
                        </p>
                        <Button
                            variant="outline"
                            onClick={() => void cancel()}
                            disabled={busy}
                        >
                            Cancel the deletion
                        </Button>
                    </div>
                ) : (
                    <div className="space-y-3">
                        <div className="space-y-2">
                            <Label htmlFor="delete-workspace-confirm">
                                Type <strong>{state.confirmation_phrase}</strong> to
                                confirm
                            </Label>
                            <Input
                                id="delete-workspace-confirm"
                                value={typed}
                                onChange={(event) => setTyped(event.target.value)}
                                autoComplete="off"
                            />
                        </div>
                        <Button
                            variant="destructive"
                            onClick={() => void schedule()}
                            disabled={busy || typed.trim() !== state.confirmation_phrase}
                        >
                            Delete in {state.grace_days} days
                        </Button>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
