'use client';

import { MessageSquare } from 'lucide-react';
import Link from 'next/link';
import { useMemo, useRef, useState } from 'react';

import type { FolderResponse, TeamMember, WorkflowListResponse } from '@/client/types.gen';
import { ArtImage } from '@/components/art/Art3D';
import { AgentAvatar } from '@/components/avatar/AgentAvatar';
import { type Avatar, faceOf } from '@/components/avatar/avatar';
import { AvatarCustomizer } from '@/components/avatar/AvatarCustomizer';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { jobArt } from '@/lib/art';
import { useFeature } from '@/lib/features';
import { cn } from '@/lib/utils';

import { type Tone, toneOf, useTeamStatus } from '../useTeamStatus';
import { WorkflowTable } from '../WorkflowTable';
import { FolderSection } from './FolderSection';

interface AgentFolderViewProps {
    /** Active (non-archived) agents only. */
    workflows: WorkflowListResponse[];
    folders: FolderResponse[];
}

/** The dot, the word and the order for each tone; the order is the
 *  roster's own (routes/team.py): the agents that need you first. */
const TONES: Record<Tone, { label: string; dot: string; pill: string; order: number }> = {
    attention: { label: 'Needs you', dot: 'bg-red-500', pill: 'bg-red-500/10 text-red-700 dark:text-red-300', order: 0 },
    working: { label: 'Working', dot: 'bg-emerald-500', pill: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300', order: 1 },
    idle: { label: 'Idle', dot: 'bg-sky-500', pill: 'bg-sky-500/10 text-sky-700 dark:text-sky-300', order: 2 },
    paused: { label: 'Paused', dot: 'bg-amber-500', pill: 'bg-amber-500/10 text-amber-700 dark:text-amber-300', order: 3 },
};

function ago(at: string | null | undefined): string | null {
    if (!at) return null;
    const minutes = Math.round((Date.now() - new Date(at).getTime()) / 60000);
    if (!Number.isFinite(minutes) || minutes < 0) return null;
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.round(hours / 24)}d ago`;
}

/**
 * One agent, the way Paperclip shows an employee: who it is, what state
 * it is in, the sentence on what it is doing, its last line, and the day's
 * numbers. The whole card opens the profile; Message is its own button.
 */
function TeamCard({
    agent,
    member,
    face,
    onOpen,
}: {
    agent: WorkflowListResponse;
    member: TeamMember | undefined;
    /** The agent's face, when faces are on (agent_faces); the job's picture otherwise. */
    face: Avatar | null;
    onOpen: (event: React.MouseEvent<HTMLButtonElement>) => void;
}) {
    const tone = TONES[toneOf(member, agent.is_live)];
    const when = ago(member?.last_at ?? member?.at);
    return (
        <div
            data-testid={`agent-card-${agent.id}`}
            className="group relative flex min-w-0 flex-col rounded-2xl border border-border/70 bg-card p-4 transition-colors hover:border-border hover:bg-muted/30"
        >
            <button
                type="button"
                aria-label={`View ${agent.name}`}
                onClick={onOpen}
                className="absolute inset-0 z-10 rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <div className="flex items-start gap-3">
                {face ? (
                    <AgentAvatar avatar={face} tone={toneOf(member, agent.is_live)} size={48} />
                ) : (
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-muted/60">
                        <ArtImage name={jobArt(agent.name)} size={36} />
                    </div>
                )}
                <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{agent.name}</p>
                    <p className="truncate text-xs text-muted-foreground">
                        {agent.handle ? `@${agent.handle}` : `${agent.total_runs ?? 0} runs`}
                    </p>
                </div>
                <span className={cn('inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium', tone.pill)}>
                    <span className={cn('h-1.5 w-1.5 rounded-full', tone.dot)} />
                    {tone.label}
                </span>
            </div>
            <p className="mt-3 line-clamp-2 min-h-[2.5rem] text-sm text-foreground/80">
                {member?.status ?? (agent.is_live === false ? 'Paused. Switch it on when it is ready.' : 'Nothing yet today.')}
            </p>
            {member?.last_line ? (
                <p className="mt-2 line-clamp-1 rounded-lg bg-muted/50 px-2.5 py-1.5 text-xs text-muted-foreground">
                    “{member.last_line}”
                </p>
            ) : null}
            <div className="mt-3 flex items-center gap-3 border-t border-border/60 pt-3 text-xs text-muted-foreground">
                {member ? (
                    <>
                        <span><b className="font-semibold text-foreground">{member.calls}</b> today</span>
                        <span><b className="font-semibold text-foreground">{member.outcomes}</b> done</span>
                        {member.failures > 0 ? (
                            <span className="text-red-600 dark:text-red-400"><b className="font-semibold">{member.failures}</b> failed</span>
                        ) : null}
                    </>
                ) : (
                    <span>{agent.total_runs ?? 0} runs in all</span>
                )}
                <span className="ml-auto">{when ?? ''}</span>
                <Link
                    href={`/workflow/${agent.id}/thread`}
                    aria-label={`Message ${agent.name}`}
                    className="relative z-20 inline-flex h-7 w-7 items-center justify-center rounded-full border border-border bg-background text-muted-foreground hover:text-foreground"
                >
                    <MessageSquare className="h-3.5 w-3.5" />
                </Link>
            </div>
        </div>
    );
}

/**
 * Agent cards open a profile; the list retains existing folder management.
 */
export function AgentFolderView({ workflows, folders }: AgentFolderViewProps) {
    const [view, setView] = useState<'cards' | 'list'>('cards');
    const [selectedId, setSelectedId] = useState<number | null>(null);
    const [filter, setFilter] = useState<Tone | 'all'>('all');
    const profileOpener = useRef<HTMLElement | null>(null);
    const listButton = useRef<HTMLButtonElement | null>(null);
    const selected = workflows.find((agent) => agent.id === selectedId);
    const roster = useTeamStatus();
    // Faces changed on this page, ahead of the list being fetched again.
    const [faces, setFaces] = useState<Record<number, Avatar | null>>({});
    const [editingFace, setEditingFace] = useState(false);
    const facesOn = useFeature('agent_faces');
    const faceFor = (agent: WorkflowListResponse) =>
        faceOf(agent.id, agent.id in faces ? faces[agent.id] : (agent.avatar as Avatar | null | undefined));

    const counts = useMemo(() => {
        const out: Record<Tone, number> = { attention: 0, working: 0, idle: 0, paused: 0 };
        for (const agent of workflows) out[toneOf(roster[agent.id], agent.is_live)] += 1;
        return out;
    }, [workflows, roster]);

    const shown = useMemo(
        () =>
            workflows
                .filter((agent) => filter === 'all' || toneOf(roster[agent.id], agent.is_live) === filter)
                .sort(
                    (a, b) =>
                        TONES[toneOf(roster[a.id], a.is_live)].order - TONES[toneOf(roster[b.id], b.is_live)].order,
                ),
        [workflows, roster, filter],
    );
    const selectedMember = selected ? roster[selected.id] : undefined;

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
                {/* The team at a glance, and a filter: Paperclip's status row. */}
                <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter agents by status">
                    <button
                        type="button"
                        aria-pressed={filter === 'all'}
                        onClick={() => setFilter('all')}
                        className={cn(
                            'rounded-full border px-3 py-1 text-xs transition-colors',
                            filter === 'all' ? 'border-foreground/20 bg-foreground text-background' : 'border-border text-muted-foreground hover:bg-muted',
                        )}
                    >
                        All {workflows.length}
                    </button>
                    {(Object.keys(TONES) as Tone[]).map((tone) =>
                        counts[tone] > 0 ? (
                            <button
                                key={tone}
                                type="button"
                                aria-pressed={filter === tone}
                                onClick={() => setFilter(filter === tone ? 'all' : tone)}
                                className={cn(
                                    'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors',
                                    filter === tone ? 'border-foreground/20 bg-foreground text-background' : 'border-border text-muted-foreground hover:bg-muted',
                                )}
                            >
                                <span className={cn('h-1.5 w-1.5 rounded-full', TONES[tone].dot)} />
                                {TONES[tone].label} {counts[tone]}
                            </button>
                        ) : null,
                    )}
                </div>
                <div className="flex gap-1" role="group" aria-label="Agent directory view">
                    <Button asChild variant="ghost" size="sm"><Link href="/company">Org chart</Link></Button>
                    <Button variant={view === 'cards' ? 'secondary' : 'ghost'} size="sm" aria-pressed={view === 'cards'} onClick={() => setView('cards')}>Cards</Button>
                    <Button ref={listButton} variant={view === 'list' ? 'secondary' : 'ghost'} size="sm" aria-pressed={view === 'list'} onClick={() => setView('list')}>List</Button>
                </div>
            </div>
            {view === 'cards' ? (
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {shown.map((agent) => (
                        <TeamCard
                            key={agent.id}
                            agent={agent}
                            member={roster[agent.id]}
                            face={facesOn ? faceFor(agent) : null}
                            onOpen={(event) => { profileOpener.current = event.currentTarget; setSelectedId(agent.id); }}
                        />
                    ))}
                    {workflows.length === 0 && <p className="text-sm text-muted-foreground">No agents yet. Switch to List to manage your existing groups.</p>}
                    {workflows.length > 0 && shown.length === 0 && <p className="text-sm text-muted-foreground">No agent is in that state right now.</p>}
                </div>
            ) : <AgentGroupedList workflows={workflows} folders={folders} />}
            <Sheet open={Boolean(selected)} onOpenChange={(open) => { if (!open) setSelectedId(null); }}>
                <SheetContent className="overflow-y-auto sm:max-w-md" onCloseAutoFocus={(event) => { event.preventDefault(); (profileOpener.current?.isConnected ? profileOpener.current : listButton.current)?.focus(); }}>
                    <SheetHeader>
                        <SheetTitle>{selected?.name}</SheetTitle>
                        <SheetDescription>Agent profile</SheetDescription>
                    </SheetHeader>
                    {selected && (
                        <div className="space-y-6 px-4 pb-6">
                            {facesOn && (
                                <div className="flex flex-col items-center gap-2">
                                    <AgentAvatar
                                        avatar={faceFor(selected)}
                                        tone={toneOf(selectedMember, selected.is_live)}
                                        size={120}
                                        label={`${selected.name}'s face`}
                                    />
                                    <Button variant="outline" size="sm" onClick={() => setEditingFace(true)}>Change face</Button>
                                </div>
                            )}
                            <div className="space-y-3 rounded-2xl border border-border bg-muted/30 p-4">
                                <span className={cn('inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium', TONES[toneOf(selectedMember, selected.is_live)].pill)}>
                                    <span className={cn('h-1.5 w-1.5 rounded-full', TONES[toneOf(selectedMember, selected.is_live)].dot)} />
                                    {TONES[toneOf(selectedMember, selected.is_live)].label}
                                </span>
                                <p className="text-sm">{selectedMember?.status ?? 'No activity reported yet.'}</p>
                                {selectedMember ? (
                                    <dl className="grid grid-cols-3 gap-2 text-center">
                                        {([['Today', selectedMember.calls], ['Answered', selectedMember.answered], ['Done', selectedMember.outcomes]] as const).map(([label, value]) => (
                                            <div key={label} className="rounded-xl bg-background px-2 py-2">
                                                <dt className="text-[11px] text-muted-foreground">{label}</dt>
                                                <dd className="text-lg font-semibold tabular-nums">{value}</dd>
                                            </div>
                                        ))}
                                    </dl>
                                ) : null}
                                {selectedMember?.last_action ? (
                                    <p className="text-xs text-muted-foreground">Last action: {selectedMember.last_action.label}</p>
                                ) : null}
                            </div>
                            <div className="grid grid-cols-2 gap-2">
                                <Button asChild><Link href={`/workflow/${selected.id}/thread`}>Message</Link></Button>
                                <Button asChild variant="outline"><Link href={`/workflow/${selected.id}`}>Edit agent</Link></Button>
                            </div>
                            <Button asChild variant="outline" className="w-full"><Link href={`/workflow/${selected.id}/runs`}>View activity · {selected.total_runs ?? 0} runs</Link></Button>
                            <p className="text-sm text-muted-foreground">Open the editor to change instructions, skills, tools, knowledge or voice settings, and test the agent.</p>
                            <Button variant="ghost" className="w-full" onClick={() => { setSelectedId(null); setView('list'); }}>Manage status, groups and archive in List</Button>
                        </div>
                    )}
                </SheetContent>
            </Sheet>
            {facesOn && selected && (
                <AvatarCustomizer
                    key={selected.id}
                    workflowId={selected.id}
                    name={selected.name}
                    avatar={faceFor(selected)}
                    open={editingFace}
                    onOpenChange={setEditingFace}
                    onSaved={(avatar) => setFaces((current) => ({ ...current, [selected.id]: avatar }))}
                />
            )}
        </div>
    );
}

function AgentGroupedList({ workflows, folders }: AgentFolderViewProps) {
    // No folders → keep the original flat list (no folder chrome, nowhere to move to).
    if (folders.length === 0) {
        return <WorkflowTable workflows={workflows} showArchived={false} />;
    }

    // Group agents by folder. Agents whose folder_id is null — or points at a
    // folder we didn't get back — fall into "Uncategorized".
    const folderIds = new Set(folders.map((f) => f.id));
    const byFolder = new Map<number, WorkflowListResponse[]>();
    const uncategorized: WorkflowListResponse[] = [];

    for (const wf of workflows) {
        if (wf.folder_id != null && folderIds.has(wf.folder_id)) {
            const bucket = byFolder.get(wf.folder_id) ?? [];
            bucket.push(wf);
            byFolder.set(wf.folder_id, bucket);
        } else {
            uncategorized.push(wf);
        }
    }

    return (
        <div className="space-y-1">
            {folders.map((folder) => (
                <FolderSection
                    key={folder.id}
                    kind="folder"
                    folder={folder}
                    workflows={byFolder.get(folder.id) ?? []}
                    allFolders={folders}
                    defaultOpen={false}
                />
            ))}
            {uncategorized.length > 0 && (
                <FolderSection
                    kind="uncategorized"
                    workflows={uncategorized}
                    allFolders={folders}
                />
            )}
        </div>
    );
}
