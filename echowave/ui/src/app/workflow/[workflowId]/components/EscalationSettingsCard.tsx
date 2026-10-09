'use client';

/**
 * When this agent hands a caller to a person, on the agent's own page.
 *
 * Who to ring (in order, Indian numbers only, each briefed in their own
 * language), when they are there, which topics always go to a person and
 * which the agent answers itself, named teams and which topics (and
 * out-of-scope subjects) go to which, rules that only watch (shadow), and
 * how many tries the agent gets at one step. Saved into the
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

type Target = { number: string; name?: string | null; language?: string };
type Slot = { day_of_week: number; start_time: string; end_time: string };
type Hours = { enabled: boolean; timezone: string; slots: Slot[] };
type Team = { name: string; numbers: Target[] };
type OutOfScope = { phrase: string; team?: string | null };
type Policy = {
    transfer_numbers: Target[];
    transfer_hours: Hours;
    always_transfer_topics: string[];
    custom_topics: string[];
    never_transfer_topics?: string[];
    rule_modes?: Record<string, 'on' | 'shadow'>;
    teams?: Team[];
    topic_teams?: Record<string, string>;
    out_of_scope_topics?: OutOfScope[];
    refund_limit: number | null;
    max_ai_attempts: number;
    [key: string]: unknown;
};
/** A team as the form edits it: numbers as one comma-separated line. */
type TeamForm = { name: string; numbers: string };

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MAX_NUMBERS = 5;
const MAX_TEAMS = 5;
const ENGLISH = [{ key: 'en', label: 'English' }];

const phrases = (text: string) =>
    text
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);

export function teamsToForm(teams: Team[] | undefined): TeamForm[] {
    return (teams ?? []).map((t) => ({ name: t.name, numbers: t.numbers.map((n) => n.number).join(', ') }));
}

export function formToTeams(rows: TeamForm[]): Team[] {
    return rows
        .filter((r) => r.name.trim())
        .map((r) => ({ name: r.name.trim(), numbers: phrases(r.numbers).map((number) => ({ number })) }));
}
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
    const [never, setNever] = useState('');
    const [teams, setTeams] = useState<TeamForm[]>([]);
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
        setNever((p.never_transfer_topics ?? []).join(', '));
        setTeams(teamsToForm(p.teams));
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

    const modes = policy.rule_modes ?? {};
    const topicTeams = policy.topic_teams ?? {};
    const outOfScope = policy.out_of_scope_topics ?? [];
    const teamNames = teams.map((t) => t.name.trim()).filter(Boolean);
    const languages = loaded.briefing_languages?.length ? loaded.briefing_languages : ENGLISH;
    const toggleShadow = (key: string) => {
        const next = { ...modes };
        if (next[key] === 'shadow') delete next[key];
        else next[key] = 'shadow';
        change({ rule_modes: next });
    };
    const setTopicTeam = (key: string, team: string) => {
        const next = { ...topicTeams };
        if (team) next[key] = team;
        else delete next[key];
        change({ topic_teams: next });
    };
    const setOutOfScope = (i: number, patch: Partial<OutOfScope>) =>
        change({ out_of_scope_topics: outOfScope.map((o, j) => (j === i ? { ...o, ...patch } : o)) });
    const editTeam = (i: number, patch: Partial<TeamForm>) => {
        setSaved(false);
        setTeams(teams.map((t, j) => (j === i ? { ...t, ...patch } : t)));
    };
    const routable = [
        ...loaded.topics.filter((t) => policy.always_transfer_topics.includes(t.key)),
        ...(phrases(custom).length ? [{ key: 'custom', label: 'Your own phrases' }] : []),
    ];

    const save = async () => {
        setSaving(true);
        setError(null);
        const known = new Set(teamNames.map((n) => n.toLowerCase()));
        const body = {
            ...policy,
            transfer_numbers: policy.transfer_numbers
                .filter((t) => t.number.trim())
                .map((t) => ({ number: t.number.trim(), name: t.name?.trim() || null, language: t.language || 'en' })),
            transfer_hours: formToHours(hours, policy.transfer_hours?.timezone),
            custom_topics: phrases(custom),
            never_transfer_topics: phrases(never),
            teams: formToTeams(teams),
            // A mapping to a team that was removed goes with it.
            topic_teams: Object.fromEntries(Object.entries(topicTeams).filter(([, t]) => known.has(t.toLowerCase()))),
            out_of_scope_topics: outOfScope
                .filter((o) => o.phrase.trim())
                .map((o) => ({
                    phrase: o.phrase.trim(),
                    team: o.team && known.has(o.team.toLowerCase()) ? o.team : null,
                })),
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
                                <select
                                    className={`${FIELD} w-28`}
                                    aria-label={`Briefing language ${i + 1}`}
                                    title="The language this person is briefed in"
                                    value={t.language ?? 'en'}
                                    onChange={(e) => setTarget(i, { language: e.target.value })}
                                >
                                    {languages.map((l) => (
                                        <option key={l.key} value={l.key}>
                                            {l.label}
                                        </option>
                                    ))}
                                </select>
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
                        <p className="text-xs text-muted-foreground">
                            Indian (+91) numbers only: both sides of a call must be in India. Each person hears the
                            briefing in their language.
                        </p>
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

                    <fieldset className="space-y-1">
                        <legend className="text-sm font-medium">The agent answers these itself</legend>
                        <input
                            className={FIELD}
                            aria-label="Never hand over for"
                            placeholder="opening hours, price list"
                            value={never}
                            onChange={(e) => {
                                setSaved(false);
                                setNever(e.target.value);
                            }}
                        />
                        <p className="text-xs text-muted-foreground">
                            Comma separated. A caller who asks for a person, or has an emergency, still reaches one.
                        </p>
                    </fieldset>

                    <fieldset className="space-y-2">
                        <legend className="text-sm font-medium">Teams</legend>
                        {teams.map((t, i) => (
                            <div key={i} className="flex gap-2">
                                <input
                                    className={`${FIELD} w-36`}
                                    aria-label={`Team ${i + 1} name`}
                                    placeholder="Billing"
                                    value={t.name}
                                    onChange={(e) => editTeam(i, { name: e.target.value })}
                                />
                                <input
                                    className={FIELD}
                                    inputMode="tel"
                                    aria-label={`Team ${i + 1} numbers`}
                                    placeholder="+91 98765 43210, +91 98765 43211"
                                    value={t.numbers}
                                    onChange={(e) => editTeam(i, { numbers: e.target.value })}
                                />
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    aria-label={`Remove team ${i + 1}`}
                                    onClick={() => {
                                        setSaved(false);
                                        setTeams(teams.filter((_, j) => j !== i));
                                    }}
                                >
                                    <Trash2 className="h-4 w-4" />
                                </Button>
                            </div>
                        ))}
                        {teams.length < MAX_TEAMS && (
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                    setSaved(false);
                                    setTeams([...teams, { name: '', numbers: '' }]);
                                }}
                            >
                                <Plus className="h-4 w-4" />
                                Add a team
                            </Button>
                        )}
                        {teamNames.length > 0 &&
                            routable.map((topic) => (
                                <label key={topic.key} className="flex items-center gap-2 text-sm">
                                    <span className="min-w-0 flex-1">{topic.label}</span>
                                    <select
                                        className={`${FIELD} w-40`}
                                        aria-label={`Team for ${topic.label}`}
                                        value={topicTeams[topic.key] ?? ''}
                                        onChange={(e) => setTopicTeam(topic.key, e.target.value)}
                                    >
                                        <option value="">The numbers above</option>
                                        {teamNames.map((n) => (
                                            <option key={n} value={n}>
                                                {n}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                            ))}
                        <p className="text-xs text-muted-foreground">
                            A team is rung first for the topics sent to it, then the numbers above.
                        </p>
                    </fieldset>

                    <fieldset className="space-y-2">
                        <legend className="text-sm font-medium">Not this agent&apos;s job</legend>
                        {outOfScope.map((o, i) => (
                            <div key={i} className="flex gap-2">
                                <input
                                    className={FIELD}
                                    aria-label={`Out of scope ${i + 1}`}
                                    placeholder="home loans"
                                    value={o.phrase}
                                    onChange={(e) => setOutOfScope(i, { phrase: e.target.value })}
                                />
                                <select
                                    className={`${FIELD} w-40`}
                                    aria-label={`Team for out of scope ${i + 1}`}
                                    value={o.team ?? ''}
                                    onChange={(e) => setOutOfScope(i, { team: e.target.value || null })}
                                >
                                    <option value="">The numbers above</option>
                                    {teamNames.map((n) => (
                                        <option key={n} value={n}>
                                            {n}
                                        </option>
                                    ))}
                                </select>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    aria-label={`Remove out of scope ${i + 1}`}
                                    onClick={() => change({ out_of_scope_topics: outOfScope.filter((_, j) => j !== i) })}
                                >
                                    <Trash2 className="h-4 w-4" />
                                </Button>
                            </div>
                        ))}
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => change({ out_of_scope_topics: [...outOfScope, { phrase: '', team: null }] })}
                        >
                            <Plus className="h-4 w-4" />
                            Add a topic
                        </Button>
                    </fieldset>

                    {(loaded.rules ?? []).some((r) => r.can_shadow) && (
                        <fieldset className="space-y-1">
                            <legend className="text-sm font-medium">Watch only</legend>
                            {(loaded.rules ?? [])
                                .filter((r) => r.can_shadow)
                                .map((rule) => (
                                    <label key={rule.key} className="flex items-center gap-2 text-sm">
                                        <input
                                            type="checkbox"
                                            aria-label={`Watch only: ${rule.label}`}
                                            checked={modes[rule.key] === 'shadow'}
                                            onChange={() => toggleShadow(rule.key)}
                                        />
                                        {rule.label}
                                    </label>
                                ))}
                            <p className="text-xs text-muted-foreground">
                                A rule set to watch only never hands a call over. Each call notes what it would have
                                done, so you can see before you switch it on. Emergencies and a caller asking for a
                                person always act.
                            </p>
                        </fieldset>
                    )}

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
