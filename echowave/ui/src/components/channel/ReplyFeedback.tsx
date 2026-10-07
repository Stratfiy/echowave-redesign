'use client';

/**
 * "Was this useful?" under one of Decibyl's replies (handoff 6, "Useful
 * feedback"; launch stream controls).
 *
 * Yes / Not quite, then optional reasons -- wrong, not relevant, too late,
 * too long, wrong language -- and a close button, because it is offered,
 * never required. Each press is saved straight away (services/feedback.py
 * stores it against the reply's version and the model that wrote it); a
 * failed save says so and keeps the choice on screen, never a fake "Thanks".
 * It informs evaluation only: it is not permission to act on anything.
 *
 * Hidden unless `reply_feedback` is on. What this person already said is
 * read once per batch of replies (`useMyFeedback`), so a reload shows their
 * answer instead of asking again.
 */

import { X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
    clientEventApiV1EventsClientPost,
    giveFeedbackApiV1FeedbackPost,
    myFeedbackApiV1FeedbackMineGet,
} from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { useFeature } from '@/lib/features';
import { cn } from '@/lib/utils';

export const REASONS: { code: string; label: string }[] = [
    { code: 'wrong', label: 'Wrong' },
    { code: 'irrelevant', label: 'Not relevant' },
    { code: 'too_late', label: 'Too late' },
    { code: 'too_long', label: 'Too long' },
    { code: 'wrong_language', label: 'Wrong language' },
];

export type Answer = { verdict: 'yes' | 'not_quite'; reasons: string[] };

const SAVE_FAILED = 'Your feedback was not saved. Try again.';
const DISMISSED_KEY = 'decibyl:feedback-dismissed';

/** A reply this person can judge: Decibyl's own answer on its thread, not
 *  a bot's, not a channel's, and not the line saying a limit was reached. */
export function isJudgeableReply(event: TimelineEvent): boolean {
    const payload = (event.payload ?? {}) as Record<string, unknown>;
    return (
        event.kind === 'message' &&
        event.actor === 'agent' &&
        event.workflow_id == null &&
        event.folder_id == null &&
        typeof payload.from === 'string' &&
        !payload.quota &&
        !payload.action_event_id
    );
}

function readDismissed(): Set<number> {
    try {
        const raw = window.localStorage.getItem(DISMISSED_KEY);
        return new Set<number>(raw ? (JSON.parse(raw) as number[]) : []);
    } catch {
        return new Set<number>();
    }
}

function writeDismissed(ids: Set<number>) {
    try {
        // The newest few hundred are enough: an old reply scrolled far away
        // asking again is harmless.
        window.localStorage.setItem(DISMISSED_KEY, JSON.stringify([...ids].slice(-300)));
    } catch {
        // Private mode or blocked storage: it is dismissed for this visit.
    }
}

/** What this person already said about these replies. Asks only for ids it
 *  has not asked about; a failed read leaves the prompt as it was. */
export function useMyFeedback(ids: number[], enabled: boolean) {
    const [answers, setAnswers] = useState<Record<number, Answer>>({});
    const asked = useRef<Set<number>>(new Set());
    const key = ids.join(',');

    useEffect(() => {
        if (!enabled) return;
        const fresh = ids.filter((id) => !asked.current.has(id));
        if (fresh.length === 0) return;
        fresh.forEach((id) => asked.current.add(id));
        void (async () => {
            const response = await myFeedbackApiV1FeedbackMineGet({
                query: { subject_kind: 'reply', ids: fresh },
            });
            if (response.error || !response.data) return;
            const found = response.data.answers as Record<string, Answer>;
            setAnswers((all) => {
                const next = { ...all };
                for (const [id, answer] of Object.entries(found)) next[Number(id)] = answer;
                return next;
            });
        })();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, key]);

    const remember = useCallback((id: number, answer: Answer) => {
        setAnswers((all) => ({ ...all, [id]: answer }));
    }, []);
    return { answers, remember };
}

export function ReplyFeedback({
    eventId,
    answer,
    onAnswered,
}: {
    eventId: number;
    answer?: Answer;
    onAnswered: (answer: Answer) => void;
}) {
    const catalogueOn = useFeature('event_catalogue');
    const [dismissed, setDismissed] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // The choice on screen, which may be ahead of what is saved while a
    // save is in flight or after one failed.
    const [shown, setShown] = useState<Answer | undefined>(answer);

    useEffect(() => {
        if (answer) setShown(answer);
    }, [answer]);
    useEffect(() => {
        setDismissed(readDismissed().has(eventId));
    }, [eventId]);

    const save = async (next: Answer) => {
        setShown(next);
        setSaving(true);
        setError(null);
        const response = await giveFeedbackApiV1FeedbackPost({
            body: {
                subject_kind: 'reply',
                subject_id: eventId,
                verdict: next.verdict,
                reasons: next.reasons,
            },
        });
        setSaving(false);
        if (response.error) {
            // The design's copy, whatever the server said: the person's
            // next step is the same either way.
            setError(SAVE_FAILED);
            return;
        }
        onAnswered(next);
    };

    const dismiss = () => {
        const ids = readDismissed();
        ids.add(eventId);
        writeDismissed(ids);
        setDismissed(true);
        if (catalogueOn) {
            // Intent only, and nothing about the reply: the catalogue
            // refuses anything else from a browser.
            void clientEventApiV1EventsClientPost({ body: { name: 'feedback_prompt_dismissed' } });
        }
    };

    if (dismissed && !shown) return null;

    if (shown?.verdict === 'yes' && !error) {
        return (
            <p className="mt-1.5 text-xs text-muted-foreground" aria-live="polite">
                {saving ? 'Saving…' : 'Thanks, marked as useful.'}
            </p>
        );
    }

    return (
        <div className="mt-1.5 text-xs" role="group" aria-label="Was this useful?">
            <div className="flex flex-wrap items-center gap-1.5">
                {!shown && <span className="text-muted-foreground">Was this useful?</span>}
                {shown?.verdict === 'not_quite' && (
                    <span className="text-muted-foreground">What was off? (optional)</span>
                )}
                {!shown && (
                    <>
                        <button
                            type="button"
                            disabled={saving}
                            className="rounded-full border border-border px-2 py-0.5 hover:bg-accent disabled:opacity-50"
                            onClick={() => void save({ verdict: 'yes', reasons: [] })}
                        >
                            Yes
                        </button>
                        <button
                            type="button"
                            disabled={saving}
                            className="rounded-full border border-border px-2 py-0.5 hover:bg-accent disabled:opacity-50"
                            onClick={() => void save({ verdict: 'not_quite', reasons: [] })}
                        >
                            Not quite
                        </button>
                    </>
                )}
                {shown?.verdict === 'not_quite' &&
                    REASONS.map((reason) => {
                        const on = shown.reasons.includes(reason.code);
                        return (
                            <button
                                key={reason.code}
                                type="button"
                                aria-pressed={on}
                                disabled={saving}
                                className={cn(
                                    'rounded-full border px-2 py-0.5 disabled:opacity-50',
                                    on
                                        ? 'border-foreground bg-foreground text-background'
                                        : 'border-border hover:bg-accent',
                                )}
                                onClick={() =>
                                    void save({
                                        verdict: 'not_quite',
                                        reasons: on
                                            ? shown.reasons.filter((r) => r !== reason.code)
                                            : [...shown.reasons, reason.code],
                                    })
                                }
                            >
                                {reason.label}
                            </button>
                        );
                    })}
                {!shown && (
                    <button
                        type="button"
                        aria-label="Close"
                        className="rounded p-0.5 text-muted-foreground hover:bg-accent"
                        onClick={dismiss}
                    >
                        <X aria-hidden className="h-3.5 w-3.5" />
                    </button>
                )}
            </div>
            {error && (
                <p role="alert" className="mt-1 text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}
