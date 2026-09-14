"use client";

/**
 * The marketplace: two shelves, bots and tools, under one search box.
 *
 * Modelled on the Slack Marketplace. A hero that says what the shelf is
 * for, a search, a row of category cards with a coloured tile each, then
 * the rows themselves. Bots are filed by industry and by function, both
 * from the template catalogue; tools by the connector catalogue's groups.
 *
 * Both shelves come from endpoints the product already had. This screen
 * adds no data of its own, only the shop front, so a bot or a tool that
 * exists is here and one that does not is not -- the marketplace cannot
 * advertise what the catalogue does not carry.
 */

import { ArrowRight, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { client } from "@/client/client.gen";
import { listConnectorsApiV1ConnectorsGet } from "@/client/sdk.gen";
import type { ConnectorGroupResponse, ConnectorResponse } from "@/client/types.gen";
import { ConnectorCard } from "@/components/integrations/ConnectorCard";
import { PageBody, PageHeader, type PageTab } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth";
import {
    type BotTemplate,
    DIRECTION_LABELS,
    filterBots,
    functionOf,
    functions,
    hireHref,
    industries,
    industryIcon,
    industryOf,
    type Shelf,
    toneFor,
    toolIcon,
} from "@/lib/marketplace";
import { cn } from "@/lib/utils";

export type ShelfKind = "bots" | "tools";

const TABS: PageTab[] = [
    { href: "/marketplace", label: "Bots" },
    { href: "/marketplace/tools", label: "Tools", prefix: true },
];

/** How many rows a category shows before "See all". */
const ROWS_PER_GROUP = 6;

function Hero({ kind }: { kind: ShelfKind }) {
    return (
        <div className="rounded-2xl bg-sidebar px-6 py-8 sm:px-10 sm:py-10">
            <p className="text-xs font-semibold uppercase tracking-wider text-brand-blue">
                Decibyl Marketplace
            </p>
            <h2 className="mt-2 max-w-xl text-2xl font-semibold leading-tight tracking-tight sm:text-3xl">
                {kind === "bots"
                    ? "A bot for every job, ready the day you hire it."
                    : "Every system you already run, in your bots' hands."}
            </h2>
            <p className="mt-2 max-w-xl text-sm text-muted-foreground">
                {kind === "bots"
                    ? "Pick one for your industry or for the job, hear it on a call, then put it on a number."
                    : "Connect the apps your business lives in and every bot can read from and write to them."}
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
                <Button asChild variant={kind === "bots" ? "default" : "outline"}>
                    <Link href="/marketplace">Explore bots</Link>
                </Button>
                <Button asChild variant={kind === "tools" ? "default" : "outline"}>
                    <Link href="/marketplace/tools">Browse tools</Link>
                </Button>
            </div>
        </div>
    );
}

/** A category tile: coloured icon, name, count. Pressing it filters. */
function CategoryCard({
    name,
    count,
    noun,
    icon: Icon,
    selected,
    onSelect,
}: {
    name: string;
    count: number;
    noun: string;
    icon: React.ComponentType<{ className?: string }>;
    selected: boolean;
    onSelect: () => void;
}) {
    return (
        <button
            type="button"
            aria-pressed={selected}
            onClick={onSelect}
            className={cn(
                "flex items-center gap-3 rounded-xl border bg-card p-3 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                selected ? "border-brand-blue ring-1 ring-brand-blue" : "border-border",
            )}
        >
            <span
                className={cn(
                    "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
                    toneFor(name),
                )}
            >
                <Icon className="h-5 w-5" />
            </span>
            <span className="min-w-0">
                <span className="block truncate text-sm font-medium">{name}</span>
                <span className="block text-xs text-muted-foreground">
                    {count} {count === 1 ? noun : `${noun}s`}
                </span>
            </span>
        </button>
    );
}

function Chip({ name, tone }: { name: string; tone?: string }) {
    return (
        <span
            className={cn(
                "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
                tone ?? "bg-muted text-muted-foreground",
            )}
        >
            {name}
        </span>
    );
}

function BotCard({ template }: { template: BotTemplate }) {
    const industry = industryOf(template);
    const fn = functionOf(template);
    const Icon = industryIcon(industry);
    return (
        <Card className="flex h-full flex-col">
            <CardContent className="flex flex-1 flex-col gap-3 p-4">
                <div className="flex items-start gap-3">
                    <span
                        className={cn(
                            "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
                            toneFor(industry),
                        )}
                    >
                        <Icon className="h-5 w-5" />
                    </span>
                    <div className="min-w-0">
                        <h3 className="truncate text-sm font-semibold">{template.name}</h3>
                        <p className="text-xs text-muted-foreground">
                            {DIRECTION_LABELS[template.direction] ?? template.direction}
                            {template.languages.length > 0
                                ? ` · ${template.languages.slice(0, 3).join(", ")}`
                                : ""}
                        </p>
                    </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                    <Chip name={industry} tone={toneFor(industry)} />
                    <Chip name={fn} />
                </div>
                <p className="line-clamp-3 text-xs text-muted-foreground">{template.summary}</p>
                <div className="mt-auto pt-1">
                    <Button asChild size="sm" className="w-full">
                        <Link href={hireHref(template.id)}>
                            Hire {template.name}
                            <ArrowRight className="ml-1 h-3 w-3" />
                        </Link>
                    </Button>
                </div>
            </CardContent>
        </Card>
    );
}

function SectionTitle({ children, action }: { children: React.ReactNode; action?: React.ReactNode }) {
    return (
        <div className="flex items-center justify-between gap-3">
            <h2 className="text-base font-semibold tracking-tight">{children}</h2>
            {action}
        </div>
    );
}

function BotsShelf({ query }: { query: string }) {
    const { user, loading: authLoading } = useAuth();
    const [templates, setTemplates] = useState<BotTemplate[] | null>(null);
    const [failed, setFailed] = useState(false);
    const [industry, setIndustry] = useState<string | null>(null);
    const [fn, setFn] = useState<string | null>(null);

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;
        void (async () => {
            try {
                const response = await client.get({ url: "/api/v1/agent-templates" });
                if (cancelled) return;
                if (response.error) {
                    setFailed(true);
                    setTemplates([]);
                    return;
                }
                const data = (response.data as { templates?: BotTemplate[] } | undefined)?.templates ?? [];
                setTemplates(data);
            } catch {
                if (!cancelled) {
                    setFailed(true);
                    setTemplates([]);
                }
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [authLoading, user]);

    const all = useMemo(() => templates ?? [], [templates]);
    const byIndustry: Shelf[] = useMemo(() => industries(all), [all]);
    const byFunction: Shelf[] = useMemo(() => functions(all), [all]);
    const shown = useMemo(() => filterBots(all, { query, industry, fn }), [all, query, industry, fn]);

    if (templates === null) return <p className="text-sm text-muted-foreground">Loading…</p>;
    if (failed) {
        return (
            <Card>
                <CardContent className="py-8 text-center text-sm text-muted-foreground">
                    The bot shelf could not be loaded. This is us, not you.
                </CardContent>
            </Card>
        );
    }

    return (
        <>
            <section className="space-y-3">
                <SectionTitle>By industry</SectionTitle>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                    {byIndustry.map((shelf) => (
                        <CategoryCard
                            key={shelf.name}
                            name={shelf.name}
                            count={shelf.count}
                            noun="bot"
                            icon={industryIcon(shelf.name)}
                            selected={industry === shelf.name}
                            onSelect={() => setIndustry((cur) => (cur === shelf.name ? null : shelf.name))}
                        />
                    ))}
                </div>
            </section>

            <section className="space-y-3">
                <SectionTitle>By function</SectionTitle>
                <div className="flex flex-wrap gap-2">
                    {byFunction.map((shelf) => {
                        const selected = fn === shelf.name;
                        return (
                            <button
                                key={shelf.name}
                                type="button"
                                aria-pressed={selected}
                                onClick={() => setFn((cur) => (cur === shelf.name ? null : shelf.name))}
                                className={cn(
                                    "rounded-full border px-3 py-1.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                                    selected
                                        ? "border-brand-blue bg-brand-blue-soft text-brand-blue"
                                        : "border-border bg-muted/30 text-muted-foreground hover:bg-muted hover:text-foreground",
                                )}
                            >
                                {shelf.name} · {shelf.count}
                            </button>
                        );
                    })}
                </div>
            </section>

            <section className="space-y-3">
                <SectionTitle>
                    {industry || fn
                        ? [industry, fn].filter(Boolean).join(" · ")
                        : query.trim()
                          ? `Bots matching “${query.trim()}”`
                          : "All bots"}
                </SectionTitle>
                {shown.length === 0 ? (
                    <Card>
                        <CardContent className="space-y-2 py-8 text-center">
                            <p className="text-sm">No bot on this shelf yet.</p>
                            <p className="text-xs text-muted-foreground">
                                Describe the job on{" "}
                                <Link href="/overview" className="underline">
                                    Home
                                </Link>{" "}
                                and Decibyl will build one.
                            </p>
                        </CardContent>
                    </Card>
                ) : (
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                        {shown.map((template) => (
                            <BotCard key={template.id} template={template} />
                        ))}
                    </div>
                )}
            </section>
        </>
    );
}

function ToolsShelf({ query }: { query: string }) {
    const [available, setAvailable] = useState(true);
    const [loading, setLoading] = useState(true);
    const [failed, setFailed] = useState(false);
    const [popular, setPopular] = useState<ConnectorResponse[]>([]);
    const [groups, setGroups] = useState<ConnectorGroupResponse[]>([]);
    const [otherCount, setOtherCount] = useState(0);
    const [group, setGroup] = useState<string | null>(null);
    const [expanded, setExpanded] = useState<string[]>([]);

    useEffect(() => {
        let cancelled = false;
        const timer = setTimeout(() => {
            void (async () => {
                setLoading(true);
                setFailed(false);
                try {
                    const response = await listConnectorsApiV1ConnectorsGet({
                        query: { q: query.trim() },
                    });
                    if (cancelled) return;
                    if (response.error || !response.data) {
                        setFailed(true);
                        return;
                    }
                    setAvailable(response.data.available);
                    setPopular(response.data.popular ?? []);
                    setGroups(response.data.groups ?? []);
                    setOtherCount(response.data.other?.length ?? 0);
                } catch {
                    if (!cancelled) setFailed(true);
                } finally {
                    if (!cancelled) setLoading(false);
                }
            })();
        }, 250);
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [query]);

    const reload = () => setExpanded((e) => [...e]);
    const visible = group ? groups.filter((g) => g.group === group) : groups;

    if (!available) {
        return (
            <Card>
                <CardContent className="py-8 text-center text-sm text-muted-foreground">
                    Connecting outside apps is not switched on for this deployment.
                </CardContent>
            </Card>
        );
    }
    if (failed) {
        return (
            <Card>
                <CardContent className="py-8 text-center text-sm text-muted-foreground">
                    The tool shelf could not be loaded. This is us, not you.
                </CardContent>
            </Card>
        );
    }

    return (
        <>
            {!query.trim() ? (
                <section className="space-y-3">
                    <SectionTitle>Categories</SectionTitle>
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                        {groups.map((g) => (
                            <CategoryCard
                                key={g.group}
                                name={g.group}
                                count={g.connectors.length}
                                noun="tool"
                                icon={toolIcon(g.group)}
                                selected={group === g.group}
                                onSelect={() => setGroup((cur) => (cur === g.group ? null : g.group))}
                            />
                        ))}
                    </div>
                </section>
            ) : null}

            {!query.trim() && !group && popular.length > 0 ? (
                <section className="space-y-3">
                    <SectionTitle>Most asked for</SectionTitle>
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                        {popular.map((connector) => (
                            <ConnectorCard
                                key={`popular:${connector.slug}`}
                                connector={connector}
                                onConnected={reload}
                            />
                        ))}
                    </div>
                </section>
            ) : null}

            {loading && groups.length === 0 ? (
                <p className="text-sm text-muted-foreground">Loading…</p>
            ) : visible.length === 0 ? (
                <Card>
                    <CardContent className="space-y-2 py-8 text-center">
                        <p className="text-sm">Nothing matches &ldquo;{query.trim()}&rdquo;.</p>
                        <p className="text-xs text-muted-foreground">
                            Build it as a{" "}
                            <Link href="/tools" className="underline">
                                custom tool
                            </Link>
                            , or tell us and we will look at adding it.
                        </p>
                    </CardContent>
                </Card>
            ) : (
                visible.map((g) => {
                    const open = Boolean(group) || Boolean(query.trim()) || expanded.includes(g.group);
                    const rows = open ? g.connectors : g.connectors.slice(0, ROWS_PER_GROUP);
                    const hidden = g.connectors.length - rows.length;
                    return (
                        <section key={g.group} className="space-y-3">
                            <SectionTitle
                                action={
                                    hidden > 0 ? (
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            onClick={() => setExpanded((e) => [...e, g.group])}
                                        >
                                            See all {g.connectors.length}
                                        </Button>
                                    ) : null
                                }
                            >
                                {g.group}
                            </SectionTitle>
                            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                                {rows.map((connector) => (
                                    <ConnectorCard
                                        key={connector.slug}
                                        connector={connector}
                                        onConnected={reload}
                                    />
                                ))}
                            </div>
                        </section>
                    );
                })
            )}

            {otherCount > 0 && !query.trim() && !group ? (
                <Card>
                    <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
                        <div>
                            <p className="text-sm font-medium">
                                {otherCount} more tool{otherCount === 1 ? "" : "s"}
                            </p>
                            <p className="text-xs text-muted-foreground">
                                Everything that did not fit a category above. Searching covers these too.
                            </p>
                        </div>
                        <Button asChild size="sm" variant="outline">
                            <Link href="/integrations/apps/more">Browse all</Link>
                        </Button>
                    </CardContent>
                </Card>
            ) : null}
        </>
    );
}

export function MarketplaceScreen({ kind }: { kind: ShelfKind }) {
    const [query, setQuery] = useState("");

    return (
        <>
            <PageHeader
                title="Marketplace"
                description="Bots to hire and tools to connect. Everything here works with what you already run."
                tabs={TABS}
            />
            <PageBody className="space-y-8">
                <Hero kind={kind} />
                <div className="relative max-w-md">
                    <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                        className="pl-8"
                        aria-label={kind === "bots" ? "Find a bot" : "Find a tool"}
                        placeholder={
                            kind === "bots"
                                ? "Find a bot by industry, job or name…"
                                : "Find an app or a service you already use…"
                        }
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                    />
                </div>
                {kind === "bots" ? <BotsShelf query={query} /> : <ToolsShelf query={query} />}
            </PageBody>
        </>
    );
}
