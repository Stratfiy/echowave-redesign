import { ArrowRightLeft } from 'lucide-react';
import Link from 'next/link';
import { Suspense } from 'react';

import { getWorkflowsApiV1WorkflowFetchGet, listFoldersApiV1FolderGet } from '@/client/sdk.gen';
import type { FolderResponse, WorkflowListResponse } from '@/client/types.gen';
import { Art3D } from '@/components/art/Art3D';
import { AgentsSectionTabs } from '@/components/evolve/AgentsSectionTabs';
import { PageBody, PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { CreateWorkflowButton } from "@/components/workflow/CreateWorkflowButton";
import { AgentFolderView } from '@/components/workflow/folders/AgentFolderView';
import { CreateFolderButton } from '@/components/workflow/folders/CreateFolderButton';
import { FolderSection } from '@/components/workflow/folders/FolderSection';
import { StartFromTemplate } from '@/components/workflow/StartFromTemplate';
import { UploadWorkflowButton } from '@/components/workflow/UploadWorkflowButton';
import { WorkflowTable } from '@/components/workflow/WorkflowTable';
import { getServerAccessToken, getServerAuthProvider } from '@/lib/auth/server';
import logger from '@/lib/logger';


export const dynamic = 'force-dynamic';

// Server component for workflow list. `archived` shows the agents that were
// put away instead (?show=archived): it was its own tab, /workflow/archived,
// and is a filter of this list now (UI-0).
async function WorkflowList({ archived }: { archived: boolean }) {
    const authProvider = await getServerAuthProvider();
    const accessToken = await getServerAccessToken();

    if (!accessToken) {
        // If no token, user needs to sign in
        const { redirect } = await import('next/navigation');
        if (authProvider === 'stack') {
            redirect('/');
        } else {
            // For OSS mode, this shouldn't happen as token is auto-generated
            return (
                <div className="text-red-500">
                    Authentication required. Please refresh the page.
                </div>
            );
        }
    }

    try {
        // Fetch both active and archived workflows in a single request
        const response = await getWorkflowsApiV1WorkflowFetchGet({
            headers: {
                'Authorization': `Bearer ${accessToken}`,
            },
            query: {
                status: 'active,archived'
            }
        });

        const allWorkflowData = response.data ? (Array.isArray(response.data) ? response.data : [response.data]) : [];

        // Squads apart from single agents: a different thing to build and
        // to test, and until now there was no screen that said they existed.
        const squads = allWorkflowData
            .filter((w: WorkflowListResponse) => w.status === 'active' && w.is_squad)
            .sort((a: WorkflowListResponse, b: WorkflowListResponse) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

        if (archived) return <ArchivedAgents workflows={allWorkflowData} />;

        // Separate active and archived workflows
        const activeWorkflows = allWorkflowData
            .filter((w: WorkflowListResponse) => w.status === 'active' && !w.is_squad)
            .sort((a: WorkflowListResponse, b: WorkflowListResponse) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

        const archivedWorkflows = allWorkflowData
            .filter((w: WorkflowListResponse) => w.status === 'archived')
            .sort((a: WorkflowListResponse, b: WorkflowListResponse) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

        // Fetch folders for grouping active agents. A failure here shouldn't
        // break the page — fall back to an empty list (flat, ungrouped view).
        let folders: FolderResponse[] = [];
        try {
            const foldersResponse = await listFoldersApiV1FolderGet({
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });
            folders = foldersResponse.data ?? [];
        } catch (folderErr) {
            logger.error(`Error fetching folders: ${folderErr}`);
        }

        return (
            <>
                {/* Active Workflows Section */}
                <div className="mb-8">
                    <h2 className="text-xl font-semibold mb-4">Working</h2>
                    {activeWorkflows.length > 0 || folders.length > 0 ? (
                        <AgentFolderView workflows={activeWorkflows} folders={folders} />
                    ) : (
                        <Card>
                            <CardContent className="p-8">
                                <Art3D name="rocket" size={88} className="mx-auto mb-3 block drop-shadow-md" />
                                <p className="text-center text-muted-foreground">
                                    No agents yet.
                                </p>
                                {/* The six ready-made agents, which existed in
                                    the API before this screen did and were
                                    never offered anywhere. */}
                                <StartFromTemplate />
                            </CardContent>
                        </Card>
                    )}
                </div>

                <div className="mb-8">
                    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                        <div>
                            <h2 className="text-xl font-semibold">Squads</h2>
                            <p className="text-sm text-muted-foreground">
                                One front desk that hands the call to the right agent.
                            </p>
                        </div>
                        <Button asChild variant="outline" size="sm">
                            <Link href="/workflow/squads/new">
                                <ArrowRightLeft className="h-4 w-4" />
                                New squad
                            </Link>
                        </Button>
                    </div>
                    {squads.length > 0 ? (
                        <WorkflowTable workflows={squads} showArchived={false} />
                    ) : (
                        <Card>
                            <CardContent className="p-6">
                                <p className="text-sm text-muted-foreground">
                                    No squads yet. Build two or more agents, then put a front desk
                                    in front of them.
                                </p>
                            </CardContent>
                        </Card>
                    )}
                </div>

                {/* A door to the archived ones, which a collapsed section
                    at the foot of this page used to hide. */}
                {archivedWorkflows.length > 0 && (
                    <p className="mb-8 text-sm text-muted-foreground">
                        {archivedWorkflows.length}{" "}
                        {archivedWorkflows.length === 1 ? "agent is" : "agents are"} archived.{" "}
                        <Link href="/workflow?show=archived" className="underline underline-offset-4 hover:text-foreground">
                            See them
                        </Link>
                        .
                    </p>
                )}
            </>
        );
    } catch (err) {
        logger.error(`Error fetching workflows: ${err}`);
        return (
            <div className="text-red-500">
                Failed to load Workflows. Please Try Again Later.
            </div>
        );
    }
}

/**
 * The agents that were put away, with what they were and a way back.
 * Somebody who archived an agent and could not find it again concluded it
 * was deleted; this says otherwise.
 */
function ArchivedAgents({ workflows }: { workflows: WorkflowListResponse[] }) {
    const archived = workflows
        .filter((w) => w.status === 'archived')
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    return (
        <div className="space-y-4">
            <Link href="/workflow" className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
                ← Working agents
            </Link>
            {archived.length === 0 ? (
                <Card>
                    <CardContent className="space-y-2 py-10 text-center">
                        <p className="text-sm">Nothing is archived.</p>
                        <p className="text-xs text-muted-foreground">
                            Archiving an agent stops it working and files it here. Nothing is deleted, and its
                            calls stay on the record.
                        </p>
                    </CardContent>
                </Card>
            ) : (
                <FolderSection kind="archived" workflows={archived} defaultOpen />
            )}
        </div>
    );
}

async function PageContent({ archived }: { archived: boolean }) {
    const workflowList = await WorkflowList({ archived });

    return <PageBody>{workflowList}</PageBody>;
}

function WorkflowsLoading() {
    // `PageBody`, not a container column: this stands in for the list only, and
    // a skeleton in a different well than the thing it replaces makes the page
    // jump sideways at the moment it loads.
    return (
        <PageBody>
            {/* Get Started Section Loading */}
            <div className="mb-12">
                <div className="h-8 w-48 bg-muted rounded mb-6"></div>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {Array.from({ length: 3 }, (_, i) => (
                        <Card key={i}>
                            <CardContent className="p-0">
                                <div className="h-40 bg-muted/70" />
                            </CardContent>
                        </Card>
                    ))}
                </div>
            </div>

            {/* Your Workflows Section Loading */}
            <div className="mb-6">
                <div className="flex justify-between items-center mb-6">
                    <div className="h-8 w-48 bg-muted rounded"></div>
                    <div className="h-10 w-32 bg-muted rounded"></div>
                </div>
                <Card>
                    <CardContent className="p-0">
                        <div className="h-96 bg-muted/70" />
                    </CardContent>
                </Card>
            </div>
        </PageBody>
    );
}

export default async function WorkflowPage({
    searchParams,
}: {
    searchParams: Promise<{ show?: string }>;
}) {
    const archived = (await searchParams).show === 'archived';
    return (
        <>
            <PageHeader
                title={archived ? 'Archived agents' : 'Your agents'}
                description={
                    archived
                        ? 'Archived agents take no calls and cost nothing. Restore one and it picks up where it was.'
                        : 'Each one does a job — on the phone, on WhatsApp, or on a schedule.'
                }
                actions={
                    <>
                        <UploadWorkflowButton />
                        <CreateFolderButton />
                        <CreateWorkflowButton />
                    </>
                }
            />
            {/* My agents · Skills, while evolve_skills is on; nothing otherwise. */}
            {!archived && <AgentsSectionTabs />}
            <Suspense fallback={<WorkflowsLoading />}>
                <PageContent archived={archived} />
            </Suspense>
        </>
    );
}
