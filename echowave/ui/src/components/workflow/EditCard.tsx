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
 * guessing which version is meant. A card made before cards recorded their
 * own change, whose change cannot be rebuilt exactly, says so (``legacy``):
 * it cannot be published on its own, and Discard still settles it.
 *
 * Edit: the person can change the proposed text before publishing it. Their
 * text replaces the bot's in the draft and goes through the same checks as
 * the bot's would (validation, the acceptable-use screen, the audit row), so
 * a refusal reads the same and the card stays open with their text. The
 * bot's own proposal is kept on the card (`original`), and is what the change
 * is later recorded as having been changed from.
 */

import { Check, GitBranch, Pencil } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';

import { settleEditApiV1TimelineEditsSettlePost } from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';
import { cn } from '@/lib/utils';

/** One field of one step the card changes, as the server recorded it. */
export type FieldChange = { node_id?: string; field?: string; old?: string; new?: string; config?: string };

export type GreetingChange = { step?: string; node_id?: string; old?: string; new?: string };

export type EditPayload = {
    workflow_id?: number;
    step?: string;
    why?: string;
    old?: string;
    new?: string;
    diff?: string;
    greetings?: GreetingChange[];
    changes?: FieldChange[];
    original?: Record<string, unknown>;
    decided?: { action?: 'publish' | 'discard'; by?: number; at?: string };
    refused?: { kind?: 'invalid' | 'acceptable_use' | 'conflict' | 'legacy'; reasons?: string[]; at?: string };
};

export function editOf(event: TimelineEvent): EditPayload {
    return (event.payload ?? {}) as EditPayload;
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

const editKey = (c: FieldChange) => `${c.node_id}:${c.field}`;

/** The text fields a person can rewrite before publishing: a step's prompt or
 * greeting. A configuration change (the escalation policy) is not text. */
export function editableChanges(edit: EditPayload): FieldChange[] {
    return (edit.changes ?? []).filter(
        (c) => !c.config && c.node_id != null && (c.field === 'prompt' || c.field === 'greeting'),
    );
}

export function EditCard({
    event,
    onSettled,
}: {
    event: TimelineEvent;
    onSettled?: (event: TimelineEvent) => void;
}) {
    const edit = editOf(event);
    const [saving, setSaving] = useState<'publish' | 'discard' | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [editing, setEditing] = useState(false);
    const [drafts, setDrafts] = useState<Record<string, string>>({});
    const lines = diffLines(edit.diff);
    const greetings = (edit.greetings ?? []).filter((g) => (g.old ?? '') !== (g.new ?? ''));

    const editable = editableChanges(edit);
    const changedEdits = editable
        .filter((c) => (drafts[editKey(c)] ?? c.new ?? '') !== (c.new ?? ''))
        .map((c) => ({ node_id: String(c.node_id), field: String(c.field), new: drafts[editKey(c)] }));
    const fieldLabel = (c: FieldChange) => {
        const where =
            c.field === 'greeting'
                ? (edit.greetings ?? []).find((g) => g.node_id === c.node_id)?.step
                : editable.filter((x) => x.field === 'prompt').length === 1
                  ? edit.step
                  : undefined;
        return `${where || `Step ${c.node_id}`} · ${c.field === 'greeting' ? 'greeting' : 'instructions'}`;
    };

    const settle = async (action: 'publish' | 'discard', edits: typeof changedEdits = []) => {
        setSaving(action);
        setError(null);
        const result = await settleEditApiV1TimelineEditsSettlePost({
            body: edits.length > 0 ? { event_id: event.id, action, edits } : { event_id: event.id, action },
        });
        setSaving(null);
        if (result.error) {
            setError(detailFromError(result.error, 'Could not do that'));
            return;
        }
        if (result.data) {
            setEditing(false);
            onSettled?.(result.data);
        }
    };

    const decided = edit.decided;
    const legacy = edit.refused?.kind === 'legacy';
    const workflowId = event.workflow_id ?? edit.workflow_id;
    const editorLink = workflowId ? (
        <Link href={`/workflow/${workflowId}`} className="ml-1 underline underline-offset-4" data-testid="open-editor">
            Open the editor
        </Link>
    ) : null;

    return (
        <div className="max-w-2xl rounded-lg border border-border bg-card p-3" data-testid="edit-card">
            <p className="flex items-center gap-2 text-sm font-medium">
                <GitBranch className="h-4 w-4 text-[var(--accent-brand)]" aria-hidden />
                Change to {edit.step || 'a step'}
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
            {!decided && !error && edit.refused?.kind === 'conflict' && (
                <p className="mt-2 text-sm text-destructive" role="alert" data-testid="edit-refused">
                    Not published: this change was edited elsewhere since.
                    {editorLink}
                </p>
            )}
            {!decided && !error && legacy && (
                <p className="mt-2 text-sm text-destructive" role="alert" data-testid="edit-refused">
                    This card was made before an update and can&apos;t be applied on its own — open the editor to
                    review it.
                    {editorLink}
                </p>
            )}
            {!decided &&
                !error &&
                edit.refused?.kind !== 'conflict' &&
                !legacy &&
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
            {editing && !decided && (
                <div className="mt-3 space-y-2" data-testid="edit-fields">
                    {editable.map((c) => (
                        <label key={editKey(c)} className="block text-xs text-muted-foreground">
                            {fieldLabel(c)}
                            <textarea
                                className="mt-1 block min-h-24 w-full rounded-md border border-border bg-background p-2 text-sm text-foreground"
                                value={drafts[editKey(c)] ?? c.new ?? ''}
                                onChange={(e) => setDrafts({ ...drafts, [editKey(c)]: e.target.value })}
                                aria-label={fieldLabel(c)}
                            />
                        </label>
                    ))}
                    <p className="text-xs text-muted-foreground">
                        Your text is checked the same way before it goes live.
                    </p>
                </div>
            )}
            {decided ? (
                <p className="mt-2 flex items-center gap-1.5 text-sm text-muted-foreground">
                    <Check className="h-3.5 w-3.5" aria-hidden />
                    {decided.action === 'publish' ? (edit.original ? 'Published, as edited' : 'Published') : 'Discarded'}
                    {decided.at ? ` · ${new Date(decided.at).toLocaleString()}` : ''}
                </p>
            ) : (
                <div className="mt-3 flex gap-2">
                    {!legacy && !editing && (
                        <Button size="sm" disabled={saving !== null} onClick={() => void settle('publish')}>
                            {saving === 'publish' ? 'Publishing…' : 'Publish'}
                        </Button>
                    )}
                    {!legacy && editing && (
                        <Button
                            size="sm"
                            disabled={saving !== null}
                            onClick={() => void settle('publish', changedEdits)}
                        >
                            {saving === 'publish' ? 'Publishing…' : 'Publish edited'}
                        </Button>
                    )}
                    {!legacy && editable.length > 0 && (
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={saving !== null}
                            onClick={() => {
                                setEditing(!editing);
                                setDrafts({});
                            }}
                        >
                            {editing ? (
                                'Cancel edit'
                            ) : (
                                <>
                                    <Pencil className="mr-1 h-3.5 w-3.5" aria-hidden />
                                    Edit
                                </>
                            )}
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
