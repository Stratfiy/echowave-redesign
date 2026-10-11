'use client';

/**
 * A skill learned something, and a person decides (services/evolve).
 *
 * Four kinds of card share this frame, by `payload.type`:
 *
 * - `offer` — "I've learned a better way to …": the lessons it adds or
 *   drops, the evidence they came from, how it did against the current
 *   version on held-out tasks and on unrelated ones, and what testing it
 *   cost. Publish or Not now.
 * - `remembered` — "Remember this as my way": the draft's fields, editable
 *   in place, then Save as a skill or Discard. Only its author sees it.
 * - `disable` — later tasks went worse since a version was published:
 *   Roll back or Keep it.
 * - `attach` — put a skill on one agent: Add or Not now.
 *
 * Nothing changes until a press; the press is written into the card's own
 * row, so the card says what was decided for everyone who opens the thread.
 */

import { Check, Sparkles } from 'lucide-react';
import { useState } from 'react';

import {
    editVersionApiV1EvolveVersionsVersionIdPut,
    settleCardApiV1EvolveCardsSettlePost,
} from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';

export type LessonChange = { op?: 'add' | 'remove'; text?: string };
export type EvidenceLine = { id?: number; kind?: string; outcome?: string; summary?: string };
export type SetResult = {
    n?: number;
    baseline_passed?: number;
    candidate_passed?: number;
    regressions?: string[];
    delta?: number;
};
export type Outcomes = { before?: { rate?: number; n?: number }; after?: { rate?: number; n?: number } } | null;

export type DraftContent = {
    title?: string;
    description?: string;
    example?: string;
    when_to_use?: string;
    steps?: string[];
    inputs?: string[];
    outputs?: string[];
    wont_do?: string[];
};

export type LearningPayload = {
    type?: 'offer' | 'remembered' | 'disable' | 'attach';
    version_id?: number;
    slug?: string;
    title?: string;
    version?: number;
    base_version?: number | null;
    changes?: LessonChange[];
    evidence?: EvidenceLine[];
    evaluation?: { related?: SetResult; unrelated?: SetResult; passed?: boolean } | null;
    cost?: { model_calls?: number; tokens?: number };
    content?: DraftContent;
    left_out?: string[];
    related_outcomes?: Outcomes;
    unrelated_outcomes?: Outcomes;
    agent_name?: string;
    decided?: { action?: string; by?: number; at?: string };
};

export function learningOf(event: TimelineEvent): LearningPayload {
    return (event.payload ?? {}) as LearningPayload;
}

const ACTIONS: Record<string, [string, string, string, string]> = {
    // type: [yes action, yes label, no action, no label]
    offer: ['publish', 'Publish', 'discard', 'Not now'],
    remembered: ['publish', 'Save as a skill', 'discard', 'Discard'],
    disable: ['rollback', 'Roll back', 'keep', 'Keep it'],
    attach: ['add', 'Add', 'discard', 'Not now'],
};

const DECIDED: Record<string, string> = {
    publish: 'Published',
    discard: 'Not taken',
    rollback: 'Rolled back',
    keep: 'Kept',
    add: 'Added',
};

function pct(rate: number | undefined): string {
    return `${Math.round((rate ?? 0) * 100)}%`;
}

export function heading(payload: LearningPayload): string {
    const title = payload.title || 'this skill';
    switch (payload.type) {
        case 'offer':
            return `I've learned a better way to ${title}`;
        case 'remembered':
            return `Remembered as your way: ${title}`;
        case 'disable':
            return `${title} has done worse since version ${payload.version ?? ''}`.trim();
        case 'attach':
            return `Add ${title} to ${payload.agent_name || 'an agent'}?`;
        default:
            return title;
    }
}

function lines(text: string): string[] {
    return text
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean);
}

function DraftEditor({
    content,
    onChange,
}: {
    content: DraftContent;
    onChange: (next: DraftContent) => void;
}) {
    const text = (key: keyof DraftContent, label: string) => (
        <label className="block text-xs text-muted-foreground">
            {label}
            <input
                className="mt-0.5 w-full rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
                value={(content[key] as string | undefined) ?? ''}
                onChange={(e) => onChange({ ...content, [key]: e.target.value })}
                aria-label={label}
            />
        </label>
    );
    const list = (key: keyof DraftContent, label: string) => (
        <label className="block text-xs text-muted-foreground">
            {label} <span className="text-[11px]">(one per line)</span>
            <textarea
                className="mt-0.5 w-full rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
                rows={Math.max(2, ((content[key] as string[] | undefined) ?? []).length + 1)}
                value={((content[key] as string[] | undefined) ?? []).join('\n')}
                onChange={(e) => onChange({ ...content, [key]: lines(e.target.value) })}
                aria-label={label}
            />
        </label>
    );
    return (
        <div className="mt-2 space-y-2" data-testid="draft-editor">
            {text('title', 'Name')}
            {text('description', 'What it helps with')}
            {text('example', 'Example')}
            {text('when_to_use', 'When to use it')}
            {list('steps', 'Steps')}
            {list('inputs', 'What it needs')}
            {list('outputs', 'What it produces')}
            {list('wont_do', 'What it will not do')}
        </div>
    );
}

export function LearningCard({
    event,
    onSettled,
}: {
    event: TimelineEvent;
    onSettled?: (event: TimelineEvent) => void;
}) {
    const payload = learningOf(event);
    const [saving, setSaving] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [draft, setDraft] = useState<DraftContent>(payload.content ?? {});
    const [editing, setEditing] = useState(false);
    const actions = ACTIONS[payload.type ?? ''];
    const decided = payload.decided;

    const press = async (action: string) => {
        setSaving(action);
        setError(null);
        if (payload.type === 'remembered' && action === 'publish' && editing && payload.version_id) {
            const saved = await editVersionApiV1EvolveVersionsVersionIdPut({
                path: { version_id: payload.version_id },
                body: { content: draft },
            });
            if (saved.error) {
                setSaving(null);
                setError(detailFromError(saved.error, 'Could not save that'));
                return;
            }
        }
        const result = await settleCardApiV1EvolveCardsSettlePost({
            body: { event_id: event.id, action },
        });
        setSaving(null);
        if (result.error) {
            setError(detailFromError(result.error, 'Could not do that'));
            return;
        }
        if (result.data) onSettled?.({ ...event, payload: result.data.payload });
    };

    const related = payload.evaluation?.related;
    const unrelated = payload.evaluation?.unrelated;
    const changes = payload.changes ?? [];
    const evidence = payload.evidence ?? [];

    return (
        <div className="max-w-2xl rounded-lg border border-border bg-card p-3" data-testid="learning-card">
            <p className="flex items-center gap-2 text-sm font-medium">
                <Sparkles className="h-4 w-4 text-[var(--accent-brand)]" aria-hidden />
                {heading(payload)}
            </p>

            {payload.type === 'offer' && (
                <>
                    {changes.length > 0 && (
                        <ul className="mt-2 space-y-1 text-sm" aria-label="What changes">
                            {changes.map((change, i) => (
                                <li key={i} className={change.op === 'remove' ? 'text-muted-foreground line-through' : ''}>
                                    {change.op === 'remove' ? '− ' : '+ '}
                                    {change.text}
                                </li>
                            ))}
                        </ul>
                    )}
                    {related && (
                        <p className="mt-2 text-xs text-muted-foreground" data-testid="learning-evaluation">
                            Held-out tasks of this kind: {related.candidate_passed ?? 0} of {related.n ?? 0} right,
                            against {related.baseline_passed ?? 0} before.
                            {unrelated
                                ? ` Unrelated tasks: ${(unrelated.regressions ?? []).length === 0 ? 'none got worse' : `${(unrelated.regressions ?? []).length} got worse`} (${unrelated.n ?? 0} checked).`
                                : ''}
                            {payload.cost
                                ? ` Testing it took ${payload.cost.model_calls ?? 0} model calls.`
                                : ''}
                        </p>
                    )}
                    {evidence.length > 0 && (
                        <details className="mt-2 text-xs">
                            <summary className="cursor-pointer text-muted-foreground">
                                Learned from {evidence.length} {evidence.length === 1 ? 'thing' : 'things'} that happened
                            </summary>
                            <ul className="mt-1 list-disc pl-5 text-muted-foreground">
                                {evidence.map((line, i) => (
                                    <li key={line.id ?? i}>{line.summary}</li>
                                ))}
                            </ul>
                        </details>
                    )}
                </>
            )}

            {payload.type === 'remembered' && (
                <>
                    {editing && !decided ? (
                        <DraftEditor content={draft} onChange={setDraft} />
                    ) : (
                        <div className="mt-2 space-y-1 text-sm">
                            {draft.description && <p>{draft.description}</p>}
                            {(draft.steps ?? []).length > 0 && (
                                <ol className="list-decimal pl-5" aria-label="Steps">
                                    {(draft.steps ?? []).map((step, i) => (
                                        <li key={i}>{step}</li>
                                    ))}
                                </ol>
                            )}
                        </div>
                    )}
                    {(payload.left_out ?? []).length > 0 && (
                        <p className="mt-2 text-xs text-muted-foreground" data-testid="left-out">
                            Left out: who it may contact, spending, calling hours and permissions stay in
                            Decibyl&apos;s own rules.
                        </p>
                    )}
                    {!decided && !editing && (
                        <button
                            type="button"
                            className="mt-1 text-xs underline underline-offset-4"
                            onClick={() => setEditing(true)}
                        >
                            Edit
                        </button>
                    )}
                </>
            )}

            {payload.type === 'disable' && payload.related_outcomes && (
                <p className="mt-2 text-xs text-muted-foreground">
                    Tasks went right {pct(payload.related_outcomes.before?.rate)} of the time before and{' '}
                    {pct(payload.related_outcomes.after?.rate)} since ({payload.related_outcomes.after?.n ?? 0} tasks).
                </p>
            )}
            {payload.type === 'disable' && payload.unrelated_outcomes && (
                <p className="mt-1 text-xs text-muted-foreground">
                    The same agents&apos; other work: {pct(payload.unrelated_outcomes.before?.rate)} before,{' '}
                    {pct(payload.unrelated_outcomes.after?.rate)} since.
                </p>
            )}

            {error && (
                <p className="mt-2 text-sm text-destructive" role="alert">
                    {error}
                </p>
            )}
            {decided ? (
                <p className="mt-2 flex items-center gap-1 text-xs text-muted-foreground" data-testid="learning-decided">
                    <Check className="h-3.5 w-3.5" aria-hidden />
                    {DECIDED[decided.action ?? ''] ?? 'Settled'}
                </p>
            ) : actions ? (
                <div className="mt-3 flex gap-2">
                    <Button size="sm" disabled={saving !== null} onClick={() => void press(actions[0])}>
                        {saving === actions[0] ? 'Working…' : actions[1]}
                    </Button>
                    <Button
                        size="sm"
                        variant="outline"
                        disabled={saving !== null}
                        onClick={() => void press(actions[2])}
                    >
                        {actions[3]}
                    </Button>
                </div>
            ) : null}
        </div>
    );
}
