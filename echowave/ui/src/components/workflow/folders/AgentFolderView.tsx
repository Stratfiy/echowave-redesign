'use client';

import Link from 'next/link';
import { useRef, useState } from 'react';

import type { FolderResponse, WorkflowListResponse } from '@/client/types.gen';
import { ArtImage } from '@/components/art/Art3D';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { jobArt } from '@/lib/art';
import { useFeature } from '@/lib/features';

import { AgentIdentityCard } from '../AgentIdentityCard';
import { WorkflowTable } from '../WorkflowTable';
import { FolderSection } from './FolderSection';

interface AgentFolderViewProps {
    /** Active (non-archived) agents only. */
    workflows: WorkflowListResponse[];
    folders: FolderResponse[];
}

/**
 * Agent cards open a profile; the list retains existing folder management.
 */
export function AgentFolderView({ workflows, folders }: AgentFolderViewProps) {
    const shell = useFeature('shell');
    const [view, setView] = useState<'cards' | 'list'>('cards');
    const [selectedId, setSelectedId] = useState<number | null>(null);
    const profileOpener = useRef<HTMLElement | null>(null);
    const listButton = useRef<HTMLButtonElement | null>(null);
    const selected = workflows.find((agent) => agent.id === selectedId);

    return (
        <div className="space-y-4">
            <div className="flex justify-end gap-1" role="group" aria-label="Agent directory view">
                <Button variant={view === 'cards' ? 'secondary' : 'ghost'} size="sm" aria-pressed={view === 'cards'} onClick={() => setView('cards')}>Cards</Button>
                <Button ref={listButton} variant={view === 'list' ? 'secondary' : 'ghost'} size="sm" aria-pressed={view === 'list'} onClick={() => setView('list')}>List</Button>
            </div>
            {view === 'cards' ? (
                <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                    {workflows.map((agent) => (
                        <AgentIdentityCard
                            key={agent.id}
                            ariaLabel={`View ${agent.name}`}
                            dataTestId={`agent-card-${agent.id}`}
                            label={agent.name}
                            avatar={shell ? <ArtImage name={jobArt(agent.name)} size={88} className="drop-shadow-sm" /> : undefined}
                            subtitle={agent.handle ? `@${agent.handle}` : `${agent.total_runs ?? 0} runs`}
                            statusBadge={<span className="text-xs text-muted-foreground">{agent.is_live === false ? 'Paused' : agent.is_live === true ? 'Enabled' : 'Status unavailable'}</span>}
                            onClick={(event) => { profileOpener.current = event.currentTarget; setSelectedId(agent.id); }}
                        />
                    ))}
                    {workflows.length === 0 && <p className="text-sm text-muted-foreground">No agents yet. Switch to List to manage your existing groups.</p>}
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
