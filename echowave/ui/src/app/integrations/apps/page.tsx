"use client";

/**
 * The catalogue: everything Decibyl connects to, in one place.
 *
 * This is the first screen a business wants and the last one we had. The
 * question they arrive with is not "how do I build an agent" — it is "will it
 * talk to the system I already run on", and until this page existed the only
 * way to answer it was to read three settings screens and guess.
 *
 * Every card either goes somewhere real in this product or says it is not
 * built. There is deliberately no "coming soon" that links nowhere: a
 * catalogue of twenty logos where three of them work is the expensive kind of
 * lie, because the business finds out after signing rather than before.
 */

import { ArrowRight, Search } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { IntegrationsTabs } from "@/components/integrations/IntegrationsTabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
    catalogueByCategory,
    type CatalogueEntry,
    CONNECT_HINTS,
    CONNECT_LABELS,
    searchCatalogue,
} from "@/constants/integrationCatalogue";
import { SETUP_CALL_URL } from "@/constants/setupCall";

function EntryCard({ entry }: { entry: CatalogueEntry }) {
    const requested = entry.connect === "request";

    return (
        <Card className="flex flex-col">
            <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-2">
                    <CardTitle className="text-base">{entry.name}</CardTitle>
                    {requested && (
                        <Badge variant="outline" className="shrink-0 text-xs font-normal">
                            Not built yet
                        </Badge>
                    )}
                </div>
                <CardDescription>{entry.blurb}</CardDescription>
            </CardHeader>
            <CardContent className="mt-auto space-y-2">
                <p className="text-xs text-muted-foreground">
                    {CONNECT_HINTS[entry.connect]}
                </p>
                {requested ? (
                    // The setup call, not a form that files an email nobody
                    // reads. Both ask for the same thing and only one of them
                    // ends with somebody's agent connected.
                    <Button asChild variant="outline" size="sm" className="w-full">
                        <a href={SETUP_CALL_URL} target="_blank" rel="noopener noreferrer">
                            {CONNECT_LABELS[entry.connect]}
                        </a>
                    </Button>
                ) : (
                    <Button asChild variant="secondary" size="sm" className="w-full">
                        <Link href={entry.href!}>
                            {CONNECT_LABELS[entry.connect]}
                            <ArrowRight className="ml-1 h-3.5 w-3.5" />
                        </Link>
                    </Button>
                )}
            </CardContent>
        </Card>
    );
}

export default function IntegrationAppsPage() {
    const [query, setQuery] = useState("");
    const groups = useMemo(
        () => catalogueByCategory(searchCatalogue(query)),
        [query],
    );

    return (
        <>
            <IntegrationsTabs />
            <div className="container mx-auto max-w-5xl space-y-8 px-4 py-8">
                <div>
                    <h1 className="text-[26px] leading-tight">Integrations</h1>
                    <p className="mt-2 text-sm text-muted-foreground">
                        What an agent can reach. Most of these are a webhook and a few
                        minutes &mdash; no developer, no agency. Anything marked
                        &ldquo;not built yet&rdquo; we will wire with you on a setup
                        call.
                    </p>
                </div>

                <div className="relative max-w-sm">
                    <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="Search apps"
                        className="pl-8"
                        aria-label="Search apps"
                    />
                </div>

                {groups.length === 0 ? (
                    // An empty search is not a dead end: the thing they typed is
                    // very likely doable, it just is not on a card.
                    <div className="rounded-lg border border-dashed p-8 text-center">
                        <p className="text-sm text-muted-foreground">
                            Nothing here matches &ldquo;{query}&rdquo;. Most systems
                            connect over the webhook even without a card.
                        </p>
                        <Button asChild variant="outline" size="sm" className="mt-4">
                            <a href={SETUP_CALL_URL} target="_blank" rel="noopener noreferrer">
                                Ask us about it
                            </a>
                        </Button>
                    </div>
                ) : (
                    groups.map((group) => (
                        <section key={group.category} className="space-y-3">
                            <h2 className="text-sm font-semibold text-muted-foreground">
                                {group.category}
                            </h2>
                            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                                {group.entries.map((entry) => (
                                    <EntryCard key={entry.id} entry={entry} />
                                ))}
                            </div>
                        </section>
                    ))
                )}
            </div>
        </>
    );
}
