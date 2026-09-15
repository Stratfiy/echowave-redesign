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

import { ArrowRight, Search, Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { client } from "@/client/client.gen";
import { getToolLibraryApiV1ToolLibraryGet, listConnectorsApiV1ConnectorsGet } from "@/client/sdk.gen";
import type { ConnectorGroupResponse, ConnectorResponse, LibraryTool } from "@/client/types.gen";
import { ConnectorLogo, ConnectorRow } from "@/components/integrations/ConnectorRow";
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

export type ShelfKind = "bots" | "tools" | "integrations";

/** The four shelves, in the order somebody shops them: what a bot can do
 *  (tools), how it should do it (skills), the bot itself, and the apps it
 *  reaches. Skills is not here yet -- the format is parsed and nothing
 *  publishes one, so a tab would be an empty room. */
const TABS: PageTab[] = [
    { href: "/marketplace/tools", label: "Tools", prefix: true },
    { href: "/marketplace", label: "Bots" },
    { href: "/marketplace/integrations", label: "Integrations", prefix: true },
];

const HERO: Record<ShelfKind, { title: string; blurb: string }> = {
    bots: {
        title: "A bot for every job, ready the day you add it.",
        blurb: "Pick one for your industry or for the job, hear it on a call, then put it on a number.",
    },
    tools: {
        title: "The things a bot can do, ready to hand it.",
        blurb: "A tool is one action during a call or a chat: look up an order, book a slot, raise a ticket.",
    },
    integrations: {
        title: "Every system you already run, in your bots' hands.",
        blurb: "Connect the apps your business lives in and every bot can read from and write to them.",
    },
};

function Hero({ kind }: { kind: ShelfKind }) {
    return (
        <div className="rounded-2xl bg-[var(--accent-brand-tint)] px-6 py-8 sm:px-10 sm:py-10">
            <p className="text-xs font-semibold uppercase tracking-wider text-brand-blue">
                Decibyl Marketplace
            </p>
            <h2 className="mt-2 max-w-xl text-2xl font-semibold leading-tight tracking-tight sm:text-3xl">
                {HERO[kind].title}
            </h2>
            <p className="mt-2 max-w-xl text-sm text-muted-foreground">{HERO[kind].blurb}</p>
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
                            Add {template.name}
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

/** How many rows a category shows on the shelf before "View all". */
const ROWS_PER_SECTION = 6;

/** A chip on the shelf: an icon, the name, the count. One press narrows. */
function FilterChip({
    label,
    count,
    icon: Icon,
    selected,
    onSelect,
}: {
    label: string;
    count?: number;
    icon?: React.ComponentType<{ className?: string }>;
    selected: boolean;
    onSelect: () => void;
}) {
    return (
        <button
            type="button"
            aria-pressed={selected}
            onClick={onSelect}
            className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm transition-colors",
                selected
                    ? "border-foreground bg-foreground text-background"
                    : "border-border bg-card text-foreground hover:bg-muted/40",
            )}
        >
            {Icon ? <Icon className="h-3.5 w-3.5" aria-hidden="true" /> : null}
            {label}
            {count !== undefined ? (
                <span className={cn("text-xs", selected ? "opacity-70" : "text-muted-foreground")}>{count}</span>
            ) : null}
        </button>
    );
}

/** The ready-made tools, by the app they act on. A tool is one action a
 *  bot takes mid-call -- look up an order, book a slot -- and this shelf is
 *  the ones that exist already; the button builds one from the entry so
 *  nobody writes an HTTP definition by hand. */
function ToolsShelf({ query }: { query: string }) {
    const [tools, setTools] = useState<LibraryTool[] | null>(null);
    const [failed, setFailed] = useState(false);
    const [vendor, setVendor] = useState<string | null>(null);
    const { user, loading: authLoading } = useAuth();

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;
        void (async () => {
            try {
                const response = await getToolLibraryApiV1ToolLibraryGet();
                if (cancelled) return;
                if (response.error || !response.data) {
                    setFailed(true);
                    return;
                }
                setTools(response.data.tools ?? []);
            } catch {
                if (!cancelled) setFailed(true);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [authLoading, user]);

    if (failed) {
        return (
            <Card>
                <CardContent className="py-8 text-center text-sm text-muted-foreground">
                    The tool shelf could not be loaded. This is us, not you.
                </CardContent>
            </Card>
        );
    }
    if (tools === null) return <p className="text-sm text-muted-foreground">Loading…</p>;

    const needle = query.trim().toLowerCase();
    const matching = needle
        ? tools.filter((t) =>
              [t.display_name, t.summary, t.vendor, t.tool_name].some((f) =>
                  (f ?? "").toLowerCase().includes(needle),
              ),
          )
        : tools;
    const shown = vendor && !needle ? matching.filter((t) => t.vendor === vendor) : matching;
    const vendors = Array.from(new Set(tools.map((t) => t.vendor)));

    return (
        <>
            {!needle ? (
                <div className="flex flex-wrap gap-2" role="group" aria-label="Apps">
                    <FilterChip label="All" selected={vendor === null} onSelect={() => setVendor(null)} />
                    {vendors.map((v) => (
                        <FilterChip
                            key={v}
                            label={v}
                            count={tools.filter((t) => t.vendor === v).length}
                            selected={vendor === v}
                            onSelect={() => setVendor((cur) => (cur === v ? null : v))}
                        />
                    ))}
                </div>
            ) : null}

            {shown.length === 0 ? (
                <Card>
                    <CardContent className="space-y-2 py-8 text-center">
                        <p className="text-sm">Nothing here does that yet.</p>
                        <p className="text-xs text-muted-foreground">
                            Build it as a{" "}
                            <Link href="/tools" className="underline">
                                custom tool
                            </Link>
                            : any HTTP endpoint your business already has.
                        </p>
                    </CardContent>
                </Card>
            ) : (
                <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
                    {shown.map((tool) => (
                        <div
                            key={tool.key}
                            className="flex items-center gap-3 rounded-xl px-2 py-2 hover:bg-muted/40"
                            data-testid="library-tool"
                        >
                            <span
                                aria-hidden="true"
                                className={cn(
                                    "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-sm font-semibold",
                                    toneFor(tool.vendor),
                                )}
                            >
                                {tool.vendor.slice(0, 1).toUpperCase()}
                            </span>
                            <div className="min-w-0 flex-1">
                                <p className="truncate text-sm font-medium">{tool.display_name}</p>
                                <p className="truncate text-xs text-muted-foreground" title={tool.summary}>
                                    {tool.summary}
                                </p>
                            </div>
                            <Button asChild size="sm" variant="outline" className="shrink-0 rounded-full">
                                <Link href={`/tools?library=${encodeURIComponent(tool.key)}`} aria-label={`Add ${tool.display_name}`}>
                                    Add
                                </Link>
                            </Button>
                        </div>
                    ))}
                </div>
            )}

            <Card>
                <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
                    <div>
                        <p className="text-sm font-medium">Something your own systems do?</p>
                        <p className="text-xs text-muted-foreground">
                            Any HTTP endpoint you already have becomes a tool a bot can call mid-conversation.
                        </p>
                    </div>
                    <Button asChild size="sm" variant="outline">
                        <Link href="/tools">Build a tool</Link>
                    </Button>
                </CardContent>
            </Card>
        </>
    );
}

function IntegrationsShelf({ query }: { query: string }) {
    const [available, setAvailable] = useState(true);
    const [loading, setLoading] = useState(true);
    const [failed, setFailed] = useState(false);
    const [popular, setPopular] = useState<ConnectorResponse[]>([]);
    const [groups, setGroups] = useState<ConnectorGroupResponse[]>([]);
    const [other, setOther] = useState<ConnectorResponse[]>([]);
    const [connectedCount, setConnectedCount] = useState(0);
    // "all", "featured", a group's name, or "other".
    const [filter, setFilter] = useState<string>("all");
    const [reloads, setReloads] = useState(0);

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
                    setOther(response.data.other ?? []);
                    setConnectedCount(response.data.connected_count ?? 0);
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
    }, [query, reloads]);

    const reload = () => setReloads((n) => n + 1);
    const searching = Boolean(query.trim());
    // The apps already on: the logos in the strip at the top, the way a
    // plugin shelf shows what you have before what you could have.
    const installed = useMemo(() => {
        const seen = new Set<string>();
        const out: ConnectorResponse[] = [];
        for (const c of [...groups.flatMap((g) => g.connectors), ...other]) {
            if (c.connected && !seen.has(c.slug)) {
                seen.add(c.slug);
                out.push(c);
            }
        }
        return out;
    }, [groups, other]);

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

    // What the shelf shows for the chip pressed. A search shows everything
    // that matched, whatever the chip.
    const sections: { key: string; title: string; rows: ConnectorResponse[] }[] = [];
    if (searching) {
        for (const g of groups) sections.push({ key: g.group, title: g.group, rows: g.connectors });
        if (other.length) sections.push({ key: "other", title: "Other", rows: other });
    } else if (filter === "featured") {
        sections.push({ key: "featured", title: "Featured", rows: popular });
    } else if (filter === "installed") {
        sections.push({ key: "installed", title: "Installed", rows: installed });
    } else if (filter === "other") {
        sections.push({ key: "other", title: "Other", rows: other });
    } else if (filter !== "all") {
        const g = groups.find((x) => x.group === filter);
        if (g) sections.push({ key: g.group, title: g.group, rows: g.connectors });
    } else {
        if (popular.length) sections.push({ key: "featured", title: "Featured", rows: popular });
        for (const g of groups) sections.push({ key: g.group, title: g.group, rows: g.connectors });
        if (other.length) sections.push({ key: "other", title: "Other", rows: other });
    }
    const narrowed = searching || filter !== "all";

    return (
        <>
            {installed.length > 0 && !searching ? (
                <button
                    type="button"
                    aria-pressed={filter === "installed"}
                    onClick={() => setFilter((cur) => (cur === "installed" ? "all" : "installed"))}
                    className={cn(
                        "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition-colors",
                        filter === "installed"
                            ? "border-foreground bg-foreground text-background"
                            : "border-border bg-card hover:bg-muted/40",
                    )}
                >
                    <span className="flex -space-x-1.5">
                        {installed.slice(0, 5).map((c) => (
                            <ConnectorLogo key={c.slug} connector={c} className="h-6 w-6 rounded-md ring-2 ring-card" />
                        ))}
                    </span>
                    <span className={filter === "installed" ? "opacity-80" : "text-muted-foreground"}>
                        {connectedCount || installed.length} installed
                    </span>
                </button>
            ) : null}

            {!searching ? (
                <div className="flex flex-wrap gap-2" role="group" aria-label="Categories">
                    <FilterChip label="All" selected={filter === "all"} onSelect={() => setFilter("all")} />
                    {popular.length > 0 ? (
                        <FilterChip label="Featured" icon={Sparkles} selected={filter === "featured"} onSelect={() => setFilter("featured")} />
                    ) : null}
                    {groups.map((g) => (
                        <FilterChip
                            key={g.group}
                            label={g.group}
                            count={g.connectors.length}
                            icon={toolIcon(g.group)}
                            selected={filter === g.group}
                            onSelect={() => setFilter((cur) => (cur === g.group ? "all" : g.group))}
                        />
                    ))}
                    {other.length > 0 ? (
                        <FilterChip
                            label="Other"
                            count={other.length}
                            icon={toolIcon("Other")}
                            selected={filter === "other"}
                            onSelect={() => setFilter("other")}
                        />
                    ) : null}
                </div>
            ) : null}

            {loading && groups.length === 0 ? (
                <p className="text-sm text-muted-foreground">Loading…</p>
            ) : sections.every((s) => s.rows.length === 0) ? (
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
                sections
                    .filter((s) => s.rows.length > 0)
                    .map((s) => {
                        const rows = narrowed ? s.rows : s.rows.slice(0, ROWS_PER_SECTION);
                        const hidden = s.rows.length - rows.length;
                        return (
                            <section key={s.key} className="space-y-2">
                                <SectionTitle
                                    action={
                                        hidden > 0 ? (
                                            <Button size="sm" variant="ghost" onClick={() => setFilter(s.key)}>
                                                View all {s.rows.length}
                                            </Button>
                                        ) : null
                                    }
                                >
                                    {s.title}
                                </SectionTitle>
                                <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
                                    {rows.map((connector) => (
                                        <ConnectorRow key={`${s.key}:${connector.slug}`} connector={connector} onConnected={reload} />
                                    ))}
                                </div>
                            </section>
                        );
                    })
            )}
        </>
    );
}

const SEARCH: Record<ShelfKind, { label: string; placeholder: string }> = {
    bots: { label: "Find a bot", placeholder: "Find a bot by industry, job or name…" },
    tools: { label: "Find a tool", placeholder: "Find a tool by what it does or the app it uses…" },
    integrations: { label: "Find an app", placeholder: "Find an app or a service you already use…" },
};

export function MarketplaceScreen({ kind }: { kind: ShelfKind }) {
    const [query, setQuery] = useState("");

    return (
        <>
            <PageHeader
                title="Marketplace"
                description="Tools to hand a bot, bots to put to work, and the apps they reach. Everything here works with what you already run."
                tabs={TABS}
            />
            <PageBody className="space-y-8">
                {kind === "bots" ? <Hero kind={kind} /> : null}
                <div className="relative max-w-md">
                    <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                        className="pl-8"
                        aria-label={SEARCH[kind].label}
                        placeholder={SEARCH[kind].placeholder}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                    />
                </div>
                {kind === "bots" ? (
                    <BotsShelf query={query} />
                ) : kind === "tools" ? (
                    <ToolsShelf query={query} />
                ) : (
                    <IntegrationsShelf query={query} />
                )}
            </PageBody>
        </>
    );
}
