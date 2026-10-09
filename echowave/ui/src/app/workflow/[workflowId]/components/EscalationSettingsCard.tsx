'use client';

/**
 * When this agent hands a caller to a person, on the agent's own page.
 *
 * Who to ring (in order), when they are there, which topics always go to a
 * person, and how many tries the agent gets at one step. Saved into the
 * agent's draft like the rest of its setup (services/escalation/settings.py),
 * so it reaches live calls when the agent is published; the card says so
 * when the draft differs from what calls use now.
 *
 * Only shown while escalation_v2 is on. The same policy can be changed from
 * the agent's chat ("send fraud calls to Priya"), as a card to publish.
 */

import { PhoneForwarded, Plus, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import {
    getEscalationPolicyApiV1EscalationsPolicyWorkflowIdGet,
    saveEscalationPolicyApiV1EscalationsPolicyWorkflowIdPut,
} from '@/client/sdk.gen';
import type { EscalationPolicyResponse } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { useFeature } from '@/lib/features';

type Target = { number: string; name?: string | null };
type Slot = { day_of_week: number; start_time: string; end_time: string };
type Hours = { enabled: boolean; timezone: string; slots: Slot[] };
type Policy = {
    transfer_numbers: Target[];
    transfer_hours: Hours;
    always_transfer_topics: string[];
    custom_topics: string[];
    refund_limit: number | null;
    max_ai_attempts: number;
    [key: string]: unknown;
};

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MAX_NUMBERS = 5;
const FIELD = 'w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm';

/** The week as the form edits it: which days, one window. */
export function hoursToForm(hours: Hours | undefined): { enabled: boolean; days: number[]; start: string; end: string } {
    const slots = hours?.slots ?? [];
    return {
        enabled: Boolean(hours?.enabled),
        days: slots.length ? [...new Set(slots.map((s) => s.day_of_week))].sort() : [0, 1, 2, 3, 4],
        start: slots[0]?.start_time ?? '09:00',
        end: slots[0]?.end_time ?? '18:00',
    };
}

export function formToHours(form: ReturnType<typeof hoursToForm>, timezone = 'Asia/Kolkata'): Hours {
    return {
        enabled: form.enabled,
        timezone,
        slots: form.enabled ? form.days.map((d) => ({ day_of_week: d, start_time: form.start, end_time: form.end })) : [],
    };
}

export function EscalationSettingsCard({ workflowId }: { workflowId: number }) {
    const on = useFeature('escalation_v2');
    const { user, loading: authLoading } = useAuth();
    const fetched = useRef(false);
    const [loaded, setLoaded] = useState<EscalationPolicyResponse | null>(null);
    const [policy, setPolicy] = useState<Policy | null>(null);
    const [hours, setHours] = useState<ReturnType<typeof hoursToForm>>(hoursToForm(undefined));
    const [custom, setCustom] = useState('');
    const [open, setOpen] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [saved, setSaved] = useState(false);

    const take = (data: EscalationPolicyResponse) => {
        const p = data.policy as unknown as Policy;
        setLoaded(data);
        setPolicy(p);
        setHours(hoursToForm(p.transfer_hours));
        setCustom((p.custom_topics ?? []).join(', '));
    };

    useEffect(() => {
        if (!on || authLoading || !user || fetched.current) return;
        fetched.current = true;
        void (async () => {
            const res = await getEscalationPolicyApiV1EscalationsPolicyWorkflowIdGet({ path: { workflow_id: workflowId } });
            if (res.error) {
                setError(detailFromError(res.error, 'Could not load who this agent hands calls to.'));
                return;
            }
            if (res.data) take(res.data);
        })();
    }, [on, authLoading, user, workflowId]);

    if (!on) return null;
    if (!policy || !loaded) {
        return error ? (
            <p className="text-sm text-red-700 dark:text-red-300" role="alert">
                {error}
            </p>
        ) : null;
    }

    const change = (patch: Partial<Policy>) => {
        setSaved(false);
        setPolicy({ ...policy, ...patch });
    };
    const setTarget = (i: number, patch: Partial<Target>) =>
        change({ transfer_numbers: policy.transfer_numbers.map((t, j) => (j === i ? { ...t, ...patch } : t)) });
    const toggleTopic = (key: string) =>
        change({
            always_transfer_topics: policy.always_transfer_topics.includes(key)
                ? policy.always_transfer_topics.filter((t) => t !== key)
                : [...policy.always_transfer_topics, key],
        });

    const save = async () => {
        setSaving(true);
        setError(null);
        const body = {
            ...policy,
            transfer_numbers: policy.transfer_numbers
                .filter((t) => t.number.trim())
                .map((t) => ({ number: t.number.trim(), name: t.name?.trim() || null })),
            transfer_hours: formToHours(hours, policy.transfer_hours?.timezone),
            custom_topics: custom
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean),
        };
        const res = await saveEscalationPolicyApiV1EscalationsPolicyWorkflowIdPut({
            path: { workflow_id: workflowId },
            body: { policy: body },
        });
        setSaving(false);
        if (res.error) {
            setError(detailFromError(res.error, 'Could not save that.'));
            return;
        }
        if (res.data) take(res.data);
        setSaved(true);
    };

    const ringing = policy.transfer_numbers.length
        ? policy.transfer_numbers.map((t) => t.name || t.number).join(', ')
        : 'the transfer tool or the workspace number';

    return (
        <section className="space-y-2" aria-label="Hand callers to a person" data-testid="escalation-settings">
            <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <PhoneForwarded className="h-3.5 w-3.5" aria-hidden />
                Hand callers to a person
            </h3>
            <p className="text-sm text-muted-foreground">
                Rings {ringing}. {policy.always_transfer_topics.length} topic
                {policy.always_transfer_topics.length === 1 ? '' : 's'} always go to a person.
            </p>
            {loaded.unpublished && (
                <p className="text-xs text-[#705500] dark:text-amber-300" data-testid="escalation-unpublished">
                    Saved in the draft. Publish the agent for live calls to use it.
                </p>
            )}
            <button
                type="button"
                className="text-xs underline underline-offset-2"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
            >
                {open ? 'Hide' : 'Change'}
            </button>
            {open && (
                <div className="space-y-4 rounded-md border border-border p-3">
                    <fieldset className="space-y-2">
                        <legend className="text-sm font-medium">Ring, in order</legend>
                        {policy.transfer_numbers.map((t, i) => (
                            <div key={i} className="flex gap-2">
                                <input
                                    className={FIELD}
                                    inputMode="tel"
                                    aria-label={`Number ${i + 1}`}
                                    placeholder="+91 98765 43210"
                                    value={t.number}
                                    onChange={(e) => setTarget(i, { number: e.target.value })}
                                />
                                <input
                                    className={FIELD}
                                    aria-label={`Name ${i + 1}`}
                                    placeholder="Name (optional)"
                                    value={t.name ?? ''}
                                    onChange={(e) => setTarget(i, { name: e.target.value })}
                                />
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    aria-label={`Remove number ${i + 1}`}
                                    onClick={() => change({ transfer_numbers: policy.transfer_numbers.filter((_, j) => j !== i) })}
                                >
                                    <Trash2 className="h-4 w-4" />
                                </Button>
                            </div>
                        ))}
                        {policy.transfer_numbers.length < MAX_NUMBERS && (
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => change({ transfer_numbers: [...policy.transfer_numbers, { number: '', name: '' }] })}
                            >
                                <Plus className="h-4 w-4" />
                                Add a number
                            </Button>
                        )}
                    </fieldset>

                    <fieldset className="space-y-2">
                        <legend className="text-sm font-medium">When someone is there</legend>
                        <label className="flex items-center gap-2 text-sm">
                            <input
                                type="checkbox"
                                checked={hours.enabled}
                                onChange={(e) => {
                                    setSaved(false);
                                    setHours({ ...hours, enabled: e.target.checked });
                                }}
                            />
                            Only at set hours (otherwise the agent&apos;s own hours)
                        </label>
                        {hours.enabled && (
                            <div className="flex flex-wrap items-center gap-2">
                                {DAYS.map((label, d) => (
                                    <label key={label} className="flex items-center gap-1 text-xs">
                                        <input
                                            type="checkbox"
                                            checked={hours.days.includes(d)}
                                            onChange={() => {
                                                setSaved(false);
                                                setHours({
                                                    ...hours,
                                                    days: hours.days.includes(d) ? hours.days.filter((x) => x !== d) : [...hours.days, d].sort(),
                                                });
                                            }}
                                        />
                                        {label}
                                    </label>
                                ))}
                                <input
                                    type="time"
                                    aria-label="From"
                                    className={`${FIELD} w-28`}
                                    value={hours.start}
                                    onChange={(e) => setHours({ ...hours, start: e.target.value })}
                                />
                                <input
                                    type="time"
                                    aria-label="Until"
                                    className={`${FIELD} w-28`}
                                    value={hours.end}
                                    onChange={(e) => setHours({ ...hours, end: e.target.value })}
                                />
                            </div>
                        )}
                        <p className="text-xs text-muted-foreground">
                            Outside these hours the agent offers a callback instead of ringing anyone.
                        </p>
                    </fieldset>

                    <fieldset className="space-y-1">
                        <legend className="text-sm font-medium">Always goes to a person</legend>
                        {loaded.topics.map((topic) => (
                            <label key={topic.key} className="flex items-center gap-2 text-sm">
                                <input
                                    type="checkbox"
                                    checked={policy.always_transfer_topics.includes(topic.key)}
                                    onChange={() => toggleTopic(topic.key)}
                                />
                                {topic.label}
                            </label>
                        ))}
                        {policy.always_transfer_topics.includes('refund_over_limit') && (
                            <label className="flex items-center gap-2 text-sm">
                                Refund limit (₹)
                                <input
                                    className={`${FIELD} w-32`}
                                    inputMode="numeric"
                                    aria-label="Refund limit"
                                    value={policy.refund_limit ?? ''}
                                    onChange={(e) => change({ refund_limit: e.target.value.trim() === '' ? null : Number(e.target.value) })}
                                />
                            </label>
                        )}
                        <label className="block text-sm">
                            Your own phrases, comma separated
                            <input
                                className={FIELD}
                                aria-label="Your own phrases"
                                placeholder="cancel my membership, speak to the doctor"
                                value={custom}
                                onChange={(e) => {
                                    setSaved(false);
                                    setCustom(e.target.value);
                                }}
                            />
                        </label>
                    </fieldset>

                    <label className="flex items-center gap-2 text-sm">
                        Tries at one step before a person takes over
                        <select
                            className={`${FIELD} w-20`}
                            aria-label="Tries before a person takes over"
                            value={policy.max_ai_attempts}
                            onChange={(e) => change({ max_ai_attempts: Number(e.target.value) })}
                        >
                            {[1, 2, 3, 4, 5].map((n) => (
                                <option key={n} value={n}>
                                    {n}
                                </option>
                            ))}
                        </select>
                    </label>

                    <div className="flex items-center gap-3">
                        <Button size="sm" disabled={saving} onClick={() => void save()}>
                            {saving ? 'Saving…' : 'Save to draft'}
                        </Button>
                        {saved && <span className="text-xs text-muted-foreground">Saved.</span>}
                    </div>
                </div>
            )}
            {error && (
                <p className="text-sm text-red-700 dark:text-red-300" role="alert">
                    {error}
                </p>
            )}
        </section>
    );
}

export default EscalationSettingsCard;
