'use client';

/**
 * What this conversation uses, above the composer (skills-and-context:
 * "In chat, show a compact Context control -- for example: Personal · Tamil
 * · 2 files · Calendar").
 *
 * Tapping it opens the sources in place: Personal (the person's own
 * preferences and memory), the workspace's confirmed facts (not in a
 * personal space), its files, the files in this conversation, and each
 * connected app -- each with a switch that leaves it out of, or puts it back
 * into, this person's turns in this conversation only
 * (api/services/personal/context_control.py). Nothing here grants access:
 * putting a source back gives back only what was already connected.
 *
 * Hidden unless `evolve_personal` is on.
 */

import { ChevronDown, ChevronUp, Layers, Loader2 } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
    chooseContextApiV1PersonalContextPut,
    conversationContextApiV1PersonalContextGet,
} from '@/client/sdk.gen';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { useFeature } from '@/lib/features';
import { cn } from '@/lib/utils';

export type ContextSource = {
    id: string;
    kind: string;
    label: string;
    detail?: string;
    included: boolean;
};

export type ConversationContext = {
    chip: string;
    left_out: number;
    space: 'personal' | 'workspace' | string;
    sources: ContextSource[];
};

export function ContextChipView({
    context,
    open,
    busy,
    error,
    onToggleOpen,
    onChoose,
}: {
    context: ConversationContext;
    open: boolean;
    busy: string | null;
    error: string | null;
    onToggleOpen: () => void;
    onChoose: (source: string, included: boolean) => void;
}) {
    return (
        <div className="mb-2" data-testid="context-chip">
            <button
                type="button"
                onClick={onToggleOpen}
                aria-expanded={open}
                aria-controls="context-sources"
                className="inline-flex h-7 max-w-full items-center gap-1.5 rounded-full border border-border bg-background px-3 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
                <Layers aria-hidden className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">{context.chip}</span>
                {context.left_out > 0 && (
                    <span className="shrink-0 text-amber-700 dark:text-amber-400">· {context.left_out} left out</span>
                )}
                {open ? (
                    <ChevronUp aria-hidden className="h-3.5 w-3.5 shrink-0" />
                ) : (
                    <ChevronDown aria-hidden className="h-3.5 w-3.5 shrink-0" />
                )}
            </button>
            {open && (
                <div
                    id="context-sources"
                    role="group"
                    aria-label="What this conversation uses"
                    className="mt-2 rounded-lg border border-border bg-card p-3"
                >
                    <p className="mb-2 text-xs text-muted-foreground">
                        What this conversation uses. Turning one off affects only your messages here.
                    </p>
                    <ul className="space-y-1.5">
                        {context.sources.map((source) => (
                            <li key={source.id} className="flex items-center gap-3" data-testid="context-source">
                                <div className="min-w-0 flex-1">
                                    <p className={cn('text-sm', !source.included && 'text-muted-foreground line-through')}>
                                        {source.label}
                                    </p>
                                    {source.detail && <p className="text-xs text-muted-foreground">{source.detail}</p>}
                                </div>
                                {busy === source.id ? (
                                    <Loader2 aria-hidden className="h-4 w-4 animate-spin text-muted-foreground" />
                                ) : (
                                    <input
                                        type="checkbox"
                                        role="switch"
                                        aria-label={`Use ${source.label}`}
                                        className="h-4 w-4"
                                        checked={source.included}
                                        disabled={busy !== null}
                                        onChange={(e) => onChoose(source.id, e.target.checked)}
                                    />
                                )}
                            </li>
                        ))}
                    </ul>
                    {error && (
                        <p className="mt-2 text-xs text-amber-700 dark:text-amber-400" role="alert">
                            {error}
                        </p>
                    )}
                </div>
            )}
        </div>
    );
}

export function ContextChip({ threadId, refreshKey }: { threadId?: string | null; refreshKey?: number }) {
    const on = useFeature('evolve_personal');
    const { user, loading: authLoading } = useAuth();
    const [context, setContext] = useState<ConversationContext | null>(null);
    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const lastKey = useRef<string | null>(null);

    const load = useCallback(async () => {
        const response = await conversationContextApiV1PersonalContextGet({
            query: threadId ? { thread_id: threadId } : undefined,
        });
        if (response.error || !response.data) {
            // The chip is an aid: when it cannot be read it is not drawn,
            // and the conversation goes on with everything in.
            setContext(null);
            return;
        }
        setContext(response.data as unknown as ConversationContext);
    }, [threadId]);

    useEffect(() => {
        if (!on || authLoading || !user) return;
        const key = `${threadId ?? 'original'}:${refreshKey ?? 0}`;
        if (lastKey.current === key) return;
        lastKey.current = key;
        void load();
    }, [on, authLoading, user, threadId, refreshKey, load]);

    if (!on || !context) return null;

    const choose = async (source: string, included: boolean) => {
        setBusy(source);
        setError(null);
        const response = await chooseContextApiV1PersonalContextPut({
            body: { thread_id: threadId ?? null, source, included },
        });
        setBusy(null);
        if (response.error || !response.data) {
            setError(detailFromError(response.error, 'Could not change that'));
            return;
        }
        setContext(response.data as unknown as ConversationContext);
    };

    return (
        <ContextChipView
            context={context}
            open={open}
            busy={busy}
            error={error}
            onToggleOpen={() => setOpen((v) => !v)}
            onChoose={(source, included) => void choose(source, included)}
        />
    );
}

export default ContextChip;
