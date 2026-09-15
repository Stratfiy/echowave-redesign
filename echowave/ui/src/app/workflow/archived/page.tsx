/**
 * The bots that were put away.
 *
 * Archiving had a button and no destination: the archived list was a
 * collapsed section at the foot of a long page, which is the same thing as
 * not existing to anybody who did not already know it was there. Somebody
 * archived a bot, went looking for it, and concluded it was deleted.
 *
 * Its own tab, open, with what it was and a way back.
 */

import Link from "next/link";
import { Suspense } from "react";

import { getWorkflowsApiV1WorkflowFetchGet } from "@/client/sdk.gen";
import type { WorkflowListResponse } from "@/client/types.gen";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { BOTS_TABS } from "@/components/layout/SectionTabs";
import { Card, CardContent } from "@/components/ui/card";
import { FolderSection } from "@/components/workflow/folders/FolderSection";
import { getServerAccessToken } from "@/lib/auth/server";
import logger from "@/lib/logger";

export const dynamic = "force-dynamic";

async function ArchivedList() {
    const accessToken = await getServerAccessToken();
    if (!accessToken) {
        return (
            <p className="text-sm text-muted-foreground">
                Authentication required. Please refresh the page.
            </p>
        );
    }

    try {
        const response = await getWorkflowsApiV1WorkflowFetchGet({
            headers: { Authorization: `Bearer ${accessToken}` },
            query: { status: "archived" },
        });
        const rows: WorkflowListResponse[] = response.data
            ? Array.isArray(response.data)
                ? response.data
                : [response.data]
            : [];
        const archived = rows
            .filter((w) => w.status === "archived")
            .sort(
                (a, b) =>
                    new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
            );

        if (archived.length === 0) {
            return (
                <Card>
                    <CardContent className="space-y-2 py-10 text-center">
                        <p className="text-sm">Nothing is archived.</p>
                        <p className="text-xs text-muted-foreground">
                            Archiving a bot from{" "}
                            <Link href="/workflow" className="underline underline-offset-4">
                                Your bots
                            </Link>{" "}
                            stops it working and files it here. Nothing is deleted, and its
                            calls stay on the record.
                        </p>
                    </CardContent>
                </Card>
            );
        }

        return <FolderSection kind="archived" workflows={archived} defaultOpen />;
    } catch (err) {
        logger.error(`Error fetching archived workflows: ${err}`);
        return (
            <p className="text-sm text-destructive">
                The archived bots could not be loaded. This is us, not you.
            </p>
        );
    }
}

export default function ArchivedWorkflowsPage() {
    return (
        <>
            <PageHeader
                title="Your bots"
                description="Archived bots take no calls and cost nothing. Restore one and it picks up where it was."
                tabs={BOTS_TABS}
            />
            <Suspense
                fallback={
                    <PageBody>
                        <div className="h-40 animate-pulse rounded-xl bg-muted" />
                    </PageBody>
                }
            >
                <PageBody>
                    <ArchivedList />
                </PageBody>
            </Suspense>
        </>
    );
}
