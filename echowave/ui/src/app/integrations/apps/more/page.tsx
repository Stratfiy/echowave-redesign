"use client";

/**
 * The long tail: every app our categoriser could not place on a shelf.
 *
 * It has its own page for one reason -- there are hundreds of them, in no
 * order a business would recognise, and putting them at the bottom of the main
 * catalogue buries the categories above. A screen that ends in nine hundred
 * unsorted rows is a screen nobody scrolls.
 *
 * It exists at all because these were being dropped. The catalogue carefully
 * filed an unmatched app under `Other`; the route then rebuilt its response by
 * walking the known groups, and `Other` is not one of them, so every app the
 * categoriser could not place was assigned correctly and lost on the way out
 * -- with the count above it still including them. Nothing errored. The only
 * way anyone would have found out is a customer asking where their app went.
 *
 * So: visible, searchable, and on a page of its own rather than nowhere.
 */

import { ArrowLeft, Search } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { listConnectorsApiV1ConnectorsGet } from "@/client/sdk.gen";
import type { ConnectorResponse } from "@/client/types.gen";
import { ConnectorCard } from "@/components/integrations/ConnectorCard";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth";

export default function MoreAppsPage() {
    const { user, loading: authLoading } = useAuth();

    const [connectors, setConnectors] = useState<ConnectorResponse[]>([]);
    const [query, setQuery] = useState("");
    const [loading, setLoading] = useState(true);
    const [failed, setFailed] = useState(false);

    const load = useCallback(async (search: string) => {
        setLoading(true);
        try {
            const response = await listConnectorsApiV1ConnectorsGet({
                query: search ? { q: search } : undefined,
            });
            // The generated client resolves rather than throws on a 4xx, so a
            // failure only surfaces if the error is checked. Reporting it as an
            // empty list would read as "we connect to nothing".
            if (response.error) {
                setFailed(true);
                setConnectors([]);
                return;
            }
            setFailed(false);
            setConnectors(response.data?.other ?? []);
        } catch {
            setFailed(true);
            setConnectors([]);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        // The auth interceptor only attaches the bearer token once auth has
        // loaded; fetching earlier sends an unauthenticated request.
        if (authLoading || !user) return;
        const timer = setTimeout(() => void load(query.trim()), 250);
        return () => clearTimeout(timer);
    }, [authLoading, user, query, load]);

    return (
        <div className="space-y-6 p-6">
            <Button asChild size="sm" variant="ghost" className="-ml-2">
                <Link href="/integrations/apps">
                    <ArrowLeft className="mr-1 h-4 w-4" />
                    Apps
                </Link>
            </Button>

            <div>
                <h1 className="text-xl font-semibold">More apps</h1>
                <p className="text-sm text-muted-foreground">
                    Everything that did not fit one of the categories. Connecting one
                    works exactly the same way.
                </p>
            </div>

            <div className="relative max-w-sm">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                    className="pl-8"
                    placeholder="Search these apps…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                />
            </div>

            {loading && connectors.length === 0 ? (
                <p className="text-sm text-muted-foreground">Loading…</p>
            ) : failed ? (
                <Card>
                    <CardContent className="space-y-2 py-8 text-center">
                        <p className="text-sm">The app catalogue could not be loaded.</p>
                        <p className="text-xs text-muted-foreground">
                            This is us, not you &mdash; nothing is wrong with your account.
                        </p>
                        <Button size="sm" variant="outline" onClick={() => void load(query)}>
                            Try again
                        </Button>
                    </CardContent>
                </Card>
            ) : connectors.length === 0 ? (
                <Card>
                    <CardContent className="space-y-2 py-8 text-center">
                        <p className="text-sm">
                            {query.trim()
                                ? `Nothing here matches “${query.trim()}”.`
                                : "Nothing is in this bucket right now."}
                        </p>
                        <p className="text-xs text-muted-foreground">
                            The categorised apps are on the{" "}
                            <Link href="/integrations/apps" className="underline">
                                main catalogue
                            </Link>
                            .
                        </p>
                    </CardContent>
                </Card>
            ) : (
                <>
                    <p className="text-sm text-muted-foreground">
                        {connectors.length} app{connectors.length === 1 ? "" : "s"}
                    </p>
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                        {connectors.map((connector) => (
                            <ConnectorCard
                                key={connector.slug}
                                connector={connector}
                                onConnected={() => void load(query.trim())}
                            />
                        ))}
                    </div>
                </>
            )}
        </div>
    );
}
