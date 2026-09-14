'use client';

/**
 * A bot proposed a change to itself; a person publishes or discards it here.
 *
 * The card shows the step, why the bot changed it, and the diff -- the same
 * shape a code review has, because that is what this is: a change somebody
 * has to read before it goes live. Once settled the card says what was done,
 * for everyone who opens the thread afterwards (see
 * services/workflow/self_edit.py: the action is written into the row).
 */

import { Check, GitBranch } from 'lucide-react';
import { useState } from 'react';

import { settleEditApiV1TimelineEditsSettlePost } from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';
import { cn } from '@/lib/utils';

export type EditPayload = {
    step?: string;
    why?: string;
    old?: string;
    new?: string;
    diff?: string;
    decided?: { action?: 'publish' | 'discard'; by?: number; at?: string };
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
    const lines = diffLines(edit.diff);

    const settle = async (action: 'publish' | 'discard') => {
        setSaving(action);
        setError(null);
        const result = await settleEditApiV1TimelineEditsSettlePost({
            body: { event_id: event.id, action },
        });
        setSaving(null);
        if (result.error) {
            setError(detailFromError(result.error, 'Could not do that'));
            return;
        }
        if (result.data) onSettled?.(result.data);
    };

    const decided = edit.decided;

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
            {error && (
                <p className="mt-2 text-sm text-destructive" role="alert">
                    {error}
                </p>
            )}
            {decided ? (
                <p className="mt-2 flex items-center gap-1.5 text-sm text-muted-foreground">
                    <Check className="h-3.5 w-3.5" aria-hidden />
                    {decided.action === 'publish' ? 'Published' : 'Discarded'}
                    {decided.at ? ` · ${new Date(decided.at).toLocaleString()}` : ''}
                </p>
            ) : (
                <div className="mt-3 flex gap-2">
                    <Button size="sm" disabled={saving !== null} onClick={() => void settle('publish')}>
                        {saving === 'publish' ? 'Publishing…' : 'Publish'}
                    </Button>
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
