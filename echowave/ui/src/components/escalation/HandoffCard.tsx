'use client';

/**
 * A caller is being handed to a person; this is what that person reads.
 *
 * The card is the handoff card from services/escalation/card.py: who is on
 * the line and whether they are verified, what they want, what was captured,
 * what the agent already did and how it went, why it came to a person, two
 * sentences of summary, the language and what the caller agreed to. The
 * person who picks up should never have to ask the caller to repeat it.
 *
 * Three buttons, each answered here, in the thread or on Today -- never on
 * another screen: Accept (it is mine), Decline (not mine: the next person is
 * rung, or the caller goes back to the agent) and, once the caller is with a
 * person, Hand back to AI with a note of what was decided. Where the carrier
 * cannot move a live caller, the card says so instead of offering a button
 * that cannot work.
 */

import { ArrowLeftRight, Check, Loader2, PhoneForwarded, X } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';

import {
    acceptEscalationApiV1EscalationsEscalationUuidAcceptPost,
    declineEscalationApiV1EscalationsEscalationUuidDeclinePost,
    getEscalationApiV1EscalationsEscalationUuidGet,
    handBackEscalationApiV1EscalationsEscalationUuidHandBackPost,
} from '@/client/sdk.gen';
import type { EscalationResponse } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';

export type HandoffCardData = {
    caller?: { name?: string | null; number_masked?: string | null; verified?: boolean; verification?: string };
    intent?: string | null;
    /** The team the policy routes this topic to; absent means the general numbers. */
    team?: string | null;
    fields?: Record<string, string>;
    actions?: { tool: string; ok: boolean | null; result?: string }[];
    reason?: string;
    reason_code?: string;
    summary?: string;
    language?: string | null;
    consent?: Record<string, boolean>;
    transcript_url?: string | null;
};

const OPEN_STATES = new Set(['requested', 'dialling', 'briefing', 'bridged']);

/** One line on where the handover stands, in words. */
export function stateLine(state: string, failure?: string | null, fallback?: string | null): string {
    switch (state) {
        case 'requested':
            return 'Getting ready to ring the team';
        case 'dialling':
            return 'Ringing the team';
        case 'briefing':
            return 'Someone picked up and is hearing the details';
        case 'bridged':
            return 'The caller is with a person';
        case 'completed':
            return 'Finished';
        case 'failed': {
            const why: Record<string, string> = {
                no_answer: 'nobody answered',
                busy: 'the line was busy',
                machine: 'it reached voicemail',
                declined: 'it was declined',
                timeout: 'nobody answered in time',
                hold_cap: 'the caller had waited long enough',
                no_human_available: 'nobody was available',
                no_one_to_ring: 'no number is set to ring',
                caller_hung_up: 'the caller hung up while waiting',
                provider_cannot_transfer: 'this phone line cannot transfer calls',
            };
            const then: Record<string, string> = {
                callback: ' · a callback was booked',
                ticket: ' · a reference was sent',
                voicemail: ' · they left a message',
            };
            return `Not connected: ${why[failure ?? ''] ?? failure ?? 'it did not go through'}${then[fallback ?? ''] ?? ''}`;
        }
        default:
            return state;
    }
}

export function HandoffCard({
    escalationUuid,
    initialCard,
    initialState,
}: {
    escalationUuid: string;
    /** What the thread row already carries, so the card draws before the fetch. */
    initialCard?: HandoffCardData;
    initialState?: string;
}) {
    const { user, loading: authLoading } = useAuth();
    const fetched = useRef(false);
    const [data, setData] = useState<EscalationResponse | null>(null);
    const [busy, setBusy] = useState<'accept' | 'decline' | 'hand_back' | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [handingBack, setHandingBack] = useState(false);
    const [note, setNote] = useState('');

    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void (async () => {
            const res = await getEscalationApiV1EscalationsEscalationUuidGet({ path: { escalation_uuid: escalationUuid } });
            if (res.error) {
                setError(detailFromError(res.error, 'Could not load this handover.'));
                return;
            }
            if (res.data) setData(res.data);
        })();
    }, [authLoading, user, escalationUuid]);

    const card = ((data?.handoff_card as HandoffCardData | undefined) ?? initialCard ?? {}) as HandoffCardData;
    const state = data?.state ?? initialState ?? 'requested';
    const open = OPEN_STATES.has(state);
    const caller = card.caller ?? {};
    const fields = Object.entries(card.fields ?? {});
    const actions = card.actions ?? [];

    const act = async (kind: 'accept' | 'decline' | 'hand_back') => {
        setBusy(kind);
        setError(null);
        const path = { escalation_uuid: escalationUuid };
        const res =
            kind === 'accept'
                ? await acceptEscalationApiV1EscalationsEscalationUuidAcceptPost({ path })
                : kind === 'decline'
                  ? await declineEscalationApiV1EscalationsEscalationUuidDeclinePost({ path })
                  : await handBackEscalationApiV1EscalationsEscalationUuidHandBackPost({ path, body: { note } });
        setBusy(null);
        if (res.error) {
            setError(detailFromError(res.error, 'That did not go through.'));
            return;
        }
        if (res.data) setData(res.data);
        if (kind === 'hand_back') setHandingBack(false);
    };

    return (
        <div className="max-w-2xl rounded-lg border border-border bg-card p-3" data-testid="handoff-card">
            <p className="flex items-center gap-2 text-sm font-medium">
                <PhoneForwarded className="h-4 w-4 text-[var(--accent-brand)]" aria-hidden />
                {caller.name || caller.number_masked || 'A caller'} needs a person
            </p>
            <p className="mt-1 text-xs text-muted-foreground" data-testid="handoff-state">
                {card.reason ? `${card.reason} · ` : ''}
                {stateLine(state, data?.failure_reason, data?.fallback)}
            </p>

            {card.summary && <p className="mt-2 text-sm">{card.summary}</p>}

            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                <dt className="text-muted-foreground">Caller</dt>
                <dd className="min-w-0 break-words">
                    {[caller.name, caller.number_masked].filter(Boolean).join(' · ') || 'Unknown'}
                    <span
                        className={
                            caller.verified
                                ? 'ml-2 rounded bg-emerald-500/15 px-1.5 text-xs text-emerald-900 dark:text-emerald-200'
                                : 'ml-2 rounded bg-muted px-1.5 text-xs'
                        }
                    >
                        {caller.verification || (caller.verified ? 'verified' : 'not verified')}
                    </span>
                </dd>
                {card.intent && (
                    <>
                        <dt className="text-muted-foreground">Wants</dt>
                        <dd className="min-w-0 break-words">{card.intent}</dd>
                    </>
                )}
                {card.team && <Field label="Team" value={card.team} />}
                {fields.map(([key, value]) => (
                    <Field key={key} label={key.replace(/_/g, ' ')} value={value} />
                ))}
                {card.language && <Field label="Language" value={card.language} />}
                {card.consent && Object.keys(card.consent).length > 0 && (
                    <Field
                        label="Told"
                        value={[
                            card.consent.ai_disclosed ? 'speaking with an AI' : null,
                            card.consent.recording_disclosed ? 'the call is recorded' : null,
                        ]
                            .filter(Boolean)
                            .join(', ') || 'nothing at the start of the call'}
                    />
                )}
            </dl>

            {actions.length > 0 && (
                <ul className="mt-2 space-y-0.5 text-sm" aria-label="What the agent already did">
                    {actions.map((a, i) => (
                        <li key={i} className="flex items-start gap-1.5">
                            {a.ok === false ? (
                                <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600" aria-label="failed" />
                            ) : (
                                <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" aria-label="done" />
                            )}
                            <span className="min-w-0 break-words">
                                {a.tool.replace(/_/g, ' ')}
                                {a.result ? <span className="text-muted-foreground"> · {a.result}</span> : null}
                            </span>
                        </li>
                    ))}
                </ul>
            )}

            {card.transcript_url && (
                <Link href={card.transcript_url} className="mt-2 inline-block text-xs underline underline-offset-2">
                    Live transcript
                </Link>
            )}

            {data?.outcome_note && (
                <p className="mt-2 text-sm">
                    <span className="text-muted-foreground">Handed back: </span>
                    {data.outcome_note}
                </p>
            )}

            {open && (
                <div className="mt-3 flex flex-wrap gap-2">
                    {!data?.human_response && (
                        <Button size="sm" className="min-h-11 md:min-h-8" disabled={busy !== null} onClick={() => void act('accept')}>
                            {busy === 'accept' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                            Accept
                        </Button>
                    )}
                    <Button
                        size="sm"
                        variant="outline"
                        className="min-h-11 md:min-h-8"
                        disabled={busy !== null}
                        onClick={() => void act('decline')}
                    >
                        Decline
                    </Button>
                    {state === 'bridged' && data?.can_hand_back && (
                        <Button
                            size="sm"
                            variant="outline"
                            className="min-h-11 md:min-h-8"
                            disabled={busy !== null}
                            onClick={() => setHandingBack((v) => !v)}
                        >
                            <ArrowLeftRight className="h-4 w-4" />
                            Hand back to AI
                        </Button>
                    )}
                </div>
            )}
            {open && state === 'bridged' && data && !data.can_hand_back && (
                <p className="mt-2 text-xs text-muted-foreground" data-testid="handoff-no-hand-back">
                    This phone line cannot move a caller back to the agent mid-call. Finish the call yourself, or tell
                    the caller the agent will call them back.
                </p>
            )}
            {handingBack && (
                <div className="mt-2 space-y-2">
                    <label className="block text-xs text-muted-foreground" htmlFor={`note-${escalationUuid}`}>
                        What was decided, and what is left for the agent
                    </label>
                    <textarea
                        id={`note-${escalationUuid}`}
                        className="min-h-20 w-full rounded-md border border-border bg-background p-2 text-sm"
                        maxLength={1000}
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                    />
                    <Button size="sm" className="min-h-11 md:min-h-8" disabled={busy !== null} onClick={() => void act('hand_back')}>
                        {busy === 'hand_back' && <Loader2 className="h-4 w-4 animate-spin" />}
                        Hand back
                    </Button>
                </div>
            )}
            {error && (
                <p className="mt-2 text-sm text-red-700 dark:text-red-300" role="alert">
                    {error}
                </p>
            )}
        </div>
    );
}

function Field({ label, value }: { label: string; value: string }) {
    return (
        <>
            <dt className="capitalize text-muted-foreground">{label}</dt>
            <dd className="min-w-0 break-words">{value}</dd>
        </>
    );
}

export default HandoffCard;
