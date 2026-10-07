"use client";

/**
 * Commands waiting for a second person: the staff console's own, and the
 * ops stream's at /admin/ops/commands (shown as needs setup until that
 * stream is here). Approve and Turn down go to the server, which checks the
 * approver's role and refuses the person who asked; this only hides the
 * button from them so they are not invited to collect a refusal.
 */

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { staffPost, useStaffData } from "@/lib/staff/data";
import { when } from "@/lib/staff/format";

import type { CommandView } from "./CommandFlow";
import { PreviewValue } from "./CommandFlow";
import { Empty, Panel, StateBadge } from "./parts";
import { useStaffConsole } from "./StaffShell";

type Waiting = CommandView & { reason: string; created_at: string; expires_at: string | null };

export function ApprovalQueue() {
    const { me } = useStaffConsole();
    const staff = useStaffData<{ commands: Waiting[] }>("/api/v1/admin/staff/commands", { state: "awaiting_approval" }, 30_000);
    const ops = useStaffData<{ commands: Waiting[] }>("/api/v1/admin/ops/commands", { state: "awaiting_approval" });
    const [busy, setBusy] = useState<string | null>(null);
    const [message, setMessage] = useState<string | null>(null);

    async function decide(base: string, id: number, verb: "approve" | "reject", refresh: () => Promise<void>) {
        setBusy(`${base}${id}${verb}`);
        setMessage(null);
        const r = await staffPost<CommandView>(`${base}/${id}/${verb}`, verb === "reject" ? { note: "Turned down from the overview" } : {});
        setBusy(null);
        if (!r.ok) setMessage(r.error);
        else setMessage(`Command #${id} is now ${r.data.state.replace(/_/g, " ")}.`);
        await refresh();
    }

    return (
        <section id="commands" aria-label="Waiting for approval" className="space-y-2">
            <Panel title="Waiting for a second person" query={staff}>
                {(data) =>
                    data.commands.length === 0 ? (
                        <Empty>No staff command is waiting.</Empty>
                    ) : (
                        <ul className="divide-y divide-border">
                            {data.commands.map((c) => (
                                <li key={c.id} className="space-y-2 py-3" data-testid="waiting-command">
                                    <div className="flex flex-wrap items-center gap-2 text-sm">
                                        <span className="font-mono text-xs">#{c.id}</span>
                                        <span className="font-medium">{c.command}</span>
                                        <StateBadge state={c.state} />
                                        <span className="text-xs text-muted-foreground">asked {when(c.created_at)} · expires {when(c.expires_at)}</span>
                                    </div>
                                    <p className="text-sm">Reason: {c.reason}</p>
                                    {c.preview && (
                                        <div className="rounded-md border border-border bg-muted/30 p-2 text-xs">
                                            <PreviewValue value={c.preview} />
                                        </div>
                                    )}
                                    {c.requested_by === me.user_id ? (
                                        <p className="text-xs text-muted-foreground">You asked for this; someone else approves it.</p>
                                    ) : (
                                        <div className="flex flex-wrap gap-2">
                                            <Button
                                                className="motion-m1 min-h-11 md:min-h-9"
                                                disabled={busy !== null}
                                                onClick={() => void decide("/api/v1/admin/staff/commands", c.id, "approve", staff.refresh)}
                                            >
                                                Approve
                                            </Button>
                                            <Button
                                                variant="outline"
                                                className="motion-m1 min-h-11 md:min-h-9"
                                                disabled={busy !== null}
                                                onClick={() => void decide("/api/v1/admin/staff/commands", c.id, "reject", staff.refresh)}
                                            >
                                                Turn down
                                            </Button>
                                        </div>
                                    )}
                                </li>
                            ))}
                        </ul>
                    )
                }
            </Panel>
            <Panel title="Operations commands waiting" query={ops} setupHint="Infrastructure commands come from the ops console (/admin/ops), which is not on this build yet.">
                {(data) =>
                    data.commands.length === 0 ? (
                        <Empty>No operations command is waiting.</Empty>
                    ) : (
                        <ul className="divide-y divide-border">
                            {data.commands.map((c) => (
                                <li key={c.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                                    <span className="font-mono text-xs">#{c.id}</span>
                                    <span className="flex-1">{c.command}</span>
                                    <span className="text-xs text-muted-foreground">{c.reason}</span>
                                    {c.requested_by !== me.user_id && (
                                        <Button
                                            size="sm"
                                            className="motion-m1 min-h-11 md:min-h-8"
                                            disabled={busy !== null}
                                            onClick={() => void decide("/api/v1/admin/ops/commands", c.id, "approve", ops.refresh)}
                                        >
                                            Approve
                                        </Button>
                                    )}
                                </li>
                            ))}
                        </ul>
                    )
                }
            </Panel>
            {message && (
                <p role="status" className="text-sm">
                    {message}
                </p>
            )}
        </section>
    );
}
