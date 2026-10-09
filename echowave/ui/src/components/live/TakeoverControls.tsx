'use client';

/**
 * Joining a live call, under its transcript in the listen panel.
 *
 * Two ways in, each behind a confirm step that says what happens before the
 * microphone is asked for: **Barge in** (the agent is paused and stays on the
 * line; let it answer, or hand back) and **Take over** (the agent is silent
 * until the call is handed back). Once on, a banner that cannot be missed
 * says the caller can hear you, with a level meter, mute, and the way back.
 *
 * Every wall is named where it stands: joining off for the workspace (an
 * admin turns it on here), somebody else already on the call (an admin can
 * still hand it back), the microphone refused. Nothing shows while
 * `live_takeover` is off.
 *
 * On a phone call, speaking from the browser is held back until telecom
 * counsel clears mixing it into the call (`voice` false, with the reason in
 * `notice`): Barge in is not offered, and Take over is silent -- the agent
 * goes quiet, the supervisor guides it with whispers and hands the call
 * back. No microphone is asked for.
 */

import { Hand, Mic, MicOff, PhoneCall, Radio, UserRoundCheck } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
    handBackApiV1LiveCallsRunIdHandBackPost,
    joinCallApiV1LiveCallsRunIdTakeoverPost,
    letAgentAnswerApiV1LiveCallsRunIdLetAgentAnswerPost,
    setJoinSettingsApiV1LiveCallsJoinSettingsPut,
    takeoverStateApiV1LiveCallsRunIdTakeoverGet,
} from '@/client/sdk.gen';
import type { TakeoverState } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { useFeature } from '@/lib/features';
import { cn } from '@/lib/utils';

import type { TakeoverLive } from './transcript';
import { useTalk } from './useTalk';

type JoinMode = 'barge' | 'takeover';

export const JOIN_COPY: Record<JoinMode, { title: string; body: string }> = {
    barge: {
        title: 'Barge in',
        body: 'You and the agent are both on the call. The agent is paused while you have the call, and you can let it answer or hand the call back.',
    },
    takeover: {
        title: 'Take over',
        body: 'You replace the agent. It says nothing until you hand the call back, and keeps following the conversation.',
    },
};

export const JOIN_WARNING =
    'The caller will hear you. Your voice is recorded with the call, and joining is recorded in the audit log. Use headphones so the caller does not hear themselves.';

/** Taking over a call the browser cannot speak into (a phone call, for now). */
export const SILENT_TAKEOVER_COPY = {
    title: 'Take over',
    body: 'The agent goes quiet and keeps following the conversation. The caller can’t hear you: guide the agent with a whisper below, then hand the call back.',
    warning: 'Taking over is recorded in the audit log.',
};

export function TakeoverControls({
    runId,
    live,
    ended,
    onSpeakingChange,
}: {
    runId: number;
    /** Who has the call, from the listen socket's events. */
    live: TakeoverLive;
    ended: boolean;
    /** True while this person's microphone is on the call. */
    onSpeakingChange?: (speaking: boolean) => void;
}) {
    const on = useFeature('live_takeover');
    const { user, loading: authLoading } = useAuth();
    const [state, setState] = useState<TakeoverState | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [choosing, setChoosing] = useState<JoinMode | null>(null);
    const [phone, setPhone] = useState('');
    const [busy, setBusy] = useState(false);
    const talk = useTalk(runId);
    const { status: talkStatus, reset: resetTalk, stop: stopTalk } = talk;

    const load = useCallback(async () => {
        const result = await takeoverStateApiV1LiveCallsRunIdTakeoverGet({ path: { run_id: runId } });
        if (result.error) {
            setError(detailFromError(result.error, 'Could not read who has this call.'));
            return;
        }
        setError(null);
        setState(result.data ?? null);
    }, [runId]);

    // Read again whenever the call says who has it changed.
    useEffect(() => {
        if (!on || authLoading || !user || ended) return;
        void load();
    }, [on, authLoading, user, ended, load, live.mode, live.byUserId, live.agentAnswering]);

    // The listen socket's events are the freshest word on who has the call;
    // the state read from the API fills in until the first one arrives.
    const mode = (live.mode !== 'ai' ? live.mode : (state?.mode ?? 'ai')) as 'ai' | JoinMode;
    const mine = Boolean(state?.mine && mode !== 'ai');
    // Whether the caller can hear a supervisor on this call, and whether a
    // barge is possible here (not on a phone call while mixing is held back).
    const voice = state?.voice ?? true;
    const canBarge = state?.can_barge ?? true;
    // Joined without a microphone: by phone, or silently.
    const presenceOnly = Boolean(state?.needs_phone) || !voice;
    const speaking = mine && talkStatus === 'live';
    useEffect(() => {
        onSpeakingChange?.(speaking);
    }, [speaking, onSpeakingChange]);

    // The call went back to the agent (handed back, or recovered after a
    // drop): the microphone closes with it.
    const previous = useRef(live.mode);
    useEffect(() => {
        if (previous.current !== 'ai' && live.mode === 'ai') stopTalk();
        previous.current = live.mode;
    }, [live.mode, stopTalk]);
    useEffect(() => {
        if (ended) stopTalk();
    }, [ended, stopTalk]);

    if (!on || ended) return null;
    if (!state) {
        return error ? (
            <p role="alert" className="text-xs text-destructive">
                {error}
            </p>
        ) : null;
    }

    const join = async (mode: JoinMode) => {
        setBusy(true);
        setError(null);
        const byPhone = state.needs_phone;
        if (!presenceOnly && !(await talk.openMic())) {
            setBusy(false);
            return;
        }
        const result = await joinCallApiV1LiveCallsRunIdTakeoverPost({
            path: { run_id: runId },
            body: byPhone ? { mode, phone: phone.trim() } : { mode },
        });
        if (result.error) {
            setBusy(false);
            resetTalk();
            setError(detailFromError(result.error, 'Could not join the call.'));
            return;
        }
        await talk.connect({ presenceOnly });
        setChoosing(null);
        setBusy(false);
        await load();
    };

    const switchTo = async (mode: JoinMode) => {
        setBusy(true);
        const result = await joinCallApiV1LiveCallsRunIdTakeoverPost({ path: { run_id: runId }, body: { mode } });
        setBusy(false);
        if (result.error) setError(detailFromError(result.error, 'Could not switch.'));
    };

    const letAnswer = async () => {
        setBusy(true);
        const result = await letAgentAnswerApiV1LiveCallsRunIdLetAgentAnswerPost({ path: { run_id: runId } });
        setBusy(false);
        if (result.error) setError(detailFromError(result.error, 'Could not let the agent answer.'));
    };

    const handBack = async () => {
        setBusy(true);
        const result = await handBackApiV1LiveCallsRunIdHandBackPost({ path: { run_id: runId } });
        setBusy(false);
        if (result.error) {
            setError(detailFromError(result.error, 'Could not hand the call back.'));
            return;
        }
        resetTalk();
        await load();
    };

    const turnOn = async () => {
        setBusy(true);
        const result = await setJoinSettingsApiV1LiveCallsJoinSettingsPut({ body: { allow_joining: true } });
        setBusy(false);
        if (result.error) {
            setError(detailFromError(result.error, 'Could not turn it on.'));
            return;
        }
        await load();
    };

    const errorLine = (error || talk.error) && (
        <p role="alert" className="text-xs text-destructive">
            {error || talk.error}
        </p>
    );

    // On the call: the banner and the controls.
    if (mine && !voice) {
        return (
            <section aria-label="You have taken over the call" className="flex flex-col gap-2 border-t border-border pt-3">
                <div
                    role="status"
                    aria-live="assertive"
                    className="flex items-center gap-2 rounded-lg bg-muted px-3 py-2 text-sm font-medium text-foreground"
                >
                    <Radio className="h-4 w-4 shrink-0" aria-hidden />
                    <span className="min-w-0 flex-1">
                        You’ve taken over. The caller can’t hear you.
                        <span className="block text-xs font-normal opacity-90">
                            The agent is silent. Guide it with a whisper below, then hand the call back.
                        </span>
                    </span>
                </div>
                {state.notice && <p className="text-xs text-muted-foreground">{state.notice}</p>}
                <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" className="ml-auto" onClick={() => void handBack()} disabled={busy}>
                        <UserRoundCheck className="mr-1.5 h-3.5 w-3.5" />
                        Hand back to the agent
                    </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                    If you close this page, the agent picks the call back up after {Math.round(state.recovery_seconds)}{' '}
                    seconds.
                </p>
                {errorLine}
            </section>
        );
    }

    if (mine) {
        const onAir = state.needs_phone || talkStatus === 'live';
        return (
            <section aria-label="You are on the call" className="flex flex-col gap-2 border-t border-border pt-3">
                <div
                    role="status"
                    aria-live="assertive"
                    className={cn(
                        'flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium',
                        talk.muted
                            ? 'bg-muted text-foreground'
                            : 'bg-red-600 text-white dark:bg-red-500',
                    )}
                >
                    <Radio className={cn('h-4 w-4 shrink-0', !talk.muted && 'animate-pulse')} aria-hidden />
                    <span className="min-w-0 flex-1">
                        {state.needs_phone
                            ? 'You’re on the call by phone. The caller can hear you.'
                            : talk.muted
                              ? 'Muted. The caller can’t hear you.'
                              : onAir
                                ? 'You’re live. The caller can hear you.'
                                : 'Connecting your microphone…'}
                        <span className="block text-xs font-normal opacity-90">
                            {mode === 'takeover'
                                ? 'You have taken over. The agent is silent.'
                                : live.agentAnswering
                                  ? 'The agent is answering. Speak to cut in.'
                                  : 'Barge: the agent is paused.'}
                        </span>
                    </span>
                    {!state.needs_phone && (
                        <span aria-hidden className="h-2 w-12 overflow-hidden rounded-full bg-white/30">
                            <span
                                className="block h-full bg-white"
                                style={{ width: `${Math.round(talk.level * 100)}%` }}
                            />
                        </span>
                    )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    {!state.needs_phone && (
                        <Button
                            size="sm"
                            variant="outline"
                            aria-pressed={talk.muted}
                            onClick={() => talk.setMuted(!talk.muted)}
                            disabled={talkStatus !== 'live'}
                        >
                            {talk.muted ? <Mic className="mr-1.5 h-3.5 w-3.5" /> : <MicOff className="mr-1.5 h-3.5 w-3.5" />}
                            {talk.muted ? 'Unmute' : 'Mute'}
                        </Button>
                    )}
                    {mode === 'barge' && (
                        <Button size="sm" variant="outline" onClick={() => void letAnswer()} disabled={busy || live.agentAnswering}>
                            Let the agent answer
                        </Button>
                    )}
                    {(mode === 'barge' || canBarge) && (
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={() => void switchTo(mode === 'barge' ? 'takeover' : 'barge')}
                            disabled={busy}
                        >
                            {mode === 'barge' ? 'Take over fully' : 'Switch to barge'}
                        </Button>
                    )}
                    {!state.needs_phone && talkStatus !== 'live' && talkStatus !== 'connecting' && (
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={() => void (async () => (await talk.openMic()) && (await talk.connect()))()}
                        >
                            Reconnect microphone
                        </Button>
                    )}
                    <Button size="sm" className="ml-auto" onClick={() => void handBack()} disabled={busy}>
                        <UserRoundCheck className="mr-1.5 h-3.5 w-3.5" />
                        Hand back to the agent
                    </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                    If you lose the connection, the agent picks the call back up after{' '}
                    {Math.round(state.recovery_seconds)} seconds.
                </p>
                {errorLine}
            </section>
        );
    }

    // Somebody else has the call.
    if (mode !== 'ai' || state.blocked === 'taken') {
        return (
            <section aria-label="Joined by someone else" className="flex flex-col gap-2 border-t border-border pt-3 text-sm">
                <p>
                    {state.by || live.by || 'Someone'}{' '}
                    {mode === 'takeover' ? 'has taken over this call.' : 'has joined this call.'}
                </p>
                {state.can_change_setting && (
                    <div>
                        <Button size="sm" variant="outline" onClick={() => void handBack()} disabled={busy}>
                            Hand back to the agent
                        </Button>
                    </div>
                )}
                {errorLine}
            </section>
        );
    }

    if (state.blocked === 'joining_off') {
        return (
            <section aria-label="Join the call" className="flex flex-col gap-2 border-t border-border pt-3 text-sm">
                <p>Joining calls is off for this workspace.</p>
                {state.can_change_setting ? (
                    <div className="flex flex-wrap items-center gap-2">
                        <Button size="sm" variant="outline" onClick={() => void turnOn()} disabled={busy}>
                            Turn on joining calls
                        </Button>
                        <span className="text-xs text-muted-foreground">
                            Admins and each agent’s owner can then speak to callers on live calls. Every join is recorded in
                            the audit log.
                        </span>
                    </div>
                ) : (
                    <p className="text-xs text-muted-foreground">A workspace admin or owner can turn it on.</p>
                )}
                {errorLine}
            </section>
        );
    }

    if (!state.can_join) return errorLine || null;

    if (choosing) {
        const silent = choosing === 'takeover' && !voice;
        const copy = silent ? SILENT_TAKEOVER_COPY : JOIN_COPY[choosing];
        return (
            <section aria-label={copy.title} className="flex flex-col gap-2 border-t border-border pt-3">
                <p className="text-sm font-medium">{copy.title}</p>
                <p className="text-sm">{copy.body}</p>
                <p className="text-xs text-muted-foreground">{silent ? SILENT_TAKEOVER_COPY.warning : JOIN_WARNING}</p>
                {state.needs_phone && (
                    <label className="flex flex-col gap-1 text-xs">
                        Your phone number — we’ll call you on it
                        <input
                            type="tel"
                            value={phone}
                            onChange={(event) => setPhone(event.target.value)}
                            placeholder="+91…"
                            className="rounded-md border border-input bg-background px-3 py-2 text-sm"
                        />
                    </label>
                )}
                <div className="flex flex-wrap items-center gap-2">
                    <Button
                        size="sm"
                        onClick={() => void join(choosing)}
                        disabled={busy || (state.needs_phone && !phone.trim())}
                    >
                        {state.needs_phone ? (
                            <PhoneCall className="mr-1.5 h-3.5 w-3.5" />
                        ) : silent ? (
                            <UserRoundCheck className="mr-1.5 h-3.5 w-3.5" />
                        ) : (
                            <Mic className="mr-1.5 h-3.5 w-3.5" />
                        )}
                        {state.needs_phone
                            ? 'Call me and join'
                            : silent
                              ? 'Take over the call'
                              : 'Turn on my microphone and join'}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setChoosing(null)} disabled={busy}>
                        Cancel
                    </Button>
                </div>
                {errorLine}
            </section>
        );
    }

    return (
        <section aria-label="Join the call" className="flex flex-col gap-2 border-t border-border pt-3">
            <div className="flex flex-wrap items-center gap-2">
                {canBarge && (
                    <Button size="sm" variant="outline" onClick={() => setChoosing('barge')}>
                        <Hand className="mr-1.5 h-3.5 w-3.5" />
                        Barge in
                    </Button>
                )}
                <Button size="sm" variant="outline" onClick={() => setChoosing('takeover')}>
                    <UserRoundCheck className="mr-1.5 h-3.5 w-3.5" />
                    Take over
                </Button>
                <span className="text-xs text-muted-foreground">
                    {voice ? 'Speak to the caller yourself.' : 'Silence the agent and guide it by typing.'}
                </span>
            </div>
            {state.notice && (
                <p className="text-xs text-muted-foreground" data-testid="takeover-notice">
                    {state.notice}
                </p>
            )}
            {errorLine}
        </section>
    );
}
