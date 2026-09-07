"use client";

/**
 * The web widget, as a screen you can find.
 *
 * Everything on this page already existed: the token, the allowed domains, the
 * theme, the position, the button text and colour, and a generated script tag.
 * It lived in a modal behind a button on the last tab of one agent's settings,
 * so a customer had to already know it was there to find it. Bolna gives
 * deployment its own group in the sidebar and Vapi puts phone numbers at the
 * top level; shipping an agent is a different job from building one, and it
 * had no home here.
 *
 * The agent picker sits at the top because the widget belongs to an agent, and
 * arriving at "web widget" without having chosen one is the normal way in. The
 * settings screen still links here, with ?agent= set, so the old route through
 * the product lands in the same place.
 */

import { ExternalLink, Rocket } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

import { DeployAgentPicker } from "@/components/deploy/DeployAgentPicker";
import { useDeployAgents } from "@/components/deploy/useDeployAgents";
import { WidgetConfigurator } from "@/components/deploy/WidgetConfigurator";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

function WebWidgetScreen() {
    const { user, redirectToLogin, loading: authLoading } = useAuth();
    const router = useRouter();
    const searchParams = useSearchParams();

    const { agents, selected, setSelectedId, loadError, load } = useDeployAgents(
        searchParams.get("agent"),
    );

    useEffect(() => {
        if (!authLoading && !user) {
            redirectToLogin();
        }
    }, [authLoading, user, redirectToLogin]);

    useEffect(() => {
        if (user) load();
    }, [user, load]);

    if (authLoading || !user) {
        return (
            <PageBody>
                <div className="space-y-4">
                    <Skeleton className="h-12 w-64" />
                    <Skeleton className="h-64 w-full" />
                </div>
            </PageBody>
        );
    }

    return (
        <div>
            <PageHeader
                title="Web widget"
                description="Put a voice agent on your website. Visitors click and talk to it — no phone number involved."
                actions={
                    <Button variant="outline" asChild>
                        <a
                            href="https://docs.decibyl.ai/voice-agent/web-widget"
                            target="_blank"
                            rel="noopener noreferrer"
                        >
                            Docs
                            <ExternalLink className="ml-2 h-4 w-4" />
                        </a>
                    </Button>
                }
            />
            <PageBody className="space-y-6">
                {agents === null ? (
                    <Skeleton className="h-64 w-full" />
                ) : agents.length === 0 ? (
                    <Card>
                        <CardContent className="flex flex-col items-center gap-3 py-14 text-center">
                            <Rocket className="h-8 w-8 text-muted-foreground" />
                            <div>
                                <p className="font-medium">No agents yet</p>
                                <p className="mt-1 text-sm text-muted-foreground">
                                    {loadError ??
                                        "A widget puts one of your agents on your website, so there needs to be one first. It takes a couple of minutes."}
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
                            label="Which agent answers"
                            onSelect={(id) => {
                                setSelectedId(id);
                                // Keep the URL honest, so a reload and a shared
                                // link both land on the agent on screen.
                                router.replace(`/deploy/web-widget?agent=${id}`, {
                                    scroll: false,
                                });
                            }}
                        />

                        {selected && (
                            <WidgetConfigurator
                                // Remount on change: the configurator holds the
                                // saved settings in local state, and without a
                                // key switching agents would show the previous
                                // agent's colour and domains until the fetch
                                // returned.
                                key={selected.id}
                                workflowId={selected.id}
                                workflowName={selected.name}
                            />
                        )}
                    </>
                )}
            </PageBody>
        </div>
    );
}

export default function WebWidgetPage() {
    // useSearchParams needs a Suspense boundary, or the whole route opts out of
    // static rendering and the build says so.
    return (
        <Suspense
            fallback={
                <PageBody>
                    <Skeleton className="h-64 w-full" />
                </PageBody>
            }
        >
            <WebWidgetScreen />
        </Suspense>
    );
}
