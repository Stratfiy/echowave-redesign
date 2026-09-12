"use client";

/**
 * The agent a prospect hears before hiring a role.
 *
 * This is the switch that takes the shelf from empty to listed. A role that
 * makes or takes calls cannot be published without a way to be heard, because
 * a voice agent sold on a screenshot is sold on a promise — so with nothing
 * marked here, every calling role stays unlisted. An empty shelf is a missing
 * setting somebody notices; a shelf full of dead demo links is one nobody
 * reports.
 *
 * Two ways to reach it, both derived from the one flag: the share link, which
 * needs no telephony at all and whose text chat works on a locked-down network
 * where WebRTC never connects, and a number pointed at the same agent, which
 * is stronger proof for a product whose pitch is that it answers your phone.
 * Either is enough. Requiring the number would gate the whole shelf on a
 * telephony purchase.
 *
 * Takes a numeric agent id rather than offering a picker, for the same reason
 * the shared-outbound screen does: this is a rare, deliberate decision about
 * which of our own agents represents the product, not a self-serve flow, and
 * the id is one lookup away for whoever is making it.
 */

import { AlertTriangle, Check, Link2, Loader2, Phone, PhoneOff } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    readDemoAgentApiV1AdminTelephonyDemoAgentGet,
    setDemoAgentApiV1AdminTelephonyAgentsWorkflowIdDemoPost,
} from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

type DemoContact = {
    workflow_id?: string | null;
    name?: string | null;
    url?: string | null;
    number?: string | null;
};

export default function DemoAgentPage() {
    const { user, loading: authLoading } = useAuth();

    const [contact, setContact] = useState<DemoContact | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [agentId, setAgentId] = useState("");
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        const result = await readDemoAgentApiV1AdminTelephonyDemoAgentGet({});
        if (result.error) {
            setError(detailFromResult(result, "Failed to read the demo agent"));
        } else {
            setError(null);
            setContact((result.data as DemoContact) ?? {});
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        // The auth interceptor only attaches the bearer token once auth has
        // loaded; fetching earlier sends an unauthenticated request.
        if (authLoading || !user) return;
        void load();
    }, [authLoading, user, load]);

    const save = async (demo: boolean, id: number) => {
        setSaving(true);
        setSaveError(null);
        const result = await setDemoAgentApiV1AdminTelephonyAgentsWorkflowIdDemoPost({
            path: { workflow_id: id },
            body: { demo },
        });
        // The generated client resolves rather than throws on a 4xx, so the
        // 409s this route raises — a paused or archived agent — only surface if
        // the error is checked explicitly.
        if (result.error) {
            setSaveError(detailFromResult(result, "Could not change the demo agent"));
        } else {
            setAgentId("");
            await load();
        }
        setSaving(false);
    };

    const current = contact?.workflow_id ? Number(contact.workflow_id) : null;
    const reachable = Boolean(contact?.url || contact?.number);

    return (
        <div className="mx-auto max-w-3xl space-y-6 p-6">
            <div>
                <h1 className="text-2xl font-semibold">Demo agent</h1>
                <p className="mt-1 text-sm text-muted-foreground">
                    The agent a prospect hears before hiring a role. Until one is set,
                    every role that makes or takes calls stays unlisted on the shelf.
                </p>
            </div>

            {error && (
                <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                    {error}
                </p>
            )}

            <div className="rounded-lg border border-border bg-card p-4">
                {loading ? (
                    <div className="space-y-2">
                        <Skeleton className="h-5 w-40" />
                        <Skeleton className="h-4 w-64" />
                    </div>
                ) : current ? (
                    <div className="space-y-3">
                        <div className="flex items-center gap-2">
                            {reachable ? (
                                <Badge className="gap-1">
                                    <Check className="h-3 w-3" />
                                    Live
                                </Badge>
                            ) : (
                                <Badge variant="destructive" className="gap-1">
                                    <AlertTriangle className="h-3 w-3" />
                                    Unreachable
                                </Badge>
                            )}
                            {/* The name leads. "Agent #3" identifies the demo
                                to nobody -- whoever opens this page has to see
                                which agent a prospect will hear without going
                                and looking the id up. The id stays, quietly,
                                because it is what the field below takes. */}
                            <span className="text-sm font-medium">
                                {contact?.name || `Agent #${current}`}
                            </span>
                            {contact?.name ? (
                                <span className="text-xs text-muted-foreground">
                                    #{current}
                                </span>
                            ) : null}
                        </div>

                        <div className="space-y-1.5 text-sm">
                            <div className="flex items-center gap-2">
                                <Link2 className="h-4 w-4 text-muted-foreground" />
                                {contact?.url ? (
                                    <a
                                        href={contact.url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="underline"
                                    >
                                        {contact.url}
                                    </a>
                                ) : (
                                    <span className="text-muted-foreground">
                                        No share link — create an embed token on this
                                        agent and it appears here.
                                    </span>
                                )}
                            </div>
                            <div className="flex items-center gap-2">
                                <Phone className="h-4 w-4 text-muted-foreground" />
                                {contact?.number ? (
                                    <span>{contact.number}</span>
                                ) : (
                                    <span className="text-muted-foreground">
                                        No number — point one at this agent for inbound
                                        and it appears here.
                                    </span>
                                )}
                            </div>
                        </div>

                        {!reachable && (
                            <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                                A prospect cannot reach this agent, so the calling roles
                                are still unlisted. It needs a share link or a number.
                            </p>
                        )}

                        <Button
                            variant="outline"
                            size="sm"
                            disabled={saving}
                            onClick={() => void save(false, current)}
                        >
                            {saving ? (
                                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                            ) : (
                                <PhoneOff className="mr-1 h-3.5 w-3.5" />
                            )}
                            Stop using this as the demo
                        </Button>
                    </div>
                ) : (
                    <p className="text-sm text-muted-foreground">
                        No demo agent set, so every calling role is unlisted. Set one
                        below.
                    </p>
                )}
            </div>

            <div className="space-y-2 rounded-lg border border-border bg-card p-4">
                <Label htmlFor="agent-id">Agent id</Label>
                <div className="flex flex-wrap items-center gap-2">
                    <Input
                        id="agent-id"
                        value={agentId}
                        inputMode="numeric"
                        placeholder="e.g. 42"
                        className="w-40"
                        onChange={(event) =>
                            setAgentId(event.target.value.replace(/\D/g, ""))
                        }
                    />
                    <Button
                        disabled={!agentId || saving}
                        onClick={() => void save(true, Number(agentId))}
                    >
                        {saving && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                        Make this the demo
                    </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                    One demo at a time — setting this clears the previous one. A paused
                    or archived agent is refused: publishing a link to something that
                    answers nothing teaches every prospect that the demo is broken.
                </p>
                {saveError && (
                    <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                        {saveError}
                    </p>
                )}
            </div>
        </div>
    );
}
