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
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { getWorkflowsApiV1WorkflowFetchGet } from "@/client/sdk.gen";
import { WidgetConfigurator } from "@/components/deploy/WidgetConfigurator";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

type Agent = { id: number; name: string };

function WebWidgetScreen() {
    const { user, redirectToLogin, loading: authLoading } = useAuth();
    const router = useRouter();
    const searchParams = useSearchParams();

    const [agents, setAgents] = useState<Agent[] | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [selectedId, setSelectedId] = useState<number | null>(null);

    useEffect(() => {
        if (!authLoading && !user) {
            redirectToLogin();
        }
    }, [authLoading, user, redirectToLogin]);

    const loadAgents = useCallback(async () => {
        try {
            const response = await getWorkflowsApiV1WorkflowFetchGet({
                query: { status: "active" },
            });
            const data = response.data
                ? Array.isArray(response.data)
                    ? response.data
                    : [response.data]
                : [];
            setAgents(
                data.map((w) => ({ id: w.id as number, name: w.name as string }))
            );
        } catch {
            // A widget screen that renders an empty picker on a network failure
            // reads as "you have no agents", which is a different and much more
            // alarming thing than "we could not reach the server".
            setLoadError("Could not load your agents. Reload to try again.");
            setAgents([]);
        }
    }, []);

    useEffect(() => {
        if (user) loadAgents();
    }, [user, loadAgents]);

    // ?agent= wins on first load, so the link from an agent's settings screen
    // lands on that agent rather than on whichever one happens to be first.
    const requested = searchParams.get("agent");
    useEffect(() => {
        if (!agents || agents.length === 0) return;
        setSelectedId((current) => {
            if (current !== null && agents.some((a) => a.id === current)) {
                return current;
            }
            const asked = requested ? Number(requested) : NaN;
            if (Number.isFinite(asked) && agents.some((a) => a.id === asked)) {
                return asked;
            }
            return agents[0].id;
        });
    }, [agents, requested]);

    const selected = useMemo(
        () => agents?.find((a) => a.id === selectedId) ?? null,
        [agents, selectedId]
    );

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
                        <Card>
                            <CardContent className="flex flex-wrap items-end gap-4 pt-6">
                                <div className="min-w-0 flex-1 space-y-2">
                                    <label
                                        htmlFor="widget-agent"
                                        className="text-sm font-medium"
                                    >
                                        Which agent answers
                                    </label>
                                    <Select
                                        value={selectedId ? String(selectedId) : undefined}
                                        onValueChange={(value) => {
                                            const next = Number(value);
                                            setSelectedId(next);
                                            // Keep the URL honest, so a reload
                                            // and a shared link both land on the
                                            // agent on screen.
                                            router.replace(
                                                `/deploy/web-widget?agent=${next}`,
                                                { scroll: false }
                                            );
                                        }}
                                    >
                                        <SelectTrigger id="widget-agent" className="max-w-md">
                                            <SelectValue placeholder="Choose an agent" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {agents.map((agent) => (
                                                <SelectItem
                                                    key={agent.id}
                                                    value={String(agent.id)}
                                                >
                                                    {agent.name}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                {selected && (
                                    <Button variant="outline" asChild>
                                        <Link href={`/workflow/${selected.id}`}>
                                            Edit this agent
                                        </Link>
                                    </Button>
                                )}
                            </CardContent>
                        </Card>

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
