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

import { Brain, Database, Plug, Wrench } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';

import { listToolsApiV1ToolsGet } from '@/client/sdk.gen';
import type { FlowNode } from '@/components/flow/types';
import { initials } from '@/components/layout/SidebarBots';
import { MemoryList } from '@/components/memory/MemoryList';
import { useAuth } from '@/lib/auth';

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

/** Outside software, as opposed to something the bot does on its own. */
export function isIntegration(category: string | undefined): boolean {
    return category === 'composio' || category === 'mcp' || category === 'http_api' || category === 'google_calendar';
}

/** One tint per category. Kept in step with app/tools/config.tsx. */
const CHIP_COLOURS: Record<string, string> = {
    http_api: '#3B82F6',
    end_call: '#EF4444',
    transfer_call: '#10B981',
    calculator: '#F59E0B',
    rate_table: '#D97706',
    mcp: '#8B5CF6',
    google_calendar: '#0F9D58',
    composio: '#EC4899',
};

type Chip = { id: string; label: string; colour: string; title?: string };

type ToolSummary = { name: string; category: string; definition?: Record<string, unknown> };

function titleCase(slug: string): string {
    return slug
        .split(/[_\-\s]+/)
        .filter(Boolean)
        .map((w) => w[0].toUpperCase() + w.slice(1))
        .join(' ');
}

/** The outside software an integration tool runs on, named the way its
 *  owner would: "Google Calendar", not "Book Appointment". The tool's own
 *  name is a step inside that app and goes in the tooltip. A custom HTTP
 *  tool is its own app, so it keeps its name. Anything this cannot place
 *  falls back to the tool name rather than to nothing. */
export function appOf(tool: ToolSummary): string {
    const definition = tool.definition ?? {};
    if (tool.category === 'google_calendar') return 'Google Calendar';
    if (tool.category === 'composio') {
        const slug = definition.toolkit ?? definition.toolkit_slug ?? definition.app;
        return typeof slug === 'string' && slug ? titleCase(slug) : tool.name;
    }
    if (tool.category === 'mcp') {
        const server = definition.server_name ?? definition.name;
        return typeof server === 'string' && server ? server : `${tool.name} (MCP)`;
    }
    return tool.name;
}

function chipOf(id: string, tool: ToolSummary | undefined): Chip {
    return {
        id,
        label: tool?.name ?? 'Skill',
        colour: (tool && CHIP_COLOURS[tool.category]) || '#6B7280',
    };
}

/** One chip per app: two calendar tools are one Google Calendar. */
function integrationChips(ids: string[], tools: Record<string, ToolSummary>): Chip[] {
    const byApp = new Map<string, Chip>();
    for (const id of ids) {
        const tool = tools[id];
        if (!tool) {
            byApp.set(id, chipOf(id, undefined));
            continue;
        }
        const app = appOf(tool);
        const existing = byApp.get(app);
        if (existing) {
            existing.title = `${existing.title}, ${tool.name}`;
            continue;
        }
        byApp.set(app, { id, label: app, colour: CHIP_COLOURS[tool.category] || '#6B7280', title: tool.name });
    }
    return [...byApp.values()];
}

function ChipSection({
    icon: Icon,
    title,
    chips,
    empty,
}: {
    icon: typeof Wrench;
    title: string;
    chips: Chip[];
    empty: React.ReactNode;
}) {
    return (
        <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <Icon className="h-3.5 w-3.5" aria-hidden />
                {title}
            </h3>
            {chips.length === 0 ? (
                <p className="text-sm text-muted-foreground">{empty}</p>
            ) : (
                <ul className="flex flex-wrap gap-1.5" aria-label={title}>
                    {chips.map((chip) => (
                        <li
                            key={chip.id}
                            title={chip.title}
                            className="rounded-md border px-2 py-0.5 text-xs font-medium"
                            style={{
                                borderColor: `${chip.colour}55`,
                                backgroundColor: `${chip.colour}1a`,
                                color: chip.colour,
                            }}
                        >
                            {chip.label}
                        </li>
                    ))}
                </ul>
            )}
        </section>
    );
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
    const [tools, setTools] = useState<Record<string, ToolSummary>>({});

    const skillIds = useMemo(() => skillIdsOf(nodes), [nodes]);
    const documents = useMemo(() => documentCountOf(nodes), [nodes]);
    const steps = nodes.filter((n) => n.type !== 'globalNode' && n.type !== 'startCall').length;

    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void (async () => {
            const response = await listToolsApiV1ToolsGet();
            if (response.error || !response.data) return;
            const byId: Record<string, ToolSummary> = {};
            for (const tool of response.data)
                byId[tool.tool_uuid] = {
                    name: tool.name,
                    category: tool.category,
                    definition: (tool.definition ?? {}) as Record<string, unknown>,
                };
            setTools(byId);
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

            {/* Two lists, the way the reference splits them: skills are what
                the bot itself can do, integrations are the outside software it
                reaches. Each chip wears its category's colour, so a calendar
                reads as a calendar before the label is read. */}
            <ChipSection
                icon={Wrench}
                title="Skills"
                chips={skillIds.filter((id) => !isIntegration(tools[id]?.category)).map((id) => chipOf(id, tools[id]))}
                empty={
                    <>
                        None yet.{' '}
                        <Link href={`/workflow/${workflowId}/tools`} className="underline underline-offset-2">
                            Add a skill
                        </Link>
                    </>
                }
            />
            <ChipSection
                icon={Plug}
                title="Integrations & tools"
                chips={integrationChips(skillIds.filter((id) => isIntegration(tools[id]?.category)), tools)}
                empty={
                    <>
                        Nothing connected.{' '}
                        <Link href="/integrations/apps" className="underline underline-offset-2">
                            Marketplace
                        </Link>
                    </>
                }
            />

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
                {/* The organisation's confirmed facts and this bot's own,
                    each with a way to forget it. Shared with Decibyl's About. */}
                <MemoryList workflowId={workflowId} botName={name} />
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
