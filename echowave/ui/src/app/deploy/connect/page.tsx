"use client";

/**
 * Connecting an agent to everything else the business already runs.
 *
 * Nothing on this screen is a new capability. The trigger endpoint, the
 * retrying outbound webhook, custom tools and the MCP server all shipped
 * months ago, and none of them were mentioned anywhere a customer looks — so
 * "can it talk to my CRM, my ads, my forms" was being answered as no, by
 * silence. This is the screen that answers it.
 *
 * Its own route rather than a card on the widget screen: putting an agent on
 * your website and wiring it into your CRM are different jobs, and a page
 * titled "Web widget" that also explains Meta lead ads is a page nobody finds
 * either thing on.
 */

import { ExternalLink, Workflow } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

import { ConnectRecipes } from "@/components/deploy/ConnectRecipes";
import { DeployAgentPicker } from "@/components/deploy/DeployAgentPicker";
import { useDeployAgents } from "@/components/deploy/useDeployAgents";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

function ConnectScreen() {
    const { user, redirectToLogin, loading: authLoading } = useAuth();
    const router = useRouter();
    const searchParams = useSearchParams();
    const { agents, selected, setSelectedId, loadError, load } = useDeployAgents(
        searchParams.get("agent"),
    );

    useEffect(() => {
        if (!authLoading && !user) redirectToLogin();
    }, [authLoading, user, redirectToLogin]);

    useEffect(() => {
        if (user) load();
    }, [user, load]);

    if (authLoading || !user) {
        return (
            <PageBody>
                <Skeleton className="h-64 w-full" />
            </PageBody>
        );
    }

    return (
        <div>
            <PageHeader
                title="Connect"
                description="Make something else start a call — a lead form, a CRM, a spreadsheet — and get the result back when it ends."
                actions={
                    <Button variant="outline" asChild>
                        <Link href="/api-keys">
                            API keys
                            <ExternalLink className="ml-2 h-4 w-4" />
                        </Link>
                    </Button>
                }
            />
            <PageBody className="space-y-6">
                {agents === null ? (
                    <Skeleton className="h-64 w-full" />
                ) : agents.length === 0 ? (
                    <Card>
                        <CardContent className="flex flex-col items-center gap-3 py-14 text-center">
                            <Workflow className="h-8 w-8 text-muted-foreground" />
                            <div>
                                <p className="font-medium">No agents yet</p>
                                <p className="mt-1 text-sm text-muted-foreground">
                                    {loadError ??
                                        "There needs to be an agent before anything can trigger one."}
                                </p>
                            </div>
                            {!loadError && (
                                <Button asChild className="mt-1">
                                    <Link href="/workflow/create">Create an agent</Link>
                                </Button>
                            )}
                        </CardContent>
                    </Card>
                ) : (
                    <>
                        <DeployAgentPicker
                            agents={agents}
                            selected={selected}
                            label="Which agent should the trigger call"
                            onSelect={(id) => {
                                setSelectedId(id);
                                router.replace(`/deploy/connect?agent=${id}`, {
                                    scroll: false,
                                });
                            }}
                        />
                        {selected && <ConnectRecipes agentUuid={selected.uuid} />}
                    </>
                )}
            </PageBody>
        </div>
    );
}

export default function ConnectPage() {
    // useSearchParams needs a Suspense boundary or the route opts out of
    // static rendering and the build says so.
    return (
        <Suspense
            fallback={
                <PageBody>
                    <Skeleton className="h-64 w-full" />
                </PageBody>
            }
        >
            <ConnectScreen />
        </Suspense>
    );
}
