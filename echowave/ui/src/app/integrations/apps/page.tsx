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

import { Search } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
    connectorActivityApiV1ConnectorsActivityGet,
    listConnectorsApiV1ConnectorsGet,
} from "@/client/sdk.gen";
import type {
    ConnectorActivity,
    ConnectorGroupResponse,
    ConnectorResponse,
} from "@/client/types.gen";
import { ConnectorCard } from "@/components/integrations/ConnectorCard";
import { GoogleCalendarConnect } from "@/components/integrations/GoogleCalendarConnect";
import { IntegrationsTabs } from "@/components/integrations/IntegrationsTabs";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

/** How the apps this account connected have actually behaved.
 *
 * The three questions an operator asks when an agent "stopped working" —
 * is it being called, is it failing, is it slow — and the three nobody could
 * answer before every action started being recorded. Shown above the
 * catalogue, because what you already use matters more than what you could.
 */
function ActivitySection() {
    const [apps, setApps] = useState<ConnectorActivity[] | null>(null);

    useEffect(() => {
        let cancelled = false;
        connectorActivityApiV1ConnectorsActivityGet()
            .then((response) => {
                if (!cancelled) setApps(response.data?.apps ?? []);
            })
            .catch(() => {
                if (!cancelled) setApps([]);
            });
        return () => {
            cancelled = true;
        };
    }, []);

    // Nothing rather than an empty card: an account that has never run an
    // action is not missing a report, it has not started yet.
    if (!apps || apps.length === 0) return null;

    return (
        <section className="space-y-3">
            <h2 className="text-sm font-medium text-muted-foreground">
                Last 30 days
            </h2>
            <Card>
                <CardContent className="divide-y py-2">
                    {apps.map((app) => (
                        <div
                            key={`${app.kind}:${app.app ?? "none"}`}
                            className="flex items-center justify-between py-2 text-sm"
                        >
                            <span>{app.app ?? app.kind}</span>
                            <span className="flex items-center gap-3 text-xs text-muted-foreground">
                                <span>{app.calls} used</span>
                                {app.errors > 0 ? (
                                    <span className="text-destructive">
                                        {app.errors} failed
                                    </span>
                                ) : null}
                                {app.avg_ms !== null && app.avg_ms !== undefined ? (
                                    <span>{app.avg_ms}ms</span>
                                ) : null}
                            </span>
                        </div>
                    ))}
                </CardContent>
            </Card>
        </section>
    );
}

export default function AppsPage() {
    const [popular, setPopular] = useState<ConnectorResponse[]>([]);
    const [groups, setGroups] = useState<ConnectorGroupResponse[]>([]);
    const [other, setOther] = useState<ConnectorResponse[]>([]);
    const [available, setAvailable] = useState(true);
    const [loading, setLoading] = useState(true);
    //: The screen could not tell "we have no apps for that" from "the
    //: catalogue did not load", and reported the second as the first. A
    //: failure shown as an empty result is a customer concluding we have
    //: nothing, and a 401 on our own vendor key looked exactly like a search
    //: that matched nothing for an hour today.
    const [failed, setFailed] = useState(false);
    const [query, setQuery] = useState("");

    const load = useCallback(async (search: string) => {
        setLoading(true);
        try {
            const response = await listConnectorsApiV1ConnectorsGet({
                query: search ? { q: search } : undefined,
            });
            // The generated client resolves rather than throws on a 4xx or
            // 5xx, so without this a backend failure fell through as
            // `available: false` and rendered "not switched on for this
            // deployment" -- a broken vendor call reported as a deployment
            // setting.
            if (response.error) {
                setFailed(true);
                setGroups([]);
                setPopular([]);
                setOther([]);
                return;
            }
            const fetched = response.data?.groups ?? [];
            const tail = response.data?.other ?? [];
            const configured = response.data?.available ?? false;
            // The catalogue is fifteen hundred rows. Configured, no error, no
            // search term and nothing back is not an empty catalogue -- it is
            // a vendor call that did not work.
            //
            // `other` counts here. A search whose only match is an
            // uncategorised app returns no groups, and judging emptiness on
            // groups alone would report the vendor as broken over a result we
            // actually have.
            setFailed(configured && !search && fetched.length === 0 && tail.length === 0);
            setGroups(fetched);
            setPopular(response.data?.popular ?? []);
            setOther(tail);
            setAvailable(configured);
        } catch {
            setFailed(true);
            setGroups([]);
            setPopular([]);
            setOther([]);
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

    // Everything reachable from this screen, `other` included. Counting only
    // the grouped apps would render "Nothing matches" over a search that did
    // match -- the same silent-absence shape that dropped the Other bucket
    // from the API in the first place.
    const total = useMemo(
        () =>
            groups.reduce((sum, group) => sum + group.connectors.length, 0) +
            other.length,
        [groups, other],
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

            <ActivitySection />

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

            {/* The apps a business asks for by name, across every category.
                A collection rather than a category, because the thing somebody
                wants is almost never the thing they would look for a *heading*
                for: nobody opens "Messaging" to find WhatsApp, they scan for
                WhatsApp. Hidden while searching -- a row of favourites above a
                filtered result is a second ranking arguing with the one the
                user typed. */}
            {available && !query.trim() && popular.length > 0 ? (
                <section className="space-y-3">
                    <h2 className="text-sm font-medium text-muted-foreground">
                        Most asked for
                    </h2>
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                        {popular.map((connector) => (
                            <ConnectorCard
                                key={`popular:${connector.slug}`}
                                connector={connector}
                                onConnected={() => void load(query.trim())}
                            />
                        ))}
                    </div>
                </section>
            ) : null}

            {!available ? (
                <Card>
                    <CardContent className="py-8 text-center text-sm text-muted-foreground">
                        Connecting outside apps is not switched on for this deployment.
                    </CardContent>
                </Card>
            ) : loading && groups.length === 0 ? (
                <p className="text-sm text-muted-foreground">Loading…</p>
            ) : failed ? (
                <Card>
                    <CardContent className="space-y-2 py-8 text-center">
                        <p className="text-sm">The app catalogue could not be loaded.</p>
                        <p className="text-xs text-muted-foreground">
                            This is us, not you — nothing is wrong with your account.
                        </p>
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={() => void load(query)}
                        >
                            Try again
                        </Button>
                    </CardContent>
                </Card>
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
                <>
                    {groups.map((group) => (
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
                    ))}

                    {/* The long tail, behind a door rather than at the bottom
                        of this one. These are the apps our categoriser could
                        not place -- hundreds of them, in no order a business
                        would recognise -- and ending the main screen on them
                        buries the categories above.

                        Not hidden, though: they were being dropped entirely
                        before, and an absence nobody can see is one nobody
                        reports. While a search is running they render here
                        instead, because somebody who typed a name wants the
                        match, not a link to go and look for it. */}
                    {other.length > 0 ? (
                        query.trim() ? (
                            <section className="space-y-3">
                                <h2 className="text-sm font-medium text-muted-foreground">
                                    Other
                                </h2>
                                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                                    {other.map((connector) => (
                                        <ConnectorCard
                                            key={connector.slug}
                                            connector={connector}
                                            onConnected={() => void load(query.trim())}
                                        />
                                    ))}
                                </div>
                            </section>
                        ) : (
                            <Card>
                                <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
                                    <div>
                                        <p className="text-sm font-medium">
                                            {other.length} more app
                                            {other.length === 1 ? "" : "s"}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            Everything that did not fit a category above.
                                            Searching covers these too.
                                        </p>
                                    </div>
                                    <Button asChild size="sm" variant="outline">
                                        <Link href="/integrations/apps/more">
                                            Browse all
                                        </Link>
                                    </Button>
                                </CardContent>
                            </Card>
                        )
                    ) : null}
                </>
            )}
        </div>
    );
}
