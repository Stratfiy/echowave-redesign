'use client';

/**
 * The bot as a teammate, at the side of its instructions.
 *
 * Slack's agent profile (name, what it does, skills) is the reference: the
 * instructions are what the bot says, and the profile is who it is -- the
 * skills it can use, the documents it reads, the brains and voice it runs on
 * and what a minute costs. Those used to sit above the prompt as a band of
 * tiles, so the first screenful of "Instructions" was a rate card. Now the
 * prompt is the page and the profile is the column beside it.
 */

import { Brain, Database, Wrench } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';

import { listToolsApiV1ToolsGet, readMemoryApiV1OrganisationMemoryGet } from '@/client/sdk.gen';
import type { FlowNode } from '@/components/flow/types';
import { initials } from '@/components/layout/SidebarBots';
import { useAuth } from '@/lib/auth';

type Fact = { key: string; value: string; subject?: string | null };

/** Every tool a step of this bot names, once each. */
export function skillIdsOf(nodes: FlowNode[]): string[] {
    const seen = new Set<string>();
    for (const node of nodes) {
        const ids = (node.data as { tool_uuids?: string[] } | undefined)?.tool_uuids ?? [];
        for (const id of ids) if (id) seen.add(id);
    }
    return [...seen];
}

export function documentCountOf(nodes: FlowNode[]): number {
    const seen = new Set<string>();
    for (const node of nodes) {
        const ids = (node.data as { document_uuids?: string[] } | undefined)?.document_uuids ?? [];
        for (const id of ids) if (id) seen.add(id);
    }
    return seen.size;
}

export function AgentProfilePanel({
    workflowId,
    name,
    nodes,
    children,
}: {
    workflowId: number;
    name: string;
    nodes: FlowNode[];
    /** The brains-and-voice row, rendered under the profile. */
    children?: React.ReactNode;
}) {
    const { user, loading: authLoading } = useAuth();
    const fetched = useRef(false);
    const [toolNames, setToolNames] = useState<Record<string, string>>({});
    const [facts, setFacts] = useState<Fact[] | null>(null);

    const skillIds = useMemo(() => skillIdsOf(nodes), [nodes]);
    const documents = useMemo(() => documentCountOf(nodes), [nodes]);
    const steps = nodes.filter((n) => n.type !== 'globalNode' && n.type !== 'startCall').length;

    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void (async () => {
            const response = await listToolsApiV1ToolsGet();
            if (response.error || !response.data) return;
            const names: Record<string, string> = {};
            for (const tool of response.data) names[tool.tool_uuid] = tool.name;
            setToolNames(names);
        })();
        void (async () => {
            // What the business has confirmed about itself: the memory every
            // bot here draws on. A failure costs the section, not the panel.
            const response = await readMemoryApiV1OrganisationMemoryGet();
            if (response.error || !response.data) return;
            setFacts(
                (response.data.facts ?? []).map((f) => ({ key: f.key, value: f.value, subject: f.subject })),
            );
        })();
    }, [authLoading, user]);

    return (
        <div className="flex flex-col gap-6 p-5" data-testid="agent-profile">
            <div className="flex items-center gap-3">
                <span
                    aria-hidden
                    className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-[var(--accent-brand-soft)] text-base font-semibold text-[var(--accent-brand)]"
                >
                    {initials(name || 'Bot')}
                </span>
                <div className="min-w-0">
                    <p className="truncate text-base font-semibold">{name || 'Bot'}</p>
                    <p className="text-xs text-muted-foreground">
                        {steps} {steps === 1 ? 'step' : 'steps'}
                    </p>
                </div>
            </div>

            <section>
                <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    <Wrench className="h-3.5 w-3.5" aria-hidden />
                    Skills
                </h3>
                {skillIds.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                        None yet.{' '}
                        <Link href={`/workflow/${workflowId}/tools`} className="underline underline-offset-2">
                            Add a skill
                        </Link>
                    </p>
                ) : (
                    <ul className="flex flex-wrap gap-1.5" aria-label="Skills">
                        {skillIds.map((id) => (
                            <li
                                key={id}
                                className="rounded-md border border-border bg-background px-2 py-0.5 text-xs"
                            >
                                {toolNames[id] ?? 'Skill'}
                            </li>
                        ))}
                    </ul>
                )}
            </section>

            <section>
                <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    <Database className="h-3.5 w-3.5" aria-hidden />
                    Knowledge
                </h3>
                <p className="text-sm text-muted-foreground">
                    {documents === 0 ? 'No documents of its own.' : `${documents} ${documents === 1 ? 'document' : 'documents'} of its own.`}{' '}
                    <Link href="/files" className="underline underline-offset-2">
                        Company knowledge
                    </Link>{' '}
                    is read by every bot.
                </p>
            </section>

            <section>
                <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    <Brain className="h-3.5 w-3.5" aria-hidden />
                    Memory
                </h3>
                {facts === null ? null : facts.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Nothing confirmed about the business yet.</p>
                ) : (
                    <ul className="space-y-1 text-sm" aria-label="Memory">
                        {facts.slice(0, 5).map((fact, i) => (
                            <li key={`${fact.key}-${i}`} className="flex gap-2">
                                <span className="shrink-0 text-muted-foreground">{fact.key}</span>
                                <span className="min-w-0 truncate">{fact.value}</span>
                            </li>
                        ))}
                        {facts.length > 5 && (
                            <li className="text-xs text-muted-foreground">{facts.length - 5} more</li>
                        )}
                    </ul>
                )}
            </section>

            {children && (
                <section>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        Brains and voice
                    </h3>
                    {children}
                </section>
            )}
        </div>
    );
}

export default AgentProfilePanel;
