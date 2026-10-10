'use client';

/**
 * Agents → Skills: every skill this workspace keeps, as a full card.
 *
 * skills-and-context.md asks each skill to explain five things: what it
 * helps accomplish (with an example), what information and app access it
 * needs, what it produces, whether it can take external actions, and where
 * it is enabled. The card answers all five, never with a blank — where a
 * skill's own text does not say, the card says so. Beneath them, what the
 * shelf never showed: the skill's versions and who made each one, the
 * evidence a learned version was offered on, and the agents using it.
 *
 * Roll back is one press for an admin; Add to an agent puts it on one more.
 * Browsing the whole shelf stays in the Marketplace — this is what you have.
 */

import { History, Loader2, Plus, Undo2 } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
    attachSkillApiV1EvolveSkillsSlugAgentsPost,
    getWorkflowsApiV1WorkflowFetchGet,
    rollbackSkillApiV1EvolveSkillsSlugRollbackPost,
    skillsTabApiV1EvolveSkillsGet,
} from '@/client/sdk.gen';
import type { SkillTabCard, SkillVersionOut } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';

type Bot = { id: number; name: string };

const STATUS: Record<string, string> = {
    published: 'Published',
    offered: 'Waiting for approval',
    draft: 'Draft',
    rejected: 'Not offered',
    rolled_back: 'Rolled back',
    discarded: 'Turned down',
};

const ORIGIN: Record<string, string> = {
    person: 'Written by a person',
    learned: 'Learned from work',
    remembered: 'Remembered from a conversation',
};

export function versionLine(version: SkillVersionOut): string {
    const status = STATUS[version.status] ?? version.status;
    const origin = ORIGIN[version.origin] ?? version.origin;
    return `Version ${version.version} · ${origin} · ${status}`;
}

function Answer({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div>
            <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
            <dd className="text-sm">{children}</dd>
        </div>
    );
}

function List({ items }: { items: string[] }) {
    if (items.length === 1) return <>{items[0]}</>;
    return (
        <ul className="list-disc pl-5">
            {items.map((item, i) => (
                <li key={i}>{item}</li>
            ))}
        </ul>
    );
}

export function SkillExplainCard({
    skill,
    bots,
    busy,
    onRollback,
    onAttach,
}: {
    skill: SkillTabCard;
    bots: Bot[];
    busy: boolean;
    onRollback: () => void;
    onAttach: (workflowId: number) => void;
}) {
    const [picking, setPicking] = useState(false);
    const explain = skill.explain;
    const versions = skill.versions ?? [];
    const improvement = skill.improvement as
        | {
              version?: number;
              related?: { n?: number; baseline_passed?: number; candidate_passed?: number };
              unrelated?: { n?: number; regressions?: number };
              cost?: { model_calls?: number; tokens?: number };
              evidence?: number;
          }
        | null
        | undefined;
    const on = new Set((skill.on_agents ?? []).map((a) => a.id));
    const free = bots.filter((b) => !on.has(b.id));

    return (
        <article className="rounded-xl border border-border bg-card p-4" data-testid="skill-tab-card">
            <header className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <h3 className="text-base font-semibold">
                        {skill.emoji ? <span aria-hidden="true">{skill.emoji} </span> : null}
                        {skill.title}
                    </h3>
                    <p className="text-xs text-muted-foreground">
                        {skill.active_version ? `Version ${skill.active_version} in use` : 'As shipped'}
                        {skill.own ? ' · Yours' : skill.division ? ` · ${skill.division}` : ''}
                    </p>
                </div>
                <div className="flex shrink-0 gap-1">
                    {skill.installed && free.length > 0 ? (
                        <Button
                            size="sm"
                            variant="outline"
                            className="rounded-full"
                            disabled={busy}
                            onClick={() => setPicking((v) => !v)}
                            aria-label={`Add ${skill.title} to an agent`}
                        >
                            <Plus className="mr-1 h-3 w-3" aria-hidden="true" />
                            Add to agent
                        </Button>
                    ) : null}
                    {skill.active_version ? (
                        <Button
                            size="sm"
                            variant="ghost"
                            className="rounded-full"
                            disabled={busy}
                            onClick={onRollback}
                            aria-label={`Roll back ${skill.title}`}
                        >
                            <Undo2 className="mr-1 h-3 w-3" aria-hidden="true" />
                            Roll back
                        </Button>
                    ) : null}
                </div>
            </header>

            {picking ? (
                <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Agents">
                    {free.map((bot) => (
                        <Button
                            key={bot.id}
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            onClick={() => {
                                setPicking(false);
                                onAttach(bot.id);
                            }}
                        >
                            {bot.name}
                        </Button>
                    ))}
                </div>
            ) : null}

            <dl className="mt-3 grid gap-3 sm:grid-cols-2">
                <Answer label="What it helps with">
                    {explain.accomplish}
                    <span className="mt-0.5 block text-xs text-muted-foreground">For example: {explain.example}</span>
                </Answer>
                <Answer label="What it needs">
                    <List items={explain.needs} />
                </Answer>
                <Answer label="What it produces">
                    <List items={explain.produces} />
                </Answer>
                <Answer label="Can it act outside Decibyl?">{explain.external_actions.sentence}</Answer>
                <Answer label="Where it is on">
                    <List items={explain.enabled_on} />
                </Answer>
                {(explain.wont_do ?? []).length > 0 ? (
                    <Answer label="What it will not do">
                        <List items={explain.wont_do ?? []} />
                    </Answer>
                ) : null}
            </dl>

            {improvement ? (
                <p className="mt-3 rounded-md bg-muted/40 p-2 text-xs" data-testid="skill-improvement">
                    Version {improvement.version} was tested before it was offered: held-out tasks of this kind went
                    from {improvement.related?.baseline_passed ?? 0} to {improvement.related?.candidate_passed ?? 0}{' '}
                    of {improvement.related?.n ?? 0} right, and{' '}
                    {(improvement.unrelated?.regressions ?? 0) === 0
                        ? 'no unrelated task got worse'
                        : `${improvement.unrelated?.regressions} unrelated tasks got worse`}{' '}
                    ({improvement.unrelated?.n ?? 0} checked). Learned from {improvement.evidence ?? 0} things that
                    happened; {improvement.cost?.model_calls ?? 0} model calls to propose and test.
                </p>
            ) : null}

            {versions.length > 0 ? (
                <details className="mt-3 text-sm">
                    <summary className="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground">
                        <History className="h-3 w-3" aria-hidden="true" />
                        {versions.length} {versions.length === 1 ? 'version' : 'versions'}
                    </summary>
                    <ol className="mt-2 space-y-2" aria-label="Version history">
                        {[...versions].reverse().map((v) => (
                            <li key={v.id} className="rounded-md border border-border p-2">
                                <p className="text-xs font-medium">{versionLine(v)}</p>
                                {(v.lessons ?? []).length > 0 ? (
                                    <ul className="mt-1 list-disc pl-5 text-xs text-muted-foreground">
                                        {(v.lessons ?? []).map((l, i) => (
                                            <li key={i}>{l}</li>
                                        ))}
                                    </ul>
                                ) : null}
                                {v.reason ? <p className="mt-1 text-xs text-muted-foreground">{v.reason}</p> : null}
                            </li>
                        ))}
                    </ol>
                </details>
            ) : null}
        </article>
    );
}

export function SkillsTab() {
    const { user, loading: authLoading } = useAuth();
    const [skills, setSkills] = useState<SkillTabCard[] | null>(null);
    const [bots, setBots] = useState<Bot[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const fetched = useRef(false);

    const load = useCallback(async () => {
        const response = await skillsTabApiV1EvolveSkillsGet();
        if (response.error || !response.data) {
            setError(detailFromError(response.error, 'Your skills could not be loaded.'));
            setSkills([]);
            return;
        }
        setSkills(response.data.skills);
    }, []);

    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void load();
        void (async () => {
            const response = await getWorkflowsApiV1WorkflowFetchGet();
            if (response.error || !response.data) return;
            setBots(response.data.map((w) => ({ id: w.id, name: w.name })));
        })();
    }, [authLoading, user, load]);

    const rollback = async (slug: string) => {
        setBusy(true);
        setError(null);
        const response = await rollbackSkillApiV1EvolveSkillsSlugRollbackPost({ path: { slug }, body: {} });
        setBusy(false);
        if (response.error) {
            setError(detailFromError(response.error, 'Could not roll that back.'));
            return;
        }
        await load();
    };

    const attach = async (slug: string, workflowId: number) => {
        setBusy(true);
        setError(null);
        const response = await attachSkillApiV1EvolveSkillsSlugAgentsPost({
            path: { slug },
            body: { workflow_id: workflowId },
        });
        setBusy(false);
        if (response.error) {
            setError(detailFromError(response.error, 'Could not add that.'));
            return;
        }
        await load();
    };

    if (skills === null) {
        return (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Loading…
            </p>
        );
    }

    return (
        <div className="space-y-4">
            {error ? (
                <p className="text-sm text-destructive" role="alert">
                    {error}
                </p>
            ) : null}
            {skills.length === 0 ? (
                <div className="rounded-xl border border-border bg-card p-6 text-sm text-muted-foreground">
                    No skills yet. Find one in the{' '}
                    <Link href="/marketplace/skills" className="underline underline-offset-4">
                        Marketplace
                    </Link>
                    , or after something goes well in a chat, say &ldquo;remember this as my way of doing it&rdquo;.
                </div>
            ) : (
                <div className="grid gap-4 lg:grid-cols-2">
                    {skills.map((skill) => (
                        <SkillExplainCard
                            key={skill.slug}
                            skill={skill}
                            bots={bots}
                            busy={busy}
                            onRollback={() => void rollback(skill.slug)}
                            onAttach={(id) => void attach(skill.slug, id)}
                        />
                    ))}
                </div>
            )}
            {skills.length > 0 ? (
                <p className="text-xs text-muted-foreground">
                    More skills are in the{' '}
                    <Link href="/marketplace/skills" className="underline underline-offset-4">
                        Marketplace
                    </Link>
                    .
                </p>
            ) : null}
        </div>
    );
}
