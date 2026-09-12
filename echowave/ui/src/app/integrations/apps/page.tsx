"use client";

/**
 * The catalogue: everything Decibyl connects to, in one place.
 *
 * This is the first screen a business wants. The question they arrive with is
 * not "how do I build an agent" — it is "will it talk to the system I already
 * run on", and until this page existed the only way to answer it was to read
 * three settings screens and guess.
 *
 * It used to answer from a hand-written list of about forty apps. It now
 * answers from the live catalogue, which is fifteen hundred — so the honest
 * reply to "do you do Shopify" stopped depending on whether anybody had
 * remembered to add Shopify.
 *
 * **Every row says how hard it is to turn on, and that is the point.** A
 * catalogue where a one-click app and one we have not registered yet look
 * identical is the expensive kind of lie, because the business finds out after
 * choosing rather than before. Four states, worst case named plainly.
 */

import { Check, ExternalLink, Search } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
    listConnectorsApiV1ConnectorsGet,
    startConnectingApiV1ConnectorsSlugConnectPost,
} from "@/client/sdk.gen";
import type { ConnectorGroupResponse, ConnectorResponse } from "@/client/types.gen";
import { GoogleCalendarConnect } from "@/components/integrations/GoogleCalendarConnect";
import { IntegrationsTabs } from "@/components/integrations/IntegrationsTabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

/** What the badge says, in the words of the work the operator has to do. */
const SETUP_LABELS: Record<string, string> = {
    one_click: "One click",
    no_auth: "No setup",
    api_key: "Needs your key",
    needs_approval: "Ask us",
};

const SETUP_HINTS: Record<string, string> = {
    one_click: "Sign in and it is connected.",
    no_auth: "Nothing to connect.",
    api_key: "Paste a key from that app. We will add a screen for this next.",
    needs_approval:
        "We have to register with this provider before anyone can connect it. Tell us you want it and it moves up the list.",
};

function ConnectorCard({
    connector,
    onConnected,
}: {
    connector: ConnectorResponse;
    onConnected: () => void;
}) {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const connect = async () => {
        setBusy(true);
        setError(null);
        try {
            const response = await startConnectingApiV1ConnectorsSlugConnectPost({
                path: { slug: connector.slug },
            });
            const url = response.data?.connect_url;
            if (!url) {
                setError("Could not start connecting just now.");
                return;
            }
            // A new tab rather than a redirect: the operator is mid-way through
            // setting an agent up, and sending them to Google and back would
            // lose whatever else they had open on this screen.
            window.open(url, "_blank", "noopener");
            onConnected();
        } catch {
            setError("Could not start connecting just now.");
        } finally {
            setBusy(false);
        }
    };

    return (
        <Card className="flex flex-col">
            <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-2">
                    <CardTitle className="text-sm font-medium">{connector.name}</CardTitle>
                    {connector.connected ? (
                        <Badge variant="secondary" className="shrink-0 gap-1">
                            <Check className="h-3 w-3" />
                            Connected
                        </Badge>
                    ) : (
                        <Badge variant="outline" className="shrink-0">
                            {SETUP_LABELS[connector.setup] ?? connector.setup}
                        </Badge>
                    )}
                </div>
                {connector.description ? (
                    <CardDescription className="line-clamp-2 text-xs">
                        {connector.description}
                    </CardDescription>
                ) : null}
            </CardHeader>
            <CardContent className="mt-auto space-y-2 pt-0">
                {connector.connected ? (
                    <p className="text-xs text-muted-foreground">
                        Ready to use in an agent&rsquo;s tools and after-call steps.
                    </p>
                ) : (
                    <>
                        <p className="text-xs text-muted-foreground">
                            {SETUP_HINTS[connector.setup]}
                        </p>
                        {connector.setup === "one_click" ? (
                            <Button size="sm" onClick={connect} disabled={busy} className="w-full">
                                {busy ? "Opening…" : "Connect"}
                                <ExternalLink className="ml-1 h-3 w-3" />
                            </Button>
                        ) : null}
                        {error ? <p className="text-xs text-destructive">{error}</p> : null}
                    </>
                )}
            </CardContent>
        </Card>
    );
}

export default function AppsPage() {
    const [groups, setGroups] = useState<ConnectorGroupResponse[]>([]);
    const [available, setAvailable] = useState(true);
    const [loading, setLoading] = useState(true);
    const [query, setQuery] = useState("");

    const load = useCallback(async (search: string) => {
        setLoading(true);
        try {
            const response = await listConnectorsApiV1ConnectorsGet({
                query: search ? { q: search } : undefined,
            });
            setGroups(response.data?.groups ?? []);
            setAvailable(response.data?.available ?? false);
        } catch {
            setGroups([]);
        } finally {
            setLoading(false);
        }
    }, []);

    // Debounced, because the catalogue is fifteen hundred rows and a request
    // per keystroke would be one per letter of "googlesheets".
    useEffect(() => {
        const timer = setTimeout(() => void load(query.trim()), 250);
        return () => clearTimeout(timer);
    }, [query, load]);

    const total = useMemo(
        () => groups.reduce((sum, group) => sum + group.connectors.length, 0),
        [groups],
    );

    return (
        <div className="space-y-6 p-6">
            <IntegrationsTabs />

            <div>
                <h1 className="text-xl font-semibold">Apps</h1>
                <p className="text-sm text-muted-foreground">
                    What your agents can read from and write to. Connect one here and it
                    becomes available as a tool during a call and as a step after one.
                </p>
            </div>

            {/* Google Calendar first, and not as one of the cards below.
                It is the one integration we built ourselves: our OAuth
                application, our token table, no third party between the agent
                and the booking. Routing it through the connector platform
                would add a network hop to something we already own, so it gets
                its own place rather than a row in a list of fifteen hundred
                where nothing would say it is different. */}
            <section className="space-y-3">
                <h2 className="text-sm font-medium text-muted-foreground">Built in</h2>
                <GoogleCalendarConnect />
            </section>

            <div className="relative max-w-sm">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                    className="pl-8"
                    placeholder="Search 1,500+ apps…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                />
            </div>

            {!available ? (
                <Card>
                    <CardContent className="py-8 text-center text-sm text-muted-foreground">
                        Connecting outside apps is not switched on for this deployment.
                    </CardContent>
                </Card>
            ) : loading && groups.length === 0 ? (
                <p className="text-sm text-muted-foreground">Loading…</p>
            ) : total === 0 ? (
                <Card>
                    <CardContent className="space-y-2 py-8 text-center">
                        <p className="text-sm">Nothing matches &ldquo;{query}&rdquo;.</p>
                        <p className="text-xs text-muted-foreground">
                            If you need an app that is not here, tell us and we will look at
                            adding it &mdash; or build it as a{" "}
                            <Link href="/tools" className="underline">
                                custom tool
                            </Link>
                            .
                        </p>
                    </CardContent>
                </Card>
            ) : (
                groups.map((group) => (
                    <section key={group.group} className="space-y-3">
                        <h2 className="text-sm font-medium text-muted-foreground">
                            {group.group}
                        </h2>
                        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                            {group.connectors.map((connector) => (
                                <ConnectorCard
                                    key={connector.slug}
                                    connector={connector}
                                    onConnected={() => void load(query.trim())}
                                />
                            ))}
                        </div>
                    </section>
                ))
            )}
        </div>
    );
}
