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

import { AtSign, Brain, Check, ChevronDown, FileText, Loader2, Paperclip, SendHorizontal, X } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';

import { postMessageApiV1TimelineMessagePost } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { detailFromResult } from '@/lib/apiError';
import { CHAT_PRESETS, OWN_BRAIN, rememberedPreset,rememberPreset } from '@/lib/chatPresets';
import {
    ACCEPTED_FILE_TYPES,
    type KnowledgeTarget,
    rejectFile,
    type Uploaded,
    uploadKnowledge,
} from '@/lib/uploadKnowledge';
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
    /** Sent, with the bots it was handed to. */
    onSent?: (asked: number[]) => void;
}) {
    const [text, setText] = useState('');
    const [sending, setSending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [fragment, setFragment] = useState<string | null>(null);
    const [highlighted, setHighlighted] = useState(0);
    const input = useRef<HTMLTextAreaElement | null>(null);
    // Files already uploaded and waiting to go with the next message. The
    // upload happens on pick, not on send: a 5MB PDF takes a moment, and a
    // send button that stalls for it reads as broken.
    const [attachments, setAttachments] = useState<Uploaded[]>([]);
    const [uploadingFile, setUploadingFile] = useState<string | null>(null);
    const filePicker = useRef<HTMLInputElement | null>(null);

    // The brain for this message. Remembered per chat on this device.
    const chatKey = workflowId != null ? `bot:${workflowId}` : `channel:${folderId}`;
    const [preset, setPreset] = useState<string>(() => rememberedPreset(chatKey));
    const choosePreset = (slug: string) => {
        setPreset(slug);
        rememberPreset(chatKey, slug);
    };
    const presetLabel = CHAT_PRESETS.find((p) => p.slug === preset)?.label ?? OWN_BRAIN.label;

    // Where a dropped file is knowledge for: this chat, and nowhere else.
    const target: KnowledgeTarget | null =
        workflowId != null
            ? { scope: 'bot', workflowId }
            : folderId != null
              ? { scope: 'channel', folderId }
              : null;

    const attach = async (file: File) => {
        if (!target) return;
        const why = rejectFile(file);
        if (why) {
            setError(why);
            return;
        }
        setError(null);
        setUploadingFile(file.name);
        try {
            const uploaded = await uploadKnowledge(file, target);
            setAttachments((have) => [...have, uploaded]);
        } catch (failure) {
            setError(failure instanceof Error ? failure.message : 'Could not upload that');
        } finally {
            setUploadingFile(null);
            if (filePicker.current) filePicker.current.value = '';
        }
    };

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
        if ((!body && attachments.length === 0) || sending || uploadingFile) return;
        setSending(true);
        setError(null);
        setNotice(null);
        const where = workflowId != null ? { workflow_id: workflowId } : { folder_id: folderId };
        const response = await postMessageApiV1TimelineMessagePost({
            body: { ...where, text: body, attachments, preset: preset || null },
        });
        setSending(false);
        if (response.error) {
            setError(detailFromResult(response, 'Could not send that'));
            return;
        }
        setText('');
        setAttachments([]);
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
        onSent?.(response.data?.asked ?? []);
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

                {(attachments.length > 0 || uploadingFile) && (
                    <ul className="mb-2 flex flex-wrap gap-2" aria-label="Attachments">
                        {attachments.map((file) => (
                            <li
                                key={file.document_uuid}
                                className="flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs"
                            >
                                <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                                <span className="max-w-[12rem] truncate">{file.filename}</span>
                                <button
                                    type="button"
                                    aria-label={`Remove ${file.filename}`}
                                    className="text-muted-foreground hover:text-foreground"
                                    onClick={() =>
                                        setAttachments((have) =>
                                            have.filter((f) => f.document_uuid !== file.document_uuid),
                                        )
                                    }
                                >
                                    <X className="h-3 w-3" />
                                </button>
                            </li>
                        ))}
                        {uploadingFile && (
                            <li className="flex items-center gap-1.5 rounded-md border border-dashed border-border px-2 py-1 text-xs text-muted-foreground">
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                <span className="max-w-[12rem] truncate">{uploadingFile}</span>
                            </li>
                        )}
                    </ul>
                )}

                <div className="flex items-end gap-2">
                    {/* A file is a message. It is uploaded as knowledge for
                        this chat alone -- the channel's bots, or this one bot
                        -- and never lands in company knowledge by accident. */}
                    {target && (
                        <>
                            <input
                                ref={filePicker}
                                type="file"
                                accept={ACCEPTED_FILE_TYPES.join(',')}
                                className="hidden"
                                aria-label="Attach a file"
                                onChange={(event) => {
                                    const file = event.target.files?.[0];
                                    if (file) void attach(file);
                                }}
                            />
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                aria-label="Attach a file"
                                title="Attach a file"
                                disabled={!!uploadingFile}
                                className="shrink-0 text-muted-foreground"
                                onClick={() => filePicker.current?.click()}
                            >
                                <Paperclip className="h-4 w-4" />
                            </Button>
                        </>
                    )}
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
                    {/* How hard the bot should think about this one. A
                        product choice, not a vendor: each is a managed tier
                        the platform prices and resolves. */}
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                aria-label="Brain for this message"
                                title="Brain for this message"
                                className="shrink-0 gap-1 px-2 text-xs text-muted-foreground"
                            >
                                <Brain className="h-4 w-4" />
                                <span className="hidden sm:inline">{presetLabel}</span>
                                <ChevronDown className="h-3 w-3" />
                            </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-64">
                            {[OWN_BRAIN, ...CHAT_PRESETS].map((option) => (
                                <DropdownMenuItem
                                    key={option.slug || 'own'}
                                    onSelect={() => choosePreset(option.slug)}
                                    className="flex items-start gap-2"
                                >
                                    <Check
                                        className={cn(
                                            'mt-0.5 h-3.5 w-3.5 shrink-0',
                                            (preset || '') === option.slug ? 'opacity-100' : 'opacity-0',
                                        )}
                                    />
                                    <span className="flex flex-col">
                                        <span className="text-sm">{option.label}</span>
                                        <span className="text-xs text-muted-foreground">{option.blurb}</span>
                                    </span>
                                </DropdownMenuItem>
                            ))}
                        </DropdownMenuContent>
                    </DropdownMenu>
                    <Button
                        onClick={() => void send()}
                        disabled={(!text.trim() && attachments.length === 0) || sending || !!uploadingFile}
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
