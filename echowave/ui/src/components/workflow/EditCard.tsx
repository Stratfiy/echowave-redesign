'use client';

/**
 * A bot proposed a change to itself; a person publishes or discards it here.
 *
 * The card shows the step, why the bot changed it, and the diff -- the same
 * shape a code review has, because that is what this is: a change somebody
 * has to read before it goes live. Once settled the card says what was done,
 * for everyone who opens the thread afterwards (see
 * services/workflow/self_edit.py: the action is written into the row).
 *
 * A greeting is shown as its own before and after, labelled: it is the
 * first thing a caller hears, and a card that changed it without showing it
 * asked somebody to approve a change they could not see. When Publish is
 * refused -- the draft fails validation, or the acceptable-use screen names
 * a clause -- the card keeps the reasons and stays open. A card acts on its
 * own change only; when that step was edited elsewhere since, Publish is
 * refused (``conflict``) and the card points at the editor rather than
 * guessing which version is meant.
 */

import { Check, GitBranch, Undo2 } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';

import {
    editPublisherApiV1TimelineEditsPublisherGet,
    settleEditApiV1TimelineEditsSettlePost,
} from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';
import { useFeature } from '@/lib/features';
import { cn } from '@/lib/utils';

export type GreetingChange = { step?: string; node_id?: string; old?: string; new?: string };
export type FileRef = { uuid?: string; name?: string };
export type EditAction = 'publish' | 'discard' | 'undo';
type Refusal = { kind?: 'invalid' | 'acceptable_use' | 'conflict'; reasons?: string[]; at?: string };

export type EditPayload = {
    workflow_id?: number;
    step?: string;
    why?: string;
    old?: string;
    new?: string;
    diff?: string;
    greetings?: GreetingChange[];
    /** editing_v2: what kind of change ('hours', 'files', 'undo'; absent for steps). */
    what?: string;
    hours?: { before?: string[]; after?: string[] };
    files?: { added?: FileRef[]; removed?: FileRef[] };
    undo_of?: number;
    decided?: { action?: 'publish' | 'discard'; by?: number; at?: string };
    refused?: Refusal;
    undone?: { by?: number; at?: string; by_card?: number };
    undo_refused?: Refusal;
};

export function editOf(event: TimelineEvent): EditPayload {
    return (event.payload ?? {}) as EditPayload;
}

/** A card still waiting for Publish or Discard. */
export function isPending(event: TimelineEvent): boolean {
    return event.kind === 'edit_proposed' && !editOf(event).decided;
}

/** One click on one card; the updated row, or why not. Shared by the card
 *  and the stack's Publish all / Discard all, which act card by card. */
export async function settleEdit(
    eventId: number,
    action: EditAction,
): Promise<{ data?: TimelineEvent; error?: string }> {
    const result = await settleEditApiV1TimelineEditsSettlePost({ body: { event_id: eventId, action } });
    if (result.error) return { error: detailFromError(result.error, 'Could not do that') };
    return { data: result.data };
}

type Publisher = { can_publish: boolean; waiting?: string | null };
const publishers = new Map<number, Promise<Publisher>>();

/** Whether the viewer may publish this agent's cards (editing_v2), asked
 *  once per agent per page. Assumed yes until known, and on any failure:
 *  the server refuses a click it should not take, and says who can. */
export function usePublisher(workflowId: number | null | undefined, on: boolean): Publisher {
    const [publisher, setPublisher] = useState<Publisher>({ can_publish: true });
    useEffect(() => {
        if (!on || !workflowId) return;
        let live = true;
        let pending = publishers.get(workflowId);
        if (!pending) {
            pending = editPublisherApiV1TimelineEditsPublisherGet({ query: { workflow_id: workflowId } })
                .then((r) => (r.data as Publisher | undefined) ?? { can_publish: true })
                .catch(() => ({ can_publish: true }));
            publishers.set(workflowId, pending);
        }
        void pending.then((p) => {
            if (live) setPublisher(p);
        });
        return () => {
            live = false;
        };
    }, [workflowId, on]);
    return publisher;
}

/** For tests: forget what was asked. */
export function forgetPublishers() {
    publishers.clear();
}

function BeforeAfter({ label, before, after }: { label: string; before: string[]; after: string[] }) {
    return (
        <div
            className="mt-2 rounded-md border border-border bg-background p-2 text-xs leading-relaxed"
            data-testid="hours-change"
        >
            <p className="mb-1 font-medium text-muted-foreground">{label}</p>
            {before.map((line, i) => (
                <div key={`b${i}`} className="whitespace-pre-wrap bg-red-500/15 px-1 text-red-900 line-through dark:text-red-200">
                    − {line}
                </div>
            ))}
            {after.map((line, i) => (
                <div key={`a${i}`} className="whitespace-pre-wrap bg-emerald-500/15 px-1 text-emerald-900 dark:text-emerald-200">
                    + {line}
                </div>
            ))}
        </div>
    );
}

/** The unified diff, minus its header, one line per row. */
export function diffLines(diff: string | undefined): { kind: 'add' | 'del' | 'ctx' | 'meta'; text: string }[] {
    return (diff || '')
        .split('\n')
        .filter((line) => line.length > 0 && !line.startsWith('---') && !line.startsWith('+++'))
        .map((line) => {
            if (line.startsWith('@@')) return { kind: 'meta' as const, text: line };
            if (line.startsWith('+')) return { kind: 'add' as const, text: line.slice(1) };
            if (line.startsWith('-')) return { kind: 'del' as const, text: line.slice(1) };
            return { kind: 'ctx' as const, text: line.startsWith(' ') ? line.slice(1) : line };
        });
}

export function EditCard({
    event,
    onSettled,
}: {
    event: TimelineEvent;
    onSettled?: (event: TimelineEvent) => void;
}) {
    const edit = editOf(event);
    const v2 = useFeature('editing_v2');
    const [saving, setSaving] = useState<EditAction | null>(null);
    const [error, setError] = useState<string | null>(null);
    const lines = diffLines(edit.diff);
    const greetings = (edit.greetings ?? []).filter((g) => (g.old ?? '') !== (g.new ?? ''));
    const addedFiles = edit.files?.added ?? [];
    const removedFiles = edit.files?.removed ?? [];

    const settle = async (action: EditAction) => {
        setSaving(action);
        setError(null);
        const result = await settleEdit(event.id, action);
        setSaving(null);
        if (result.error) {
            setError(result.error);
            return;
        }
        if (result.data) onSettled?.(result.data);
    };

    const decided = edit.decided;
    const workflowId = event.workflow_id ?? edit.workflow_id;
    const publisher = usePublisher(workflowId, v2);
    const canUndo = v2 && decided?.action === 'publish' && !edit.undone && publisher.can_publish;
    const editorLink = workflowId ? (
        <Link href={`/workflow/${workflowId}`} className="ml-1 underline underline-offset-4" data-testid="open-editor">
            Open the editor
        </Link>
    ) : null;

    return (
        <div className="max-w-2xl rounded-lg border border-border bg-card p-3" data-testid="edit-card">
            <p className="flex items-center gap-2 text-sm font-medium">
                <GitBranch className="h-4 w-4 text-[var(--accent-brand)]" aria-hidden />
                {edit.what === 'undo' ? 'Undo the change to ' : 'Change to '}
                {edit.step || 'a step'}
            </p>
            {edit.why && <p className="mt-1 text-sm text-muted-foreground">{edit.why}</p>}
            {lines.length > 0 && (
                <pre
                    className="mt-2 max-h-72 overflow-auto rounded-md border border-border bg-background p-2 text-xs leading-relaxed"
                    aria-label="What changes"
                >
                    {lines.map((line, i) => (
                        <div
                            key={i}
                            className={cn(
                                'whitespace-pre-wrap px-1',
                                line.kind === 'add' && 'bg-emerald-500/15 text-emerald-900 dark:text-emerald-200',
                                line.kind === 'del' && 'bg-red-500/15 text-red-900 line-through dark:text-red-200',
                                line.kind === 'meta' && 'text-muted-foreground',
                            )}
                        >
                            {line.kind === 'add' ? '+ ' : line.kind === 'del' ? '− ' : '  '}
                            {line.text}
                        </div>
                    ))}
                </pre>
            )}
            {greetings.map((greeting, i) => (
                <div
                    key={`${greeting.node_id ?? greeting.step ?? ''}-${i}`}
                    className="mt-2 rounded-md border border-border bg-background p-2 text-xs leading-relaxed"
                    data-testid="greeting-change"
                >
                    <p className="mb-1 font-medium text-muted-foreground">
                        Greeting{greeting.step ? ` · ${greeting.step}` : ''}
                    </p>
                    <div className="whitespace-pre-wrap bg-red-500/15 px-1 text-red-900 line-through dark:text-red-200">
                        − {greeting.old || '(no greeting)'}
                    </div>
                    <div className="whitespace-pre-wrap bg-emerald-500/15 px-1 text-emerald-900 dark:text-emerald-200">
                        + {greeting.new || '(no greeting)'}
                    </div>
                </div>
            ))}
            {edit.hours && (
                <BeforeAfter
                    label="Opening hours"
                    before={edit.hours.before ?? []}
                    after={edit.hours.after ?? []}
                />
            )}
            {(addedFiles.length > 0 || removedFiles.length > 0) && (
                <div
                    className="mt-2 rounded-md border border-border bg-background p-2 text-xs leading-relaxed"
                    data-testid="files-change"
                >
                    <p className="mb-1 font-medium text-muted-foreground">Files</p>
                    {removedFiles.map((file, i) => (
                        <div key={`r${i}`} className="bg-red-500/15 px-1 text-red-900 line-through dark:text-red-200">
                            − Stops reading {file.name || file.uuid}
                        </div>
                    ))}
                    {addedFiles.map((file, i) => (
                        <div key={`a${i}`} className="bg-emerald-500/15 px-1 text-emerald-900 dark:text-emerald-200">
                            + Reads {file.name || file.uuid}
                        </div>
                    ))}
                </div>
            )}
            {decided && !edit.undone && !error && edit.undo_refused && (
                <p className="mt-2 text-sm text-destructive" role="alert" data-testid="undo-refused">
                    Not undone:{' '}
                    {edit.undo_refused.kind === 'conflict'
                        ? 'this change was edited elsewhere since.'
                        : (edit.undo_refused.reasons ?? []).join('; ')}
                    {edit.undo_refused.kind === 'conflict' && editorLink}
                </p>
            )}
            {!decided && !error && edit.refused?.kind === 'conflict' && (
                <p className="mt-2 text-sm text-destructive" role="alert" data-testid="edit-refused">
                    Not published: this change was edited elsewhere since.
                    {editorLink}
                </p>
            )}
            {!decided &&
                !error &&
                edit.refused?.kind !== 'conflict' &&
                edit.refused?.reasons &&
                edit.refused.reasons.length > 0 && (
                <div className="mt-2 text-sm text-destructive" role="alert" data-testid="edit-refused">
                    <p>
                        {edit.refused.kind === 'acceptable_use'
                            ? 'Not published: this may breach the acceptable use policy.'
                            : 'Not published: this change cannot go live yet.'}
                    </p>
                    <ul className="mt-1 list-disc pl-5">
                        {edit.refused.reasons.map((reason, i) => (
                            <li key={i}>{reason}</li>
                        ))}
                    </ul>
                </div>
            )}
            {error && (
                <p className="mt-2 text-sm text-destructive" role="alert">
                    {error}
                    {/open the editor/i.test(error) && editorLink}
                </p>
            )}
            {decided ? (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                    <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
                        <Check className="h-3.5 w-3.5" aria-hidden />
                        {edit.undone ? 'Undone' : decided.action === 'publish' ? 'Published' : 'Discarded'}
                        {(edit.undone?.at ?? decided.at)
                            ? ` · ${new Date((edit.undone?.at ?? decided.at) as string).toLocaleString()}`
                            : ''}
                    </p>
                    {canUndo && (
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={saving !== null}
                            onClick={() => void settle('undo')}
                            data-testid="edit-undo"
                        >
                            <Undo2 className="mr-1 h-3.5 w-3.5" aria-hidden />
                            {saving === 'undo' ? 'Undoing…' : 'Undo'}
                        </Button>
                    )}
                </div>
            ) : (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                    {v2 && !publisher.can_publish ? (
                        <p className="text-sm text-muted-foreground" data-testid="edit-waiting">
                            {publisher.waiting || 'Waiting for a workspace admin to publish.'}
                        </p>
                    ) : (
                        <Button size="sm" disabled={saving !== null} onClick={() => void settle('publish')}>
                            {saving === 'publish' ? 'Publishing…' : 'Publish'}
                        </Button>
                    )}
                    <Button
                        size="sm"
                        variant="outline"
                        disabled={saving !== null}
                        onClick={() => void settle('discard')}
                    >
                        {saving === 'discard' ? 'Discarding…' : 'Discard'}
                    </Button>
                </div>
            )}
        </div>
    );
}

export default EditCard;
