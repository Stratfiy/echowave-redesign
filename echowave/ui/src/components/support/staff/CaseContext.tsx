"use client";

/**
 * Screen 32, the context panel: the customer and workspace, exactly what
 * they shared, read-only diagnostics no wider than that share, their files,
 * the actions on this case (screen 33) and the case's audit history.
 * Nothing here is the customer's private history.
 */

import { ExternalLink, Paperclip, Plus } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AuditTimeline, TaskStatus } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { TASK_STATES, type TaskState } from "@/lib/shell/taskState";
import {
    ACTION_STATE_LABEL,
    attachmentLink,
    listActions,
    type SupportAction,
    type SupportCase,
} from "@/lib/support/staff";
import { cn } from "@/lib/utils";


function Block({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <section className="border-b border-border p-3 last:border-0">
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
            {children}
        </section>
    );
}

export function CaseContext({
    supportCase,
    actionsOn,
    className,
}: {
    supportCase: SupportCase;
    actionsOn: boolean;
    className?: string;
}) {
    const [actions, setActions] = useState<SupportAction[] | null>(null);
    const [actionsError, setActionsError] = useState<string | null>(null);
    const [fileError, setFileError] = useState<string | null>(null);

    const loadActions = useCallback(async () => {
        if (!actionsOn) return;
        const outcome = await listActions({ ticketId: supportCase.id });
        if (outcome.ok) {
            setActions(outcome.value);
            setActionsError(null);
        } else setActionsError(outcome.error);
    }, [actionsOn, supportCase.id]);

    useEffect(() => {
        void loadActions();
    }, [loadActions, supportCase.version, supportCase.messages.length]);

    async function open(id: number) {
        setFileError(null);
        const outcome = await attachmentLink(id);
        if (outcome.ok) window.open(outcome.value, "_blank", "noopener,noreferrer");
        else setFileError(outcome.error);
    }

    const task = supportCase.diagnostics.task;
    const newAction = `/superadmin/support/actions/new?ticket=${supportCase.id}&organization=${supportCase.workspace.id}&user=${supportCase.requester.id}`;

    return (
        <aside aria-label="Customer context" className={cn("flex min-h-0 flex-col overflow-y-auto", className)}>
            <Block title="Customer">
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                    <dt className="text-muted-foreground">Person</dt>
                    <dd className="min-w-0 break-words">
                        {supportCase.requester.email ?? "—"} <span className="text-muted-foreground">#{supportCase.requester.id}</span>
                    </dd>
                    <dt className="text-muted-foreground">Workspace</dt>
                    <dd className="min-w-0 break-words">
                        {supportCase.workspace.name ?? "—"} <span className="text-muted-foreground">#{supportCase.workspace.id}</span>
                    </dd>
                    <dt className="text-muted-foreground">Reopened</dt>
                    <dd>{supportCase.reopened_count} times</dd>
                </dl>
            </Block>

            <Block title="What they shared">
                {supportCase.shared.sections.map((section) => (
                    <div key={section.key} className="mb-2 text-sm last:mb-0" data-testid={`case-shared-${section.key}`}>
                        <p className="font-medium">{section.label}</p>
                        <dl className="grid grid-cols-[minmax(5rem,auto)_1fr] gap-x-3">
                            {section.fields.map((field) => (
                                <div key={field.label} className="contents">
                                    <dt className="text-muted-foreground">{field.label}</dt>
                                    <dd className="min-w-0 whitespace-pre-wrap break-words">{String(field.value ?? "—")}</dd>
                                </div>
                            ))}
                        </dl>
                    </div>
                ))}
                {supportCase.shared.left_out.length > 0 && (
                    <p className="text-xs text-muted-foreground">Not shared by the customer: {supportCase.shared.left_out.join(", ").replace(/_/g, " ")}.</p>
                )}
            </Block>

            <Block title="Diagnostics (read-only)">
                {task ? (
                    task.state === "missing" ? (
                        <p className="text-sm text-muted-foreground">{task.note}</p>
                    ) : (
                        <div className="flex flex-col gap-1 text-sm">
                            <p>
                                Task #{task.id} · version {task.version}
                            </p>
                            {(TASK_STATES as readonly string[]).includes(task.state) ? <TaskStatus state={task.state as TaskState} /> : <p>{task.state}</p>}
                            <ol className="mt-1 text-xs text-muted-foreground">
                                {(task.history ?? []).map((h, i) => (
                                    <li key={i}>
                                        {new Date(h.at).toLocaleString()} → {h.to.replace(/_/g, " ")}
                                        {h.reason_code ? ` (${h.reason_code})` : ""}
                                    </li>
                                ))}
                            </ol>
                        </div>
                    )
                ) : (
                    <p className="text-sm text-muted-foreground">
                        {supportCase.affected ? "The customer did not share the task's details." : "Not about a task."}
                    </p>
                )}
                {supportCase.diagnostics.allowances ? (
                    <ul className="mt-2 text-xs">
                        {supportCase.diagnostics.allowances.map((a) => (
                            <li key={a.kind}>
                                {a.unit}: {a.used} of {a.limit} used today
                            </li>
                        ))}
                    </ul>
                ) : (
                    <p className="mt-2 text-xs text-muted-foreground">Daily limits are off, so there is no allowance to show.</p>
                )}
            </Block>

            {supportCase.attachments.length > 0 && (
                <Block title="Files">
                    <ul className="flex flex-col gap-1 text-sm">
                        {supportCase.attachments.map((a) => (
                            <li key={a.id}>
                                <button type="button" className="motion-m1 flex min-h-11 items-center gap-1 underline-offset-2 hover:underline md:min-h-0" onClick={() => void open(a.id)}>
                                    <Paperclip aria-hidden className="h-3.5 w-3.5" /> {a.file_name}
                                    <ExternalLink aria-hidden className="h-3 w-3" />
                                </button>
                            </li>
                        ))}
                    </ul>
                    {fileError && (
                        <p role="alert" className="mt-1 text-xs text-[#772322] dark:text-red-300">
                            {fileError}
                        </p>
                    )}
                </Block>
            )}

            {actionsOn && (
                <Block title="Actions">
                    {actionsError ? (
                        <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                            {actionsError}
                        </p>
                    ) : actions && actions.length > 0 ? (
                        <ul className="mb-2 flex flex-col gap-1 text-sm">
                            {actions.map((a) => (
                                <li key={a.id}>
                                    <Link href={`/superadmin/support/actions/${a.id}`} className="motion-m1 flex min-h-11 flex-wrap justify-between gap-2 underline-offset-2 hover:underline md:min-h-0">
                                        <span className="min-w-0 break-words">{a.preview.title}</span>
                                        <span className="motion-m2 text-muted-foreground">{ACTION_STATE_LABEL[a.state] ?? a.state}</span>
                                    </Link>
                                </li>
                            ))}
                        </ul>
                    ) : (
                        <p className="mb-2 text-sm text-muted-foreground">No actions on this case.</p>
                    )}
                    <Button asChild variant="outline" className="motion-m1 min-h-11 md:min-h-9">
                        <Link href={newAction}>
                            <Plus aria-hidden className="h-4 w-4" /> Request an action
                        </Link>
                    </Button>
                </Block>
            )}

            <Block title="History">
                <AuditTimeline
                    entries={supportCase.history.map((h) => ({
                        id: h.id,
                        at: h.at ?? "",
                        actor: h.actor,
                        action: h.action,
                        reason: h.note || undefined,
                    }))}
                    emptyTitle="No staff activity yet."
                />
            </Block>
        </aside>
    );
}

export default CaseContext;
