'use client';

/**
 * Several changes waiting on one thread (editing_v2), as a stack.
 *
 * Each card is its own draft (services/workflow/self_edit.py), so they can be
 * published or discarded in any order. "Publish all" and "Discard all" are a
 * convenience over that, not a different action: they press each card's own
 * button in turn and show what happened to each one -- one can be refused
 * ("edited elsewhere since") while the others go live.
 */

import { Layers } from 'lucide-react';
import { useState } from 'react';

import type { TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';

import { editOf, settleEdit, usePublisher } from './EditCard';

type Result = { id: number; step: string; ok: boolean; line: string };

export function EditStack({
    pending,
    onSettled,
}: {
    /** The waiting cards, oldest first. */
    pending: TimelineEvent[];
    onSettled?: (event: TimelineEvent) => void;
}) {
    const [busy, setBusy] = useState<'publish' | 'discard' | null>(null);
    const [results, setResults] = useState<Result[]>([]);
    const workflowId = pending[0]?.workflow_id ?? editOf(pending[0] ?? ({} as TimelineEvent)).workflow_id;
    const publisher = usePublisher(workflowId, true);

    if (pending.length < 2 && results.length === 0) return null;

    const all = async (action: 'publish' | 'discard') => {
        setBusy(action);
        const out: Result[] = [];
        // One at a time, oldest first: each is its own publish through the
        // gate, and a refusal on one must not stop the rest.
        for (const event of pending) {
            const step = editOf(event).step || 'a step';
            const result = await settleEdit(event.id, action);
            if (result.data) onSettled?.(result.data);
            out.push({
                id: event.id,
                step,
                ok: !result.error,
                line: result.error ?? (action === 'publish' ? 'Published' : 'Discarded'),
            });
            setResults([...out]);
        }
        setBusy(null);
    };

    return (
        <div className="max-w-2xl rounded-lg border border-border bg-muted/40 p-3" data-testid="edit-stack">
            <p className="flex items-center gap-2 text-sm font-medium">
                <Layers className="h-4 w-4 text-[var(--accent-brand)]" aria-hidden />
                {pending.length > 0
                    ? `${pending.length} changes waiting`
                    : 'Done'}
            </p>
            {pending.length > 1 && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                    {publisher.can_publish ? (
                        <Button size="sm" disabled={busy !== null} onClick={() => void all('publish')}>
                            {busy === 'publish' ? 'Publishing…' : 'Publish all'}
                        </Button>
                    ) : (
                        <p className="text-sm text-muted-foreground">
                            {publisher.waiting || 'Waiting for a workspace admin to publish.'}
                        </p>
                    )}
                    <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void all('discard')}>
                        {busy === 'discard' ? 'Discarding…' : 'Discard all'}
                    </Button>
                </div>
            )}
            {results.length > 0 && (
                <ul className="mt-2 space-y-0.5 text-sm" aria-label="What happened to each change">
                    {results.map((r) => (
                        <li key={r.id} className={r.ok ? 'text-muted-foreground' : 'text-destructive'}>
                            {r.step}: {r.line}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

export default EditStack;
