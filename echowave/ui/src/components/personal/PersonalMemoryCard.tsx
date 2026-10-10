'use client';

/**
 * A person's own memory, as a card on their own thread (`personal_memory`
 * rows; api/services/personal/cards.py).
 *
 * Three views of one row kind:
 * - `about_me` -- "What do you know about me?": every preference they told
 *   Decibyl and what it learned from their own conversations, each with
 *   where it came from and when, and Correct / Forget on each.
 * - `saved` -- a preference just saved from their line, with the same two
 *   buttons, so a misread is put right where it happened.
 * - `proposal` -- one Decibyl offers (from their feedback on a reply, or in a
 *   turn). Nothing is kept until they press Save.
 *
 * The row carries ids, never values: the card reads the values from the
 * person's own store when it draws (GET /personal/cards/{id}), which answers
 * only its owner. Everything happens here, in the thread -- never "go to
 * Settings".
 */

import { Check, History, Loader2, Pencil, Trash2, UserRound } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
    answerProposalApiV1PersonalCardsEventIdAnswerPost,
    cardApiV1PersonalCardsEventIdGet,
    correctLearnedApiV1PersonalLearnedFactIdCorrectPost,
    correctPreferenceApiV1PersonalPreferencesPreferenceIdCorrectPost,
    forgetLearnedApiV1PersonalLearnedFactIdDelete,
    forgetPreferenceApiV1PersonalPreferencesPreferenceIdDelete,
} from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

export type KeptSource = { kind?: string | null; excerpt?: string | null; line?: string | null };

export type KeptPreference = {
    id: number;
    label?: string;
    kind?: string;
    topic?: string;
    value?: string;
    status?: string;
    observed_at?: string | null;
    review_at?: string | null;
    source?: KeptSource;
    history?: KeptPreference[];
    forgotten?: boolean;
};

export type LearnedItem = {
    id: number;
    label: string;
    key?: string;
    value?: string;
    observed_at?: string | null;
    source?: KeptSource;
};

export type Proposal = { kind: string; topic: string; value: string; label: string };

export type MemoryCardData = {
    event_id: number;
    view: 'about_me' | 'saved' | 'proposal' | string;
    preferences?: KeptPreference[];
    learned?: LearnedItem[];
    refused?: string[];
    proposal?: Proposal | null;
    why?: string | null;
    state?: string | null;
};

/** The view a row was written with, read from its payload. */
export function memoryViewOf(event: TimelineEvent): string {
    const view = (event.payload as { view?: unknown } | null)?.view;
    return typeof view === 'string' ? view : '';
}

export function shortDate(at?: string | null): string {
    if (!at) return '';
    const date = new Date(at);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

/** Where a kept preference came from, in one line. */
export function sourceLine(item: KeptPreference): string {
    const when = shortDate(item.observed_at);
    const kind = item.source?.kind;
    let from: string;
    if (kind === 'feedback') from = 'From your feedback on a reply';
    else if (kind === 'suggestion') from = 'You saved a suggestion';
    else if (kind === 'correction') from = 'You corrected it';
    else if (item.source?.excerpt) from = `You said “${item.source.excerpt}”`;
    else from = 'You said it';
    return when ? `${from} · ${when}` : from;
}

type Editing = { scope: 'preference' | 'learned'; id: number } | null;

function Row({
    label,
    source,
    history,
    editing,
    confirmingForget,
    busy,
    error,
    onCorrect,
    onForget,
    onSubmit,
    onCancel,
    onConfirmForget,
    placeholder,
}: {
    label: string;
    source: string;
    history?: KeptPreference[];
    editing: boolean;
    confirmingForget: boolean;
    busy: boolean;
    error: string | null;
    onCorrect: () => void;
    onForget: () => void;
    onSubmit: (text: string) => void;
    onCancel: () => void;
    onConfirmForget: () => void;
    placeholder: string;
}) {
    const [text, setText] = useState('');
    return (
        <li className="py-2" data-testid="memory-row">
            <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{label}</p>
                    <p className="text-xs text-muted-foreground">{source}</p>
                    {history && history.length > 0 && (
                        <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground" data-testid="memory-history">
                            <History aria-hidden className="h-3 w-3" />
                            Was: {history.map((h) => `${h.label}${h.observed_at ? ` (${shortDate(h.observed_at)})` : ''}`).join(', ')}
                        </p>
                    )}
                </div>
                {!editing && !confirmingForget && (
                    <div className="flex shrink-0 gap-1">
                        <Button type="button" size="sm" variant="ghost" onClick={onCorrect} disabled={busy}>
                            <Pencil aria-hidden className="mr-1 h-3.5 w-3.5" />
                            Correct
                        </Button>
                        <Button type="button" size="sm" variant="ghost" onClick={onForget} disabled={busy}>
                            <Trash2 aria-hidden className="mr-1 h-3.5 w-3.5" />
                            Forget
                        </Button>
                    </div>
                )}
            </div>
            {editing && (
                <form
                    className="mt-2 flex gap-2"
                    onSubmit={(e) => {
                        e.preventDefault();
                        if (text.trim()) onSubmit(text.trim());
                    }}
                >
                    <Input
                        autoFocus
                        aria-label={`Correct ${label}`}
                        value={text}
                        placeholder={placeholder}
                        onChange={(e) => setText(e.target.value)}
                        className="h-8 text-sm"
                    />
                    <Button type="submit" size="sm" disabled={busy || !text.trim()}>
                        {busy ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> : 'Save'}
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
                        Cancel
                    </Button>
                </form>
            )}
            {confirmingForget && (
                <div className="mt-2 flex items-center gap-2 text-sm" role="group" aria-label="Forget this">
                    <span>Forget this for good?</span>
                    <Button type="button" size="sm" variant="destructive" onClick={onConfirmForget} disabled={busy}>
                        Forget
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
                        Keep it
                    </Button>
                </div>
            )}
            {error && (
                <p className="mt-1 text-xs text-amber-700 dark:text-amber-400" role="alert">
                    {error}
                </p>
            )}
        </li>
    );
}

const PLACEHOLDER: Record<string, string> = {
    language: 'Hindi',
    call_window: '10:30, or between 10 and 6',
    channel: 'WhatsApp, the app, or a notification',
    cadence: 'daily, weekly or monthly',
    length: 'short or detailed',
};

export function PersonalMemoryCard({ event }: { event: TimelineEvent }) {
    const [data, setData] = useState<MemoryCardData | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [editing, setEditing] = useState<Editing>(null);
    const [forgetting, setForgetting] = useState<Editing>(null);
    const [busy, setBusy] = useState(false);
    const [rowError, setRowError] = useState<{ key: string; message: string } | null>(null);
    const [answerError, setAnswerError] = useState<string | null>(null);

    const load = useCallback(async () => {
        const response = await cardApiV1PersonalCardsEventIdGet({ path: { event_id: event.id } });
        if (response.error || !response.data) {
            setLoadError(detailFromError(response.error, 'Could not read this card'));
            return;
        }
        setLoadError(null);
        setData(response.data as unknown as MemoryCardData);
    }, [event.id]);

    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);
    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void load();
    }, [authLoading, user, load]);

    const keyOf = (scope: string, id: number) => `${scope}:${id}`;

    const correct = async (scope: 'preference' | 'learned', id: number, text: string) => {
        setBusy(true);
        setRowError(null);
        const response =
            scope === 'preference'
                ? await correctPreferenceApiV1PersonalPreferencesPreferenceIdCorrectPost({
                      path: { preference_id: id },
                      body: { text },
                  })
                : await correctLearnedApiV1PersonalLearnedFactIdCorrectPost({ path: { fact_id: id }, body: { text } });
        setBusy(false);
        if (response.error) {
            setRowError({ key: keyOf(scope, id), message: detailFromError(response.error, 'Could not correct that') });
            return;
        }
        setEditing(null);
        if (scope === 'preference' && data?.view === 'saved') {
            // The saved card names the old row; show the new one in its place.
            const fresh = (response.data as { preference?: KeptPreference } | undefined)?.preference;
            if (fresh) {
                setData((d) =>
                    d
                        ? {
                              ...d,
                              preferences: (d.preferences ?? []).map((p) =>
                                  p.id === id ? { ...fresh, history: [{ ...p, history: [] }, ...(p.history ?? [])] } : p,
                              ),
                          }
                        : d,
                );
                return;
            }
        }
        await load();
    };

    const forget = async (scope: 'preference' | 'learned', id: number) => {
        setBusy(true);
        setRowError(null);
        const response =
            scope === 'preference'
                ? await forgetPreferenceApiV1PersonalPreferencesPreferenceIdDelete({ path: { preference_id: id } })
                : await forgetLearnedApiV1PersonalLearnedFactIdDelete({ path: { fact_id: id } });
        setBusy(false);
        if (response.error) {
            setRowError({ key: keyOf(scope, id), message: detailFromError(response.error, 'Could not forget that') });
            return;
        }
        setForgetting(null);
        setData((d) =>
            d
                ? {
                      ...d,
                      preferences:
                          scope === 'preference'
                              ? (d.preferences ?? []).map((p) => (p.id === id ? { id, forgotten: true } : p))
                              : d.preferences,
                      learned: scope === 'learned' ? (d.learned ?? []).filter((l) => l.id !== id) : d.learned,
                  }
                : d,
        );
    };

    const answer = async (save: boolean) => {
        setBusy(true);
        setAnswerError(null);
        const response = await answerProposalApiV1PersonalCardsEventIdAnswerPost({
            path: { event_id: event.id },
            body: { save },
        });
        setBusy(false);
        if (response.error) {
            setAnswerError(detailFromError(response.error, 'Could not save that'));
            return;
        }
        await load();
    };

    const rowProps = (scope: 'preference' | 'learned', id: number) => ({
        editing: editing?.scope === scope && editing.id === id,
        confirmingForget: forgetting?.scope === scope && forgetting.id === id,
        busy,
        error: rowError?.key === keyOf(scope, id) ? rowError.message : null,
        onCorrect: () => {
            setForgetting(null);
            setRowError(null);
            setEditing({ scope, id });
        },
        onForget: () => {
            setEditing(null);
            setRowError(null);
            setForgetting({ scope, id });
        },
        onSubmit: (text: string) => void correct(scope, id, text),
        onCancel: () => {
            setEditing(null);
            setForgetting(null);
        },
        onConfirmForget: () => void forget(scope, id),
    });

    const view = data?.view ?? memoryViewOf(event);
    const title =
        view === 'about_me'
            ? 'What I keep about you'
            : view === 'proposal'
              ? 'Keep this as a preference?'
              : 'Saved to your preferences';

    const preferences = data?.preferences ?? [];
    const learned = data?.learned ?? [];

    return (
        <div
            className="rounded-lg border border-border bg-card p-4"
            role="group"
            aria-label={title}
            data-testid="personal-memory-card"
            data-view={view}
        >
            <p className="flex items-start gap-2 text-sm font-medium">
                <UserRound aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                <span>{title}</span>
            </p>
            <p className="mt-0.5 pl-6 text-xs text-muted-foreground">Only you can see this.</p>
            {loadError && (
                <p className="mt-2 pl-6 text-sm text-amber-700 dark:text-amber-400" role="alert">
                    {loadError}
                </p>
            )}
            {!data && !loadError && (
                <p className="mt-2 flex items-center gap-2 pl-6 text-sm text-muted-foreground">
                    <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
                    Reading…
                </p>
            )}

            {data && view === 'proposal' && data.proposal && (
                <div className="mt-2 pl-6">
                    <p className="text-sm">{data.proposal.label}</p>
                    {data.why && <p className="text-xs text-muted-foreground">{data.why}</p>}
                    {data.state === 'open' ? (
                        <div className="mt-2 flex gap-2">
                            <Button type="button" size="sm" onClick={() => void answer(true)} disabled={busy}>
                                Save
                            </Button>
                            <Button type="button" size="sm" variant="ghost" onClick={() => void answer(false)} disabled={busy}>
                                No thanks
                            </Button>
                        </div>
                    ) : (
                        <p className="mt-2 flex items-center gap-1 text-xs text-muted-foreground" role="status">
                            {data.state === 'saved' ? (
                                <>
                                    <Check aria-hidden className="h-3.5 w-3.5 text-emerald-600" /> Saved to your preferences
                                </>
                            ) : (
                                'Not kept'
                            )}
                        </p>
                    )}
                    {answerError && (
                        <p className="mt-1 text-xs text-amber-700 dark:text-amber-400" role="alert">
                            {answerError}
                        </p>
                    )}
                </div>
            )}

            {data && view !== 'proposal' && (
                <div className="mt-1 pl-6">
                    {view === 'about_me' && (
                        <p className="mt-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                            Your preferences
                        </p>
                    )}
                    {preferences.length === 0 && view === 'about_me' && (
                        <p className="py-2 text-sm text-muted-foreground" data-testid="memory-empty">
                            Nothing yet. Say something like &ldquo;Tamil for calls&rdquo; and I&rsquo;ll keep it here.
                        </p>
                    )}
                    <ul className={cn('divide-y divide-border')}>
                        {preferences.map((item) =>
                            item.forgotten ? (
                                <li key={item.id} className="py-2 text-sm text-muted-foreground" data-testid="memory-forgotten">
                                    Forgotten.
                                </li>
                            ) : (
                                <Row
                                    key={item.id}
                                    label={item.label ?? ''}
                                    source={sourceLine(item)}
                                    history={item.history}
                                    placeholder={PLACEHOLDER[item.kind ?? ''] ?? 'Say it in a few words'}
                                    {...rowProps('preference', item.id)}
                                />
                            ),
                        )}
                    </ul>
                    {view === 'about_me' && learned.length > 0 && (
                        <>
                            <p className="mt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                                Learned from your conversations
                            </p>
                            <ul className="divide-y divide-border">
                                {learned.map((item) => (
                                    <Row
                                        key={item.id}
                                        label={item.label}
                                        source={[item.source?.line, shortDate(item.observed_at)].filter(Boolean).join(' · ')}
                                        placeholder="What it should say"
                                        {...rowProps('learned', item.id)}
                                    />
                                ))}
                            </ul>
                        </>
                    )}
                    {(data.refused ?? []).map((line) => (
                        <p key={line} className="mt-1 text-xs text-amber-700 dark:text-amber-400" role="status">
                            {line}
                        </p>
                    ))}
                </div>
            )}
        </div>
    );
}

export default PersonalMemoryCard;
