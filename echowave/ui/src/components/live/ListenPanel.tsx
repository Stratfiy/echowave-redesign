'use client';

/**
 * Listening to one live call, in place: under the strip it was opened from.
 *
 * The transcript streams as it is said, both sides labelled, the words still
 * being heard shown lighter until they settle. Sound is off until asked for,
 * and is receive-only -- the browser is never asked for its microphone to
 * listen. Under it, the whisper box: an instruction the agent follows from its
 * next reply (or at once, marked urgent) and the caller never hears. Typed, or
 * dictated into the box and read before it is sent.
 *
 * Every wall is named where it stands, with the way past it: listening off for
 * the workspace (an admin turns it on here), not permitted (who can), a
 * greeting that never tells callers they may be monitored (a proposed change,
 * as the same publish-or-discard card the agent's own edits use, right here).
 *
 * With live_takeover, the panel is also where a supervisor joins the call
 * (`TakeoverControls`): barge in or take over, speaking from this browser.
 * Who has the call, and each stretch of a supervisor speaking, appear in the
 * transcript in order with the words.
 */

import { Headphones, Lock, Mic, Send, Square, UserRound, VolumeX, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import {
    liveCallDetailApiV1LiveCallsRunIdGet,
    proposeConsentFixApiV1LiveCallsRunIdConsentFixPost,
    setLiveSettingsApiV1LiveCallsSettingsPut,
    whisperToCallApiV1LiveCallsRunIdWhisperPost,
} from '@/client/sdk.gen';
import type { LiveCallDetail, LiveCallItem, TimelineEvent } from '@/client/types.gen';
import { Waveform } from '@/components/channel/Waveform';
import { Button } from '@/components/ui/button';
import { EditCard } from '@/components/workflow/EditCard';
import { detailFromError } from '@/lib/apiError';
import { appendDictation, useDictation } from '@/lib/useDictation';
import { cn } from '@/lib/utils';

import { TakeoverControls } from './TakeoverControls';
import { duration, type LiveEvent, type TranscriptEntry } from './transcript';
import { useLiveListen } from './useLiveListen';

export const BLOCKED_COPY: Record<string, string> = {
    setting_off: 'Live listening is off for this workspace.',
    not_permitted: 'Only the agent’s owner or a workspace admin can listen to its calls.',
};

export function callerLabel(call: Pick<LiveCallItem, 'caller' | 'direction'>): string {
    if (call.direction === 'web') return 'Web call';
    return call.caller || 'Unknown number';
}

export function ListenPanel({
    call,
    canChangeSetting,
    onClose,
    onSettingChanged,
}: {
    call: LiveCallItem;
    canChangeSetting: boolean;
    onClose: () => void;
    onSettingChanged?: (allowed: boolean) => void;
}) {
    return (
        <section
            aria-label={`Listening to ${call.agent_name}`}
            className="flex flex-col gap-3 rounded-xl border border-border bg-card p-3 sm:p-4"
        >
            <header className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                        {call.agent_name} · {callerLabel(call)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                        {call.direction === 'inbound' ? 'Incoming' : call.direction === 'outbound' ? 'Outgoing' : 'Web'} call
                        {call.step ? ` · ${call.step}` : ''}
                    </p>
                </div>
                <Button size="icon" variant="ghost" aria-label="Close" onClick={onClose} className="shrink-0 rounded-full">
                    <X className="h-4 w-4" />
                </Button>
            </header>
            {call.blocked ? (
                <Blocked call={call} canChangeSetting={canChangeSetting} onSettingChanged={onSettingChanged} />
            ) : (
                <Listening call={call} />
            )}
        </section>
    );
}

function Blocked({
    call,
    canChangeSetting,
    onSettingChanged,
}: {
    call: LiveCallItem;
    canChangeSetting: boolean;
    onSettingChanged?: (allowed: boolean) => void;
}) {
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const turnOn = async () => {
        setSaving(true);
        setError(null);
        const result = await setLiveSettingsApiV1LiveCallsSettingsPut({ body: { allow_listening: true } });
        setSaving(false);
        if (result.error) {
            setError(detailFromError(result.error, 'Could not turn it on.'));
            return;
        }
        onSettingChanged?.(true);
    };
    const reason = call.blocked ?? '';
    return (
        <div role="status" className="flex flex-col gap-2 text-sm">
            <p>{BLOCKED_COPY[reason] ?? 'You cannot listen to this call.'}</p>
            {reason === 'setting_off' &&
                (canChangeSetting ? (
                    <div className="flex flex-wrap items-center gap-2">
                        <Button size="sm" onClick={() => void turnOn()} disabled={saving}>
                            Turn on live listening
                        </Button>
                        <span className="text-xs text-muted-foreground">
                            Admins and each agent’s owner can then listen and whisper. It is recorded in the audit log.
                        </span>
                    </div>
                ) : (
                    <p className="text-xs text-muted-foreground">A workspace admin or owner can turn it on.</p>
                ))}
            {error && (
                <p role="alert" className="text-xs text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}

function Listening({ call }: { call: LiveCallItem }) {
    const [detail, setDetail] = useState<LiveCallDetail | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let gone = false;
        void (async () => {
            const result = await liveCallDetailApiV1LiveCallsRunIdGet({ path: { run_id: call.run_id } });
            if (gone) return;
            if (result.error) {
                setError(detailFromError(result.error, 'This call could not be opened.'));
                return;
            }
            setDetail(result.data ?? null);
        })();
        return () => {
            gone = true;
        };
    }, [call.run_id]);

    if (error) {
        return (
            <p role="alert" className="text-sm text-destructive">
                {error}
            </p>
        );
    }
    if (!detail) return <p className="text-sm text-muted-foreground">Opening the call…</p>;
    return <LiveCall call={call} detail={detail} />;
}

function LiveCall({ call, detail }: { call: LiveCallItem; detail: LiveCallDetail }) {
    const [audio, setAudio] = useState(false);
    const [speaking, setSpeaking] = useState(false);
    const { state, status, error } = useLiveListen(call.run_id, {
        audio,
        initial: detail.transcript as LiveEvent[],
        speaking,
    });
    const ended = status === 'ended' || state.ended;
    const bottom = useRef<HTMLLIElement | null>(null);
    useEffect(() => {
        bottom.current?.scrollIntoView?.({ block: 'nearest' });
    }, [state.entries.length]);

    return (
        <div className="flex flex-col gap-3">
            {detail.consent.warning && <ConsentWarning runId={call.run_id} warning={detail.consent.warning} />}
            <div className="flex flex-wrap items-center gap-2">
                <Button
                    size="sm"
                    variant={audio ? 'secondary' : 'outline'}
                    aria-pressed={audio}
                    onClick={() => setAudio((on) => !on)}
                    disabled={ended}
                >
                    {audio ? <VolumeX className="mr-1.5 h-3.5 w-3.5" /> : <Headphones className="mr-1.5 h-3.5 w-3.5" />}
                    {audio ? 'Stop audio' : 'Hear the call'}
                </Button>
                <span className="text-xs text-muted-foreground" aria-live="polite">
                    {ended
                        ? 'The call has ended.'
                        : status === 'connecting'
                          ? 'Connecting…'
                          : status === 'error'
                            ? 'Lost the connection to this call.'
                            : `Live · ${state.step ?? call.step ?? 'on the call'}`}
                </span>
            </div>
            {error && <p className="text-xs text-destructive">{error}</p>}
            <ol aria-label="Live transcript" className="flex max-h-80 flex-col gap-2 overflow-y-auto pr-1">
                {state.entries.length === 0 && (
                    <li className="text-sm text-muted-foreground">Nothing has been said yet.</li>
                )}
                {state.entries.map((entry) => (
                    <Entry key={entry.key} entry={entry} />
                ))}
                <li ref={bottom} aria-hidden className="h-0" />
            </ol>
            <TakeoverControls runId={call.run_id} live={state.takeover} ended={ended} onSpeakingChange={setSpeaking} />
            <WhisperBox runId={call.run_id} disabled={ended} />
        </div>
    );
}

function Entry({ entry }: { entry: TranscriptEntry }) {
    if (entry.kind === 'takeover') {
        return (
            <li className="flex items-start gap-2 rounded-lg border border-dashed border-border px-3 py-2">
                <UserRound className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                <p className="min-w-0 break-words text-xs text-muted-foreground">{entry.text}</p>
            </li>
        );
    }
    if (entry.kind === 'supervisor') {
        return (
            <li className="flex flex-col">
                <span className="text-xs font-medium text-muted-foreground">{entry.by} (supervisor)</span>
                <span className={cn('text-sm', !entry.final && 'text-muted-foreground')}>
                    {entry.final
                        ? `Spoke to the caller · ${duration(entry.seconds)} · not transcribed`
                        : 'Speaking to the caller…'}
                </span>
            </li>
        );
    }
    if (entry.kind === 'whisper') {
        return (
            <li className="flex items-start gap-2 rounded-lg border border-dashed border-border bg-muted/40 px-3 py-2">
                <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0">
                    <p className="text-xs font-medium text-muted-foreground">
                        Whisper from {entry.by}
                        {entry.urgent ? ' (urgent)' : ''} · not spoken to the caller
                    </p>
                    <p className="break-words text-sm">{entry.text}</p>
                </div>
            </li>
        );
    }
    return (
        <li className="flex flex-col">
            <span className="text-xs font-medium text-muted-foreground">{entry.speaker === 'caller' ? 'Caller' : 'Agent'}</span>
            <span className={cn('break-words text-sm', !entry.final && 'text-muted-foreground')}>
                {entry.text}
                {entry.cutOff ? ' —' : ''}
            </span>
        </li>
    );
}

function ConsentWarning({ runId, warning }: { runId: number; warning: string }) {
    const [card, setCard] = useState<TimelineEvent | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const propose = async () => {
        setBusy(true);
        setError(null);
        const result = await proposeConsentFixApiV1LiveCallsRunIdConsentFixPost({ path: { run_id: runId } });
        setBusy(false);
        if (result.error) {
            setError(detailFromError(result.error, 'Could not propose that.'));
            return;
        }
        setCard(result.data ?? null);
    };
    return (
        <div className="flex flex-col gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2">
            <p className="text-sm text-amber-800 dark:text-amber-200">{warning}</p>
            {!card && (
                <div>
                    <Button size="sm" variant="outline" onClick={() => void propose()} disabled={busy}>
                        Propose a greeting change
                    </Button>
                </div>
            )}
            {error && (
                <p role="alert" className="text-xs text-destructive">
                    {error}
                </p>
            )}
            {card && <EditCard event={card} onSettled={setCard} />}
        </div>
    );
}

function WhisperBox({ runId, disabled }: { runId: number; disabled: boolean }) {
    const [text, setText] = useState('');
    const [urgent, setUrgent] = useState(false);
    const [sending, setSending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [sent, setSent] = useState<string | null>(null);
    const dictation = useDictation((transcript) => setText((was) => appendDictation(was, transcript)));

    const send = async () => {
        const words = text.trim();
        if (!words || sending) return;
        setSending(true);
        setError(null);
        const result = await whisperToCallApiV1LiveCallsRunIdWhisperPost({
            path: { run_id: runId },
            body: { text: words, urgent },
        });
        setSending(false);
        if (result.error) {
            setError(detailFromError(result.error, 'The whisper did not reach the call.'));
            return;
        }
        setText('');
        setUrgent(false);
        setSent(urgent ? 'Sent. The agent is answering with it now.' : 'Sent. The agent uses it from its next reply.');
    };

    return (
        <form
            className="flex flex-col gap-2 border-t border-border pt-3"
            onSubmit={(event) => {
                event.preventDefault();
                void send();
            }}
        >
            <label htmlFor={`whisper-${runId}`} className="text-xs font-medium">
                Whisper to the agent <span className="font-normal text-muted-foreground">— the caller won’t hear it</span>
            </label>
            <textarea
                id={`whisper-${runId}`}
                value={text}
                onChange={(event) => {
                    setText(event.target.value);
                    setSent(null);
                }}
                maxLength={500}
                rows={2}
                disabled={disabled}
                placeholder={disabled ? 'The call has ended.' : 'e.g. Offer the 5 pm slot before the morning ones.'}
                className="w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
            />
            <div className="flex flex-wrap items-center gap-2">
                <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={disabled || dictation.transcribing}
                    onClick={() => (dictation.listening ? dictation.stop() : void dictation.start())}
                    aria-label={dictation.listening ? 'Stop dictating' : 'Dictate the whisper'}
                >
                    {dictation.listening ? <Square className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />}
                </Button>
                {dictation.listening && <Waveform levels={dictation.levels} />}
                <label className="flex items-center gap-1.5 text-xs">
                    <input
                        type="checkbox"
                        checked={urgent}
                        onChange={(event) => setUrgent(event.target.checked)}
                        disabled={disabled}
                    />
                    Urgent — cut in now
                </label>
                <Button type="submit" size="sm" className="ml-auto" disabled={disabled || sending || !text.trim()}>
                    <Send className="mr-1.5 h-3.5 w-3.5" />
                    Send whisper
                </Button>
            </div>
            {(error || dictation.error) && (
                <p role="alert" className="text-xs text-destructive">
                    {error || dictation.error}
                </p>
            )}
            {sent && !error && (
                <p role="status" className="text-xs text-muted-foreground">
                    {sent}
                </p>
            )}
        </form>
    );
}
