'use client';

/**
 * What a bot remembers, with a way to make it forget.
 *
 * The same list on every bot's About and on Decibyl's: the organisation's
 * confirmed facts, plus the bot's own where there is a bot. Each row can be
 * forgotten. Forgetting is a status, not a delete (see
 * routes/organisation_memory.py): the row leaves every prompt and every
 * screen at once, and the same row shows "Forgotten · Undo" here until the
 * list is left, so a mis-press costs one click. The same forgetting a person
 * asks for in chat ("forget our Saturday hours") goes through the action
 * card and lands on the same status -- see services/workflow/actions.py.
 */

import { Trash2, Undo2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import {
    readMemoryApiV1OrganisationMemoryGet,
    setStatusApiV1OrganisationMemoryFactIdStatusPost,
} from '@/client/sdk.gen';
import type { MemoryItem } from '@/client/types.gen';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';

const SHOW = 5;

export function MemoryList({
    workflowId,
    botName,
}: {
    /** A bot's About: the organisation's memory plus this bot's own. */
    workflowId?: number;
    botName?: string;
}) {
    const { user, loading: authLoading } = useAuth();
    const fetched = useRef(false);
    const [facts, setFacts] = useState<MemoryItem[] | null>(null);
    const [forgotten, setForgotten] = useState<Record<number, string>>({});
    const [busy, setBusy] = useState<number | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [all, setAll] = useState(false);

    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void (async () => {
            const response = await readMemoryApiV1OrganisationMemoryGet({
                query: workflowId != null ? { workflow_id: workflowId } : undefined,
            });
            if (response.error || !response.data) {
                setFacts([]);
                return;
            }
            setFacts(response.data.facts ?? []);
        })();
    }, [authLoading, user, workflowId]);

    const setStatus = async (fact: MemoryItem, status: string) => {
        setBusy(fact.id);
        setError(null);
        const result = await setStatusApiV1OrganisationMemoryFactIdStatusPost({
            path: { fact_id: fact.id },
            body: { status },
        });
        setBusy(null);
        if (result.error) {
            setError(detailFromError(result.error, 'Could not change that'));
            return;
        }
        setForgotten((was) => {
            const next = { ...was };
            if (status === 'rejected') next[fact.id] = fact.status;
            else delete next[fact.id];
            return next;
        });
    };

    if (facts === null) return null;
    if (facts.length === 0) {
        return <p className="text-sm text-muted-foreground">Nothing confirmed about the business yet.</p>;
    }
    const shown = all ? facts : facts.slice(0, SHOW);

    return (
        <div>
            <ul className="space-y-1 text-sm" aria-label="Memory">
                {shown.map((fact) => {
                    const gone = fact.id in forgotten;
                    return (
                        <li key={fact.id} className="group flex items-center gap-2">
                            {gone ? (
                                <span className="min-w-0 flex-1 truncate text-muted-foreground line-through">
                                    {fact.key}
                                </span>
                            ) : (
                                <>
                                    <span className="shrink-0 text-muted-foreground">{fact.key}</span>
                                    <span className="min-w-0 flex-1 truncate">{fact.value}</span>
                                </>
                            )}
                            {fact.workflow_id != null && !gone && (
                                <span className="shrink-0 rounded-full border border-border px-1.5 text-[10px] text-muted-foreground">
                                    {botName ? `${botName} only` : 'this bot'}
                                </span>
                            )}
                            {gone ? (
                                <button
                                    type="button"
                                    disabled={busy === fact.id}
                                    onClick={() => void setStatus(fact, forgotten[fact.id] || 'confirmed')}
                                    className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground underline-offset-2 hover:underline"
                                >
                                    <Undo2 className="h-3 w-3" aria-hidden />
                                    Forgotten · Undo
                                </button>
                            ) : (
                                <button
                                    type="button"
                                    aria-label={`Forget ${fact.key}`}
                                    title="Forget"
                                    disabled={busy === fact.id}
                                    onClick={() => void setStatus(fact, 'rejected')}
                                    className="shrink-0 rounded p-1 text-muted-foreground opacity-60 hover:bg-muted hover:text-destructive hover:opacity-100 focus-visible:opacity-100"
                                >
                                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                                </button>
                            )}
                        </li>
                    );
                })}
            </ul>
            {facts.length > SHOW && !all && (
                <button
                    type="button"
                    onClick={() => setAll(true)}
                    className="mt-1 text-xs text-muted-foreground underline-offset-2 hover:underline"
                >
                    {facts.length - SHOW} more
                </button>
            )}
            {error && (
                <p role="alert" className="mt-1 text-xs text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}

export default MemoryList;
