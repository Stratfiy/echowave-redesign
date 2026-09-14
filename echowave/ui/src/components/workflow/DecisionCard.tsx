'use client';

/**
 * A bot asked; a person answers here.
 *
 * The reference screens (ChatGPT's bot desktop, Slack's approvals) agree on
 * the shape: the question, one line of why, a vertical list of options, and
 * once answered a stamp on the same card saying what was decided. The card
 * that asked is the card that shows the answer, for everyone who opens the
 * channel afterwards -- so the answer is written into the question's own row
 * (see services/workflow/decisions.py) and the card reads `payload.decided`.
 *
 * Three modes from the payload: `single` (radios), `multi` (checkboxes),
 * `approve` (two buttons, no list). `allow_other` adds a line to type in.
 */

import { Check, CircleHelp } from 'lucide-react';
import { useState } from 'react';

import { decideApiV1TimelineDecidePost } from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { detailFromError } from '@/lib/apiError';
import { cn } from '@/lib/utils';

export type DecisionPayload = {
    question?: string;
    why?: string;
    options?: string[];
    mode?: 'single' | 'multi' | 'approve';
    allow_other?: boolean;
    decided?: { choice?: string[]; other?: string | null; by?: number; at?: string };
};

export function decisionOf(event: TimelineEvent): DecisionPayload {
    return (event.payload ?? {}) as DecisionPayload;
}

export function DecisionCard({
    event,
    onDecided,
}: {
    event: TimelineEvent;
    /** The updated row, so the list holding this card can replace it. */
    onDecided?: (event: TimelineEvent) => void;
}) {
    const decision = decisionOf(event);
    const options = decision.options ?? [];
    const mode = decision.mode ?? 'single';
    const [picked, setPicked] = useState<string[]>([]);
    const [other, setOther] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const submit = async (choice: string[]) => {
        setSaving(true);
        setError(null);
        const result = await decideApiV1TimelineDecidePost({
            body: { event_id: event.id, choice, other: other.trim() || null },
        });
        setSaving(false);
        if (result.error) {
            setError(detailFromError(result.error, 'Could not record that'));
            return;
        }
        if (result.data) onDecided?.(result.data);
    };

    const toggle = (option: string) =>
        setPicked((current) =>
            mode === 'multi'
                ? current.includes(option)
                    ? current.filter((o) => o !== option)
                    : [...current, option]
                : [option],
        );

    const decided = decision.decided;
    const answer = decided
        ? [...(decided.choice ?? []), ...(decided.other ? [decided.other] : [])].join(', ')
        : null;

    return (
        <div
            className="rounded-lg border border-border bg-card p-4"
            role="group"
            aria-label={decision.question ?? event.summary}
        >
            <p className="flex items-start gap-2 text-sm font-medium">
                <CircleHelp aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                <span>{decision.question ?? event.summary}</span>
            </p>
            {decision.why && (
                <p className="mt-1 pl-6 text-sm text-muted-foreground">{decision.why}</p>
            )}

            {decided ? (
                // The stamp. Muted and past tense: this is a record now, not a
                // control, and a second person opening the channel should read
                // it as settled rather than reach for a button.
                <p className="mt-3 flex items-center gap-1.5 pl-6 text-sm">
                    <Check aria-hidden className="h-4 w-4 text-emerald-600" />
                    <span className="font-medium">{answer}</span>
                    <span className="text-muted-foreground">
                        · {mode === 'approve' ? 'decided' : 'chosen'}
                    </span>
                </p>
            ) : mode === 'approve' ? (
                <div className="mt-3 flex gap-2 pl-6">
                    <Button size="sm" disabled={saving} onClick={() => void submit(['Approve'])}>
                        Approve
                    </Button>
                    <Button
                        size="sm"
                        variant="outline"
                        disabled={saving}
                        onClick={() => void submit(['Reject'])}
                    >
                        Reject
                    </Button>
                </div>
            ) : (
                <div className="mt-3 pl-6">
                    <ul className="flex flex-col gap-1">
                        {options.map((option) => {
                            const on = picked.includes(option);
                            return (
                                <li key={option}>
                                    <label
                                        className={cn(
                                            'flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm',
                                            on ? 'border-primary bg-accent' : 'border-border hover:bg-accent/50',
                                        )}
                                    >
                                        <input
                                            type={mode === 'multi' ? 'checkbox' : 'radio'}
                                            name={`decision-${event.id}`}
                                            checked={on}
                                            onChange={() => toggle(option)}
                                            className="h-3.5 w-3.5"
                                        />
                                        {option}
                                    </label>
                                </li>
                            );
                        })}
                    </ul>
                    {decision.allow_other && (
                        <Input
                            aria-label="Something else"
                            placeholder="Something else…"
                            value={other}
                            onChange={(e) => setOther(e.target.value)}
                            className="mt-2"
                        />
                    )}
                    <div className="mt-3 flex items-center gap-3">
                        <Button
                            size="sm"
                            disabled={saving || (picked.length === 0 && !other.trim())}
                            onClick={() => void submit(picked)}
                        >
                            {saving ? 'Saving…' : mode === 'multi' ? 'Choose these' : 'Choose'}
                        </Button>
                        {error && (
                            <span role="alert" className="text-sm text-destructive">
                                {error}
                            </span>
                        )}
                    </div>
                </div>
            )}
            {error && mode === 'approve' && (
                <p role="alert" className="mt-2 pl-6 text-sm text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}
