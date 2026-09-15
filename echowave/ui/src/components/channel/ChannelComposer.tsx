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

import { AtSign, Brain, Check, ChevronDown, FileText, Hash, Loader2, Mic, Paperclip, SendHorizontal, Square, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { listFoldersApiV1FolderGet, postMessageApiV1TimelineMessagePost, translateTextApiV1TranslatePost } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { detailFromResult } from '@/lib/apiError';
import { CHAT_PRESETS, OWN_BRAIN, rememberedPreset,rememberPreset } from '@/lib/chatPresets';
import { hasIndicScript } from '@/lib/indic';
import {
    ACCEPTED_FILE_TYPES,
    type KnowledgeTarget,
    rejectFile,
    type Uploaded,
    uploadKnowledge,
} from '@/lib/uploadKnowledge';
import { appendDictation, useDictation } from '@/lib/useDictation';
import { cn } from '@/lib/utils';

import { Waveform } from './Waveform';

export type ChannelBot = { id: number; name: string; handle?: string | null };
/** A channel a message can be sent to with `#`. */
export type ChannelRef = { id: number; name: string };

/** The slug a channel answers to: its name, lower-case, spaces as dashes. */
export function channelSlug(channel: ChannelRef): string {
    return channel.name
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
}

export type Tag = { kind: 'bot' | 'channel'; fragment: string };

/** What is being typed at the caret: `@` a bot or `#` a channel, or null.
 *  Same boundary rule as `mentionFragment`: at the start or after a space. */
export function tagFragment(text: string, caret: number): Tag | null {
    const before = text.slice(0, caret);
    const match = /(?:^|\s)([@#])([a-z0-9_-]*)$/i.exec(before);
    if (!match) return null;
    return { kind: match[1] === '@' ? 'bot' : 'channel', fragment: match[2].toLowerCase() };
}

/** Every `@handle` and `#channel` in a message, for painting them blue. */
export function tagTokens(text: string): { text: string; tag: boolean }[] {
    const out: { text: string; tag: boolean }[] = [];
    const re = /(^|\s)([@#][a-z0-9_-]+)/gi;
    let last = 0;
    for (const match of text.matchAll(re)) {
        const start = (match.index ?? 0) + match[1].length;
        if (start > last) out.push({ text: text.slice(last, start), tag: false });
        out.push({ text: match[2], tag: true });
        last = start + match[2].length;
    }
    if (last < text.length) out.push({ text: text.slice(last), tag: false });
    return out;
}

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
    const tag = tagFragment(text, caret);
    return tag && tag.kind === 'bot' ? tag.fragment : null;
}

export function ChannelComposer({
    folderId,
    workflowId,
    assistant = false,
    bots,
    channels,
    channelName,
    onSent,
}: {
    /** A channel, or -- with `workflowId` instead -- one bot's own chat, where
     *  there is nobody to @ because the bot is implied. */
    folderId?: number;
    workflowId?: number;
    /** Decibyl's own thread: neither a channel nor a bot. */
    assistant?: boolean;
    bots: ChannelBot[];
    /** The channels `#` offers. Fetched here when not given. */
    channels?: ChannelRef[];
    channelName: string;
    /** Sent, with the bots it was handed to. */
    onSent?: (asked: number[]) => void;
}) {
    const [text, setText] = useState('');
    const [sending, setSending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [tag, setTag] = useState<Tag | null>(null);
    const [highlighted, setHighlighted] = useState(0);
    // `#channel` chosen: the message goes there, and a blue chip says so.
    const [routeTo, setRouteTo] = useState<ChannelRef | null>(null);
    const [fetchedChannels, setFetchedChannels] = useState<ChannelRef[]>([]);
    const channelList = channels ?? fetchedChannels;
    useEffect(() => {
        if (channels) return;
        let cancelled = false;
        void (async () => {
            const response = await listFoldersApiV1FolderGet();
            if (cancelled || response.error || !response.data) return;
            setFetchedChannels(response.data.map((f) => ({ id: f.id, name: f.name })));
        })();
        return () => {
            cancelled = true;
        };
    }, [channels]);
    const mirror = useRef<HTMLDivElement | null>(null);
    const input = useRef<HTMLTextAreaElement | null>(null);
    // Files already uploaded and waiting to go with the next message. The
    // upload happens on pick, not on send: a 5MB PDF takes a moment, and a
    // send button that stalls for it reads as broken.
    const [attachments, setAttachments] = useState<Uploaded[]>([]);
    const [uploadingFile, setUploadingFile] = useState<string | null>(null);
    const filePicker = useRef<HTMLInputElement | null>(null);

    // The brain for this message. Remembered per chat on this device.
    const chatKey =
        workflowId != null ? `bot:${workflowId}` : assistant ? 'assistant' : `channel:${folderId}`;
    const [preset, setPreset] = useState<string>(() => rememberedPreset(chatKey));
    const choosePreset = (slug: string) => {
        setPreset(slug);
        rememberPreset(chatKey, slug);
    };
    const presetLabel = CHAT_PRESETS.find((p) => p.slug === preset)?.label ?? OWN_BRAIN.label;

    // Where a dropped file is knowledge for: this chat, and nowhere else.
    // Decibyl is the exception on purpose: it is the workspace's own
    // assistant and answers from Company knowledge, so a PDF handed to it
    // lands there -- the one place every bot can read it from.
    const target: KnowledgeTarget | null =
        workflowId != null
            ? { scope: 'bot', workflowId }
            : folderId != null
              ? { scope: 'channel', folderId }
              : assistant
                ? { scope: 'org' }
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

    // A draft in an Indian script gets two offers: say it in English, or
    // keep the words and write them in Roman letters. Only then -- the
    // offers are not a feature of the box, they are a reply to what was
    // typed.
    const [converting, setConverting] = useState<string | null>(null);
    const convertDraft = async (mode: 'translate' | 'transliterate') => {
        const draft = text.trim();
        if (!draft || converting) return;
        setConverting(mode);
        setError(null);
        const response = await translateTextApiV1TranslatePost({ body: { text: draft, mode } });
        setConverting(null);
        if (response.error) {
            setError(detailFromResult(response, 'Could not translate that'));
            return;
        }
        if (response.data?.text) setText(response.data.text);
    };

    // Talk instead of type. The words land in the box, not on the wire:
    // the person reads them, fixes a name the model misheard, and sends.
    const dictation = useDictation((transcript) => {
        setText((was) => appendDictation(was, transcript));
        requestAnimationFrame(() => input.current?.focus());
    });

    const suggestions = useMemo(() => {
        if (tag === null) return [];
        if (tag.kind === 'channel') {
            return channelList
                .map((channel) => ({ kind: 'channel' as const, id: channel.id, name: channel.name, handle: channelSlug(channel), channel }))
                .filter(({ handle }) => handle && handle.startsWith(tag.fragment))
                .slice(0, 6);
        }
        return bots
            .map((bot) => ({ kind: 'bot' as const, id: bot.id, name: bot.name, handle: handleOf(bot), bot }))
            .filter(({ handle }) => handle && handle.startsWith(tag.fragment))
            .slice(0, 6);
    }, [bots, channelList, tag]);

    const syncFragment = (value: string, caret: number) => {
        setTag(tagFragment(value, caret));
        setHighlighted(0);
    };

    const complete = (choice: (typeof suggestions)[number]) => {
        const element = input.current;
        const caret = element?.selectionStart ?? text.length;
        const sigil = choice.kind === 'bot' ? '@' : '#';
        const before = text.slice(0, caret).replace(/[@#][a-z0-9_-]*$/i, `${sigil}${choice.handle} `);
        const next = before + text.slice(caret);
        if (choice.kind === 'channel') setRouteTo(choice.channel);
        setText(next);
        setTag(null);
        // Put the caret back after the handle rather than at the end: the
        // person is mid-sentence, and a caret that jumps to the end of a
        // message they are editing is a message they have to repair.
        requestAnimationFrame(() => {
            element?.focus();
            element?.setSelectionRange(before.length, before.length);
        });
    };

    const typeSigil = (sigil: '@' | '#') => {
        const element = input.current;
        const caret = element?.selectionStart ?? text.length;
        const before = text.slice(0, caret);
        const lead = before && !/\s$/.test(before) ? ' ' : '';
        const next = `${before}${lead}${sigil}${text.slice(caret)}`;
        setText(next);
        const at = before.length + lead.length + 1;
        requestAnimationFrame(() => {
            element?.focus();
            element?.setSelectionRange(at, at);
            syncFragment(next, at);
        });
    };

    const send = async () => {
        const body = text.trim();
        if ((!body && attachments.length === 0) || sending || uploadingFile) return;
        setSending(true);
        setError(null);
        setNotice(null);
        const where = routeTo
            ? { folder_id: routeTo.id }
            : assistant
              ? { assistant: true }
              : workflowId != null
                ? { workflow_id: workflowId }
                : { folder_id: folderId };
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
        setTag(null);
        setRouteTo(null);

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
            {(error || dictation.error) && (
                <p className="mb-2 text-sm text-destructive" role="alert">
                    {error || dictation.error}
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
                        aria-label={tag?.kind === 'channel' ? 'Channels' : 'Bots in this channel'}
                        className="absolute bottom-full mb-1 w-full max-w-sm overflow-hidden rounded-md border border-border bg-popover shadow-md"
                    >
                        {suggestions.map((choice, index) => (
                            <li key={`${choice.kind}-${choice.id}`}>
                                <button
                                    type="button"
                                    role="option"
                                    aria-selected={index === highlighted}
                                    onMouseDown={(event) => {
                                        // mousedown, not click: the textarea
                                        // blurs first otherwise and the caret
                                        // position this needs is gone.
                                        event.preventDefault();
                                        complete(choice);
                                    }}
                                    className={cn(
                                        'flex w-full flex-col items-start px-3 py-1.5 text-left text-sm',
                                        index === highlighted
                                            ? 'bg-accent'
                                            : 'hover:bg-accent',
                                    )}
                                >
                                    <span className="font-medium">{choice.name}</span>
                                    <span className="text-xs text-[var(--brand-blue)]">
                                        {choice.kind === 'bot' ? '@' : '#'}{choice.handle}
                                    </span>
                                </button>
                            </li>
                        ))}
                    </ul>
                )}

                {hasIndicScript(text) && (
                    <div className="mb-2 flex flex-wrap items-center gap-2 text-xs" aria-label="Language offers">
                        <button
                            type="button"
                            disabled={!!converting}
                            onClick={() => void convertDraft('translate')}
                            className="rounded-md border border-border bg-background px-2 py-1 hover:bg-accent disabled:opacity-60"
                        >
                            {converting === 'translate' ? 'Translating…' : 'Translate to English'}
                        </button>
                        <button
                            type="button"
                            disabled={!!converting}
                            onClick={() => void convertDraft('transliterate')}
                            className="rounded-md border border-border bg-background px-2 py-1 hover:bg-accent disabled:opacity-60"
                        >
                            {converting === 'transliterate' ? 'Converting…' : 'Roman script'}
                        </button>
                    </div>
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
                            typeSigil('@');
                        }}
                    >
                        <AtSign className="h-4 w-4" />
                    </Button>
                    )}
                    {/* The other half: # opens the channels, and the message
                        goes to the one chosen. */}
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label="Send to a channel"
                        title="Send to a channel"
                        className="shrink-0 text-muted-foreground"
                        onMouseDown={(event) => {
                            event.preventDefault();
                            typeSigil('#');
                        }}
                    >
                        <Hash className="h-4 w-4" />
                    </Button>
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label={dictation.listening ? 'Stop listening' : 'Speak'}
                        title={dictation.listening ? 'Stop listening' : 'Speak instead of typing'}
                        disabled={dictation.transcribing}
                        className={cn(
                            'shrink-0',
                            dictation.listening ? 'text-[var(--accent-brand)]' : 'text-muted-foreground',
                        )}
                        onClick={() => (dictation.listening ? dictation.stop() : void dictation.start())}
                    >
                        {dictation.transcribing ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : dictation.listening ? (
                            <Square className="h-4 w-4" />
                        ) : (
                            <Mic className="h-4 w-4" />
                        )}
                    </Button>
                    {dictation.listening && (
                        <div className="flex min-h-[38px] flex-1 items-center gap-3 rounded-md border border-[var(--accent-brand)]/50 bg-background px-3 text-sm text-muted-foreground">
                            <Waveform levels={dictation.levels} />
                            <span className="hidden sm:inline">Listening… press stop when done</span>
                        </div>
                    )}
                    <div className={cn('relative min-w-0 flex-1', dictation.listening && 'hidden')}>
                    {routeTo && (
                        <span
                            className="absolute -top-7 left-0 inline-flex items-center gap-1 rounded-full bg-[var(--brand-blue-soft)] px-2 py-0.5 text-xs font-medium text-[var(--brand-blue)]"
                            data-testid="route-chip"
                        >
                            to #{channelSlug(routeTo)}
                            <button
                                type="button"
                                aria-label="Send here instead"
                                className="ml-0.5 rounded-full px-1 hover:bg-[var(--brand-blue-glow)]"
                                onClick={() => setRouteTo(null)}
                            >
                                ×
                            </button>
                        </span>
                    )}
                    {/* The same words as the box, painted: a tag in blue, the
                        rest in ink. The textarea over it writes in transparent
                        so this shows through, and scrolls with it. */}
                    <div
                        ref={mirror}
                        aria-hidden="true"
                        className="pointer-events-none absolute inset-0 overflow-hidden whitespace-pre-wrap break-words rounded-md px-3 py-2 text-sm"
                    >
                        {tagTokens(text).map((token, index) =>
                            token.tag ? (
                                <span key={index} className="font-medium text-[var(--brand-blue)]">
                                    {token.text}
                                </span>
                            ) : (
                                <span key={index} className="text-foreground">{token.text}</span>
                            ),
                        )}
                        {'\u200b'}
                    </div>
                    <textarea
                        ref={input}
                        rows={1}
                        value={text}
                        onScroll={(event) => {
                            if (mirror.current) mirror.current.scrollTop = event.currentTarget.scrollTop;
                        }}
                        aria-label={`Message ${channelName}`}
                        placeholder={
                            workflowId != null
                                ? `Message ${channelName}`
                                : `Message #${channelName} — @ a bot to ask it for something`
                        }
                        className="relative max-h-40 min-h-[38px] w-full resize-y rounded-md border border-input bg-transparent px-3 py-2 text-sm text-transparent caret-foreground outline-none focus-visible:border-ring"
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
                                    complete(suggestions[highlighted]);
                                    return;
                                }
                                if (event.key === 'Escape') {
                                    setTag(null);
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
                    </div>
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
