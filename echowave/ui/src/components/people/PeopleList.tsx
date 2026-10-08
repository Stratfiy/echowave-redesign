"use client";

/**
 * People (PEOPLE.md): the person's own contacts, most recently in touch
 * first, with search, where they come from, possible duplicates, and cards
 * colleagues chose to show them. Only they can see their contacts.
 *
 * States, each distinct: loading, failed (with Retry), no contacts yet,
 * nothing matches the search, and a sync running (the list refreshes when
 * it finishes).
 */

import { Search, UserRound, Users } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    myMergesApiV1PeopleMergesGet,
    myPeopleApiV1PeopleGet,
    peopleStatusApiV1PeopleStatusGet,
    savePeopleSettingsApiV1PeopleSettingsPut,
} from "@/client/sdk.gen";
import type { MergeSuggestion, PeopleList as PeopleListData, PeopleStatus } from "@/client/types.gen";
import { EmptyState } from "@/components/EmptyState";
import { MergeReview } from "@/components/people/MergeReview";
import { PeopleSources } from "@/components/people/PeopleSources";
import { ErrorState } from "@/components/shell/ErrorState";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { ago } from "@/lib/people/format";

const POLL_MS = 3000;

export function PeopleList() {
    const { user, loading: authLoading } = useAuth();
    const [query, setQuery] = useState("");
    const [data, setData] = useState<PeopleListData | null>(null);
    const [status, setStatus] = useState<PeopleStatus | null>(null);
    const [merges, setMerges] = useState<MergeSuggestion[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [settingError, setSettingError] = useState<string | null>(null);

    const loadList = useCallback(async (q: string) => {
        const response = await myPeopleApiV1PeopleGet({ query: q ? { q } : {} });
        if (response.error || !response.data) {
            setError(detailFromResult(response, "Could not load your contacts"));
            return;
        }
        setError(null);
        setData(response.data);
    }, []);

    const loadStatus = useCallback(async () => {
        const [s, m] = await Promise.all([peopleStatusApiV1PeopleStatusGet(), myMergesApiV1PeopleMergesGet()]);
        if (s.error || !s.data) {
            setError(detailFromResult(s, "Could not load your contacts"));
            return;
        }
        setStatus(s.data);
        setMerges(m.data?.merges ?? []);
    }, []);

    const reload = useCallback(async () => {
        await Promise.all([loadList(query), loadStatus()]);
    }, [loadList, loadStatus, query]);

    const fetched = useRef(false);
    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void reload();
    }, [authLoading, user, reload]);

    // Search as the person types, a beat after they stop.
    const first = useRef(true);
    useEffect(() => {
        if (first.current) {
            first.current = false;
            return;
        }
        const timer = setTimeout(() => void loadList(query.trim()), 250);
        return () => clearTimeout(timer);
    }, [query, loadList]);

    // While a sync runs, ask again until it finishes, then refresh the list.
    const syncing = status?.providers.some((p) => p.state === "syncing") ?? false;
    useEffect(() => {
        if (!syncing) return;
        const timer = setInterval(async () => {
            const s = await peopleStatusApiV1PeopleStatusGet();
            if (s.data) {
                setStatus(s.data);
                if (!s.data.providers.some((p) => p.state === "syncing")) {
                    void loadList(query.trim());
                    const m = await myMergesApiV1PeopleMergesGet();
                    setMerges(m.data?.merges ?? []);
                }
            }
        }, POLL_MS);
        return () => clearInterval(timer);
    }, [syncing, loadList, query]);

    const setAgentsMayRead = async (value: boolean) => {
        setSettingError(null);
        const response = await savePeopleSettingsApiV1PeopleSettingsPut({
            body: { agents_may_read: value },
        });
        if (response.error) {
            setSettingError(detailFromResult(response, "Could not save that"));
            return;
        }
        setStatus((s) => (s ? { ...s, agents_may_read: value } : s));
    };

    const loading = data === null || status === null;
    const people = data?.people ?? [];
    const shared = data?.shared ?? [];

    return (
        <div className="mx-auto flex w-full max-w-[960px] flex-col gap-5 px-4 py-6 md:px-6">
            <div>
                <h1 className="text-2xl font-semibold leading-8">People</h1>
                <p className="text-sm text-muted-foreground">Your contacts, and what last happened with each. Only you can see these.</p>
            </div>

            {error && <ErrorState title="Could not load your contacts" description={error} onRetry={() => void reload()} />}

            {!error && loading && (
                <div aria-busy className="flex flex-col gap-2" data-testid="people-loading">
                    <Skeleton className="h-28 w-full" />
                    <Skeleton className="h-14 w-full" />
                    <Skeleton className="h-14 w-full" />
                </div>
            )}

            {!error && !loading && status && (
                <>
                    <PeopleSources providers={status.providers} onChanged={() => void reload()} />
                    {merges.length > 0 && <MergeReview merges={merges} onDecided={() => void reload()} />}

                    <label className="relative block">
                        <span className="sr-only">Search your contacts</span>
                        <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                        <Input
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="Search by name, company, number or email"
                            className="min-h-11 pl-9"
                            data-testid="people-search"
                        />
                    </label>

                    {people.length === 0 && !query.trim() && (
                        <EmptyState
                            icon={Users}
                            title="No contacts yet"
                            description="Connect Google or Outlook, import a file, or they will appear as Decibyl calls, messages and meets people for you."
                        />
                    )}
                    {people.length === 0 && query.trim() && (
                        <EmptyState
                            icon={Search}
                            title={`Nobody matches “${query.trim()}”`}
                            description="Try part of a name, a company, or the last digits of a number."
                        />
                    )}

                    {people.length > 0 && (
                        <section aria-label="Your contacts">
                            <p className="mb-2 text-xs text-muted-foreground">
                                {data?.total === people.length ? `${data?.total} contacts` : `${people.length} of ${data?.total} contacts`}
                            </p>
                            <ul className="flex flex-col divide-y divide-border rounded-[8px] border border-border" data-testid="people-list">
                                {people.map((p) => (
                                    <li key={p.id}>
                                        <Link href={`/people/${p.id}`} className="motion-m1 flex min-h-16 items-start gap-3 px-4 py-3 hover:bg-muted/50">
                                            <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-medium">
                                                {p.name.slice(0, 1).toUpperCase() || <UserRound aria-hidden className="h-4 w-4" />}
                                            </span>
                                            <span className="min-w-0 flex-1">
                                                <span className="flex flex-wrap items-baseline justify-between gap-x-2">
                                                    <span className="break-words font-medium">{p.name}</span>
                                                    {p.last_interaction_at && (
                                                        <span className="text-xs text-muted-foreground">{ago(p.last_interaction_at)}</span>
                                                    )}
                                                </span>
                                                {p.company && <span className="block text-sm text-muted-foreground">{p.company}</span>}
                                                {p.brief && <span className="line-clamp-2 block text-sm text-muted-foreground">{p.brief}</span>}
                                            </span>
                                        </Link>
                                    </li>
                                ))}
                            </ul>
                        </section>
                    )}

                    {shared.length > 0 && (
                        <section aria-label="Shared with you">
                            <h2 className="mb-2 text-sm font-semibold">Shared with you</h2>
                            <ul className="flex flex-col divide-y divide-border rounded-[8px] border border-border">
                                {shared.map((card) => (
                                    <li key={card.id} className="px-4 py-3 text-sm">
                                        <p className="font-medium">{card.name}</p>
                                        <p className="break-all text-muted-foreground">
                                            {[card.company, ...(card.phones ?? []), ...(card.emails ?? [])].filter(Boolean).join(" · ")}
                                        </p>
                                        {card.shared_by && <p className="text-xs text-muted-foreground">Shared by {card.shared_by}</p>}
                                    </li>
                                ))}
                            </ul>
                        </section>
                    )}

                    <section aria-label="Agents" className="flex items-start justify-between gap-4 rounded-[8px] border border-border px-4 py-3">
                        <div>
                            <p className="text-sm font-medium">Let Decibyl&apos;s agents read briefs</p>
                            <p className="text-sm text-muted-foreground">
                                When an agent calls or writes to one of your contacts for you, it may read that contact&apos;s brief. The brief then goes
                                with that call, so whoever can open the call&apos;s record can read it.
                            </p>
                            {settingError && (
                                <p role="alert" className="mt-1 text-sm text-destructive">
                                    {settingError}
                                </p>
                            )}
                        </div>
                        <Switch
                            checked={status.agents_may_read}
                            onCheckedChange={(v) => void setAgentsMayRead(v)}
                            aria-label="Let Decibyl's agents read briefs"
                            className="mt-1"
                        />
                    </section>
                </>
            )}
        </div>
    );
}

export default PeopleList;
