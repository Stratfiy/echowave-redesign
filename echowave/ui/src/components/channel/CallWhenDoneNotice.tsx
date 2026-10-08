'use client';

import { Phone } from 'lucide-react';
import { useState } from 'react';

import { askToBeCalledApiV1CallWhenDonePost } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { detailFromError } from '@/lib/apiError';
import { useFeature } from '@/lib/features';

/** What a "call me when it's done" line carries (services/call_when_done). */
export interface CallWhenDoneInfo {
    state?: string;
    needs_number?: boolean;
    reason?: string | null;
    due_at?: string | null;
}

export function callWhenDoneOf(payload: unknown): CallWhenDoneInfo | null {
    const info = (payload as { call_when_done?: unknown } | null)?.call_when_done;
    return info && typeof info === 'object' ? (info as CallWhenDoneInfo) : null;
}

/**
 * Decibyl's line about a call when work finishes: when it will ring, or why
 * it told you here instead. When no number is on file yet, the number is
 * asked for right here, in the thread -- it then appears on a card to
 * confirm once, never on another screen.
 */
export function CallWhenDoneNotice({
    body,
    info,
    threadId,
    onAsked,
}: {
    body: string;
    info: CallWhenDoneInfo;
    threadId?: string | null;
    onAsked?: () => void;
}) {
    const on = useFeature('call_when_done');
    const [phone, setPhone] = useState('');
    const [sending, setSending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [sent, setSent] = useState(false);

    const send = async () => {
        setSending(true);
        setError(null);
        const response = await askToBeCalledApiV1CallWhenDonePost({
            body: { thread_id: threadId ?? null, phone: phone.trim() },
        });
        setSending(false);
        if (response.error) {
            setError(detailFromError(response.error, 'Could not use that number'));
            return;
        }
        setSent(true);
        onAsked?.();
    };

    return (
        <div
            className="rounded-[var(--radius-card,0.75rem)] border border-border bg-card px-3 py-2"
            data-testid="call-when-done-notice"
            data-state={info.state ?? ''}
        >
            <p className="flex items-start gap-2 text-sm">
                <Phone aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-[var(--accent-brand)]" />
                <span>{body}</span>
            </p>
            {on && info.needs_number && !sent && (
                <form
                    className="mt-2 flex flex-wrap items-center gap-2"
                    onSubmit={(e) => {
                        e.preventDefault();
                        if (phone.trim()) void send();
                    }}
                >
                    <label className="sr-only" htmlFor="call-when-done-phone">
                        Number to ring
                    </label>
                    <Input
                        id="call-when-done-phone"
                        type="tel"
                        inputMode="tel"
                        autoComplete="tel"
                        placeholder="+91 98765 43210"
                        value={phone}
                        onChange={(e) => setPhone(e.target.value)}
                        className="h-9 w-48"
                    />
                    <Button type="submit" size="sm" disabled={sending || !phone.trim()}>
                        {sending ? 'Sending…' : 'Show it on a card'}
                    </Button>
                </form>
            )}
            {sent && (
                <p className="mt-1 text-xs text-muted-foreground">Confirm the number on the card in this thread.</p>
            )}
            {error && (
                <p role="alert" className="mt-1 text-xs text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}
