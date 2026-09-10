import { ArrowRightLeft } from 'lucide-react';
import Link from 'next/link';
import { Suspense } from 'react';

import { getWorkflowsApiV1WorkflowFetchGet, listFoldersApiV1FolderGet } from '@/client/sdk.gen';
import type { FolderResponse, WorkflowListResponse } from '@/client/types.gen';
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

// Server component for workflow list
async function WorkflowList() {
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
                    <h2 className="text-xl font-semibold mb-4">Active Agents</h2>
                    {activeWorkflows.length > 0 || folders.length > 0 ? (
                        <AgentFolderView workflows={activeWorkflows} folders={folders} />
                    ) : (
                        <Card>
                            <CardContent className="p-8">
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

                {/* Archived Section — collapsible, same design as the folder/Uncategorized sections */}
                {archivedWorkflows.length > 0 && (
                    <div className="mb-8">
                        <FolderSection kind="archived" workflows={archivedWorkflows} />
                    </div>
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

async function PageContent() {
    const workflowList = await WorkflowList();

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

export default function WorkflowPage() {
    return (
        <>
            <PageHeader
                title="Voice agents"
                description="Design a conversation, publish it, and point a number at it."
                actions={
                    <>
                        <UploadWorkflowButton />
                        <CreateFolderButton />
                        <CreateWorkflowButton />
                    </>
                }
            />
            <Suspense fallback={<WorkflowsLoading />}>
                <PageContent />
            </Suspense>
        </>
    );
}
