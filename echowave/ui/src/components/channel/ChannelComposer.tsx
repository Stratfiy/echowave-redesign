'use client';

/**
 * Saying something in a channel, and addressing a bot while you do.
 *
 * The autocomplete is the point, not decoration. A handle is only useful if
 * somebody can find it without having memorised it, and the alternative —
 * typing a name you half-remember and getting silence — is precisely the
 * failure `mentions.py` refuses to paper over by guessing. Offering the
 * roster as you type means the common case never reaches the "no bot called
 * that" branch at all.
 *
 * What it does NOT do is send optimistically. The bot's reply arrives from a
 * worker a moment later and the stream polls for it, so a message drawn
 * locally before the server has it would be a message that looks sent and may
 * not be — and the one it is followed by would then be answering something the
 * channel has no record of.
 */

import { AtSign, Loader2, SendHorizontal } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';

import { postMessageApiV1TimelineMessagePost } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import { detailFromResult } from '@/lib/apiError';
import { cn } from '@/lib/utils';

export type ChannelBot = { id: number; name: string; handle?: string | null };

/** The handle a bot answers to, falling back to one derived from its name.
 *
 *  The same fallback the server applies, and for the same reason: a bot whose
 *  handle was never assigned is still addressable, rather than being quietly
 *  unaddressable with nothing on screen to say so. Kept deliberately simple —
 *  this suggests, the server resolves. */
export function handleOf(bot: ChannelBot): string {
    if (bot.handle) return bot.handle;
    return (bot.name || '')
        .normalize('NFKD')
        .replace(/[̀-ͯ]/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9\s_-]+/g, ' ')
        .trim()
        .replace(/[\s_-]+/g, '-');
}

/** The handle fragment being typed at the caret, or null.
 *
 *  Mirrors the server's boundary rule: a mention starts at the beginning or
 *  after whitespace, so "ramesh@clinic.example" offers nothing. Without that,
 *  typing a customer's email address pops a bot list mid-word. */
export function mentionFragment(text: string, caret: number): string | null {
    const before = text.slice(0, caret);
    const match = /(?:^|\s)@([a-z0-9_-]*)$/i.exec(before);
    return match ? match[1].toLowerCase() : null;
}

export function ChannelComposer({
    folderId,
    workflowId,
    bots,
    channelName,
    onSent,
}: {
    /** A channel, or -- with `workflowId` instead -- one bot's own chat, where
     *  there is nobody to @ because the bot is implied. */
    folderId?: number;
    workflowId?: number;
    bots: ChannelBot[];
    channelName: string;
    onSent?: () => void;
}) {
    const [text, setText] = useState('');
    const [sending, setSending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [fragment, setFragment] = useState<string | null>(null);
    const [highlighted, setHighlighted] = useState(0);
    const input = useRef<HTMLTextAreaElement | null>(null);

    const suggestions = useMemo(() => {
        if (fragment === null) return [];
        return bots
            .map((bot) => ({ bot, handle: handleOf(bot) }))
            .filter(({ handle }) => handle && handle.startsWith(fragment))
            .slice(0, 6);
    }, [bots, fragment]);

    const syncFragment = (value: string, caret: number) => {
        const next = mentionFragment(value, caret);
        setFragment(next);
        setHighlighted(0);
    };

    const complete = (handle: string) => {
        const element = input.current;
        const caret = element?.selectionStart ?? text.length;
        const before = text.slice(0, caret).replace(/@[a-z0-9_-]*$/i, `@${handle} `);
        const next = before + text.slice(caret);
        setText(next);
        setFragment(null);
        // Put the caret back after the handle rather than at the end: the
        // person is mid-sentence, and a caret that jumps to the end of a
        // message they are editing is a message they have to repair.
        requestAnimationFrame(() => {
            element?.focus();
            element?.setSelectionRange(before.length, before.length);
        });
    };

    const send = async () => {
        const body = text.trim();
        if (!body || sending) return;
        setSending(true);
        setError(null);
        setNotice(null);
        const response = await postMessageApiV1TimelineMessagePost({
            body: workflowId != null ? { workflow_id: workflowId, text: body } : { folder_id: folderId, text: body },
        });
        setSending(false);
        if (response.error) {
            setError(detailFromResult(response, 'Could not send that'));
            return;
        }
        setText('');
        setFragment(null);

        // The server refuses to guess, and the screen has to say so. A handle
        // that matched nothing leaves somebody waiting on a reply that was
        // never coming; an ambiguous one is a naming collision that would
        // otherwise hide behind a bot that sometimes answers.
        const unknown = response.data?.unknown ?? [];
        const ambiguous = response.data?.ambiguous ?? [];
        const said: string[] = [];
        if (unknown.length) {
            said.push(
                `There is nobody here called ${unknown.map((h) => `@${h}`).join(', ')}.`,
            );
        }
        if (ambiguous.length) {
            said.push(
                `More than one bot here answers to ${ambiguous
                    .map((h) => `@${h}`)
                    .join(', ')}, so none of them was asked.`,
            );
        }
        setNotice(said.join(' ') || null);
        onSent?.();
    };

    return (
        <div className="border-t border-border bg-card px-6 py-3">
            {error && (
                <p className="mb-2 text-sm text-destructive" role="alert">
                    {error}
                </p>
            )}
            {notice && (
                <p className="mb-2 text-sm text-amber-600" role="status">
                    {notice}
                </p>
            )}

            <div className="relative">
                {suggestions.length > 0 && (
                    <ul
                        role="listbox"
                        aria-label="Bots in this channel"
                        className="absolute bottom-full mb-1 w-full max-w-sm overflow-hidden rounded-md border border-border bg-popover shadow-md"
                    >
                        {suggestions.map(({ bot, handle }, index) => (
                            <li key={bot.id}>
                                <button
                                    type="button"
                                    role="option"
                                    aria-selected={index === highlighted}
                                    onMouseDown={(event) => {
                                        // mousedown, not click: the textarea
                                        // blurs first otherwise and the caret
                                        // position this needs is gone.
                                        event.preventDefault();
                                        complete(handle);
                                    }}
                                    className={cn(
                                        'flex w-full flex-col items-start px-3 py-1.5 text-left text-sm',
                                        index === highlighted
                                            ? 'bg-accent'
                                            : 'hover:bg-accent',
                                    )}
                                >
                                    <span className="font-medium">{bot.name}</span>
                                    <span className="text-xs text-muted-foreground">
                                        @{handle}
                                    </span>
                                </button>
                            </li>
                        ))}
                    </ul>
                )}

                <div className="flex items-end gap-2">
                    {/* The mockup's affordance, and the discoverable half of
                        the autocomplete: typing @ opens the roster, and this
                        types the @ for somebody who did not know that. */}
                    {workflowId == null && (
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label="Mention a bot"
                        title="Mention a bot"
                        className="shrink-0 text-muted-foreground"
                        onMouseDown={(event) => {
                            event.preventDefault();
                            const element = input.current;
                            const caret = element?.selectionStart ?? text.length;
                            const before = text.slice(0, caret);
                            // A space first if the caret is mid-word, so the
                            // mention starts at a boundary the server accepts.
                            const lead = before && !/\s$/.test(before) ? ' ' : '';
                            const next = `${before}${lead}@${text.slice(caret)}`;
                            setText(next);
                            const at = before.length + lead.length + 1;
                            requestAnimationFrame(() => {
                                element?.focus();
                                element?.setSelectionRange(at, at);
                                syncFragment(next, at);
                            });
                        }}
                    >
                        <AtSign className="h-4 w-4" />
                    </Button>
                    )}
                    <textarea
                        ref={input}
                        rows={1}
                        value={text}
                        aria-label={`Message ${channelName}`}
                        placeholder={
                            workflowId != null
                                ? `Message ${channelName}`
                                : `Message #${channelName} — @ a bot to ask it for something`
                        }
                        className="max-h-40 min-h-[38px] flex-1 resize-y rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring"
                        onChange={(event) => {
                            setText(event.target.value);
                            syncFragment(
                                event.target.value,
                                event.target.selectionStart ?? 0,
                            );
                        }}
                        onClick={(event) =>
                            syncFragment(
                                text,
                                event.currentTarget.selectionStart ?? 0,
                            )
                        }
                        onKeyDown={(event) => {
                            if (suggestions.length > 0) {
                                if (event.key === 'ArrowDown') {
                                    event.preventDefault();
                                    setHighlighted((i) => (i + 1) % suggestions.length);
                                    return;
                                }
                                if (event.key === 'ArrowUp') {
                                    event.preventDefault();
                                    setHighlighted(
                                        (i) =>
                                            (i - 1 + suggestions.length) %
                                            suggestions.length,
                                    );
                                    return;
                                }
                                if (event.key === 'Tab' || event.key === 'Enter') {
                                    event.preventDefault();
                                    complete(suggestions[highlighted].handle);
                                    return;
                                }
                                if (event.key === 'Escape') {
                                    setFragment(null);
                                    return;
                                }
                            }
                            // Shift+Enter for a newline, the convention every
                            // chat product shares. A message that sends on the
                            // key somebody uses to start a second line is a
                            // message sent half-written.
                            if (event.key === 'Enter' && !event.shiftKey) {
                                event.preventDefault();
                                void send();
                            }
                        }}
                    />
                    <Button
                        onClick={() => void send()}
                        disabled={!text.trim() || sending}
                        aria-label="Send"
                    >
                        {sending ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                            <SendHorizontal className="h-4 w-4" />
                        )}
                    </Button>
                </div>
            </div>
        </div>
    );
}

export default ChannelComposer;
