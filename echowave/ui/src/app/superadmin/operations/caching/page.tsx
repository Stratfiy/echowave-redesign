"use client";

import type { ReactNode } from "react";

import { MetricDefinition } from "@/components/shell";
import { Empty, PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness } from "@/components/staff/StaffShell";
import { useStaffData } from "@/lib/staff/data";
import { count, money, percent, words } from "@/lib/staff/format";

type Bucket = {
    calls: number;
    input_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    output_tokens: number;
    reasoning_tokens: number;
    cacheable_input_tokens: number;
    hit_rate: number | null;
    raw_hit_rate: number | null;
    warm_calls: number;
    cold_calls: number;
    cost_paise: number;
    unpriced: string[];
};

type Caching = {
    window: { start: string; end: string; days: number };
    totals: Bucket & { conversations: number; retry_calls: number; side_calls: number };
    by_feature: Array<Bucket & { feature: string }>;
    by_model: Array<Bucket & { provider: string; model: string }>;
    top_fingerprints: Array<Bucket & { prompt_fingerprint: string; feature: string | null }>;
    cold_warm: Array<Bucket & { feature: string; position: string }>;
    cost_per_outcome: Array<{
        feature: string;
        tasks: number;
        tasks_with_outcome: number;
        succeeded: number;
        cost_paise_of_tasks_with_outcome: number;
        cost_paise_per_success: number | null;
        retry_calls: number;
        side_calls: number;
    }>;
    prefix_breaks: {
        by_feature: Array<{ feature: string; conversations: number; conversations_with_breaks: number; breaks: number; system: number; tools: number; both: number }>;
        top_conversations: Array<{ conversation_key: string; feature: string; breaks: number; kinds: Record<string, number> }>;
        volatile_prefixes: Array<{ prompt_fingerprint: string; feature: string | null; conversations: number; distinct_first_prefixes: number }>;
    };
    capabilities: Array<{
        provider: string;
        model: string;
        supported: boolean | string;
        mechanism: string;
        min_cacheable_tokens: number | string;
        platform_requests_cache: boolean | string;
        cache_read_multiplier: number | string;
        cache_write_multiplier: number | string;
    }>;
};

const WINDOW_DAYS = 7;

function Cell({ children, right }: { children: ReactNode; right?: boolean }) {
    return <td className={`py-1 ${right ? "text-right" : ""}`}>{children}</td>;
}

function Head({ labels, firstLeft = 1 }: { labels: string[]; firstLeft?: number }) {
    return (
        <thead className="text-left text-xs text-muted-foreground">
            <tr>
                {labels.map((label, i) => (
                    <th key={label} className={`py-1 font-normal ${i >= firstLeft ? "text-right" : ""}`}>
                        {label}
                    </th>
                ))}
            </tr>
        </thead>
    );
}

function shown(value: unknown): string {
    if (value === true) return "yes";
    if (value === false) return "no";
    return String(value);
}

/**
 * Prompt caching (roadmap item 10): is the cache being hit, what does a
 * finished task cost, and which prompts keep changing their prefix. Seven
 * days of every model call, both doors. Measures; nothing here changes a
 * request. See docs/plans/caching-and-compression.md for how to read it.
 */
export default function CachingPage() {
    const query = useStaffData<Caching>("/api/v1/admin/staff/operations/caching", { days: WINDOW_DAYS });
    useReportFreshness(query.state, query.refreshedAt);
    return (
        <div className="space-y-4">
            <PageHeader
                title="Prompt caching"
                description="Cache hit rate, cold and warm calls, cost per successful outcome and the prefixes that broke, over the last 7 days. Calls too short for their vendor to cache are left out of the hit rate, never counted as misses."
            />
            <Panel title="Headline" query={query}>
                {(d) => (
                    <div className="grid gap-4 sm:grid-cols-3">
                        <MetricDefinition
                            name="Cache hit rate"
                            value={d.totals.hit_rate === null ? null : percent(d.totals.hit_rate)}
                            definition="Tokens read from the vendor's cache over the input of every call long enough to be cached."
                            period={`${d.window.days} days · n=${count(d.totals.calls)} calls`}
                            missingReason="No call in this window was long enough to cache."
                            source="llm_call_usage"
                        />
                        <MetricDefinition
                            name="Vendor cost, cache writes included"
                            value={money(d.totals.cost_paise)}
                            definition="What the vendors charge us, priced against the rate book. Calls on an account's own key are counted, not priced."
                            period={`${count(d.totals.conversations)} conversations`}
                            missingReason="Nothing priced in this window."
                            source="llm_call_usage × provider rates"
                        />
                        <MetricDefinition
                            name="Retries and background calls"
                            value={`${count(d.totals.retry_calls)} / ${count(d.totals.side_calls)}`}
                            definition="Second attempts (rate limit, another vendor, the fallback model) and background summaries. Both are in every task's cost."
                            period={`${d.window.days} days`}
                            missingReason="None recorded."
                            source="llm_call_usage"
                        />
                    </div>
                )}
            </Panel>

            <Panel title="By feature" query={query}>
                {(d) =>
                    d.by_feature.length === 0 ? (
                        <Empty>No model calls recorded in this window.</Empty>
                    ) : (
                        <TableRegion label="Cache by feature">
                            <table className="w-full min-w-[640px] text-sm">
                                <Head labels={["Feature", "Calls", "Hit rate", "Warm", "Cold", "Cache writes", "Cost"]} />
                                <tbody>
                                    {d.by_feature.map((r) => (
                                        <tr key={r.feature} className="border-t border-border tabular-nums">
                                            <Cell>{words(r.feature)}</Cell>
                                            <Cell right>{count(r.calls)}</Cell>
                                            <Cell right>{percent(r.hit_rate)}</Cell>
                                            <Cell right>{count(r.warm_calls)}</Cell>
                                            <Cell right>{count(r.cold_calls)}</Cell>
                                            <Cell right>{count(r.cache_write_tokens)}</Cell>
                                            <Cell right>
                                                {money(r.cost_paise)}
                                                {r.unpriced.length > 0 && <span className="ml-1 text-xs text-muted-foreground">+ unpriced</span>}
                                            </Cell>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </TableRegion>
                    )
                }
            </Panel>

            <div className="grid gap-4 lg:grid-cols-2">
                <Panel title="By model" query={query}>
                    {(d) => (
                        <TableRegion label="Cache by model">
                            <table className="w-full min-w-[480px] text-sm">
                                <Head labels={["Vendor and model", "Calls", "Hit rate", "Cost"]} />
                                <tbody>
                                    {d.by_model.map((r) => (
                                        <tr key={`${r.provider}/${r.model}`} className="border-t border-border tabular-nums">
                                            <Cell>
                                                {r.provider} <span className="font-mono text-xs">{r.model}</span>
                                            </Cell>
                                            <Cell right>{count(r.calls)}</Cell>
                                            <Cell right>{percent(r.hit_rate)}</Cell>
                                            <Cell right>{money(r.cost_paise)}</Cell>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </TableRegion>
                    )}
                </Panel>
                <Panel title="Cold and warm" query={query}>
                    {(d) => (
                        <TableRegion label="Cold and warm calls">
                            <table className="w-full min-w-[480px] text-sm">
                                <Head labels={["Feature", "Call", "Warm", "Cold", "Hit rate"]} firstLeft={2} />
                                <tbody>
                                    {d.cold_warm.map((r) => (
                                        <tr key={`${r.feature}-${r.position}`} className="border-t border-border tabular-nums">
                                            <Cell>{words(r.feature)}</Cell>
                                            <Cell>{r.position === "first" ? "First of conversation" : "Later"}</Cell>
                                            <Cell right>{count(r.warm_calls)}</Cell>
                                            <Cell right>{count(r.cold_calls)}</Cell>
                                            <Cell right>{percent(r.hit_rate)}</Cell>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </TableRegion>
                    )}
                </Panel>
            </div>

            <Panel title="Cost per successful outcome" query={query}>
                {(d) => (
                    <div className="space-y-2">
                        <p className="text-xs text-muted-foreground">Every call of a task counts, retries and summaries included, and failed tasks are carried by the ones that succeeded. Work with no outcome (a chat) shows none.</p>
                        <TableRegion label="Cost per successful outcome by feature">
                            <table className="w-full min-w-[560px] text-sm">
                                <Head labels={["Feature", "Tasks", "With outcome", "Succeeded", "Per success", "Retries", "Summaries"]} />
                                <tbody>
                                    {d.cost_per_outcome.map((r) => (
                                        <tr key={r.feature} className="border-t border-border tabular-nums">
                                            <Cell>{words(r.feature)}</Cell>
                                            <Cell right>{count(r.tasks)}</Cell>
                                            <Cell right>{count(r.tasks_with_outcome)}</Cell>
                                            <Cell right>{count(r.succeeded)}</Cell>
                                            <Cell right>{money(r.cost_paise_per_success)}</Cell>
                                            <Cell right>{count(r.retry_calls)}</Cell>
                                            <Cell right>{count(r.side_calls)}</Cell>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </TableRegion>
                    </div>
                )}
            </Panel>

            <div className="grid gap-4 lg:grid-cols-2">
                <Panel title="Top prompts by spend" query={query}>
                    {(d) =>
                        d.top_fingerprints.length === 0 ? (
                            <Empty>No prompt fingerprints recorded yet.</Empty>
                        ) : (
                            <TableRegion label="Top prompt fingerprints">
                                <table className="w-full min-w-[420px] text-sm">
                                    <Head labels={["Fingerprint", "Calls", "Hit rate", "Cost"]} />
                                    <tbody>
                                        {d.top_fingerprints.map((r) => (
                                            <tr key={r.prompt_fingerprint} className="border-t border-border tabular-nums">
                                                <Cell>
                                                    <span className="font-mono text-xs">{r.prompt_fingerprint}</span> <span className="text-xs text-muted-foreground">{words(r.feature)}</span>
                                                </Cell>
                                                <Cell right>{count(r.calls)}</Cell>
                                                <Cell right>{percent(r.hit_rate)}</Cell>
                                                <Cell right>{money(r.cost_paise)}</Cell>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </TableRegion>
                        )
                    }
                </Panel>
                <Panel title="Prefix breakers" query={query}>
                    {(d) => (
                        <div className="space-y-3" data-testid="prefix-breakers">
                            <TableRegion label="Prefix breaks by feature">
                                <table className="w-full min-w-[420px] text-sm">
                                    <Head labels={["Feature", "Conversations", "With breaks", "System", "Tools", "Both"]} />
                                    <tbody>
                                        {d.prefix_breaks.by_feature.map((r) => (
                                            <tr key={r.feature} className="border-t border-border tabular-nums">
                                                <Cell>{words(r.feature)}</Cell>
                                                <Cell right>{count(r.conversations)}</Cell>
                                                <Cell right>{count(r.conversations_with_breaks)}</Cell>
                                                <Cell right>{count(r.system)}</Cell>
                                                <Cell right>{count(r.tools)}</Cell>
                                                <Cell right>{count(r.both)}</Cell>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </TableRegion>
                            {d.prefix_breaks.top_conversations.length === 0 ? (
                                <Empty>No conversation changed its prefix in this window.</Empty>
                            ) : (
                                <ul className="divide-y divide-border text-sm">
                                    {d.prefix_breaks.top_conversations.map((c) => (
                                        <li key={c.conversation_key} className="flex flex-wrap items-center gap-2 py-1">
                                            <span className="font-mono text-xs">{c.conversation_key}</span>
                                            <span className="text-xs text-muted-foreground">{words(c.feature)}</span>
                                            <span className="ml-auto text-xs tabular-nums">
                                                {count(c.breaks)} breaks ({Object.entries(c.kinds).map(([k, n]) => `${k} ${n}`).join(", ")})
                                            </span>
                                        </li>
                                    ))}
                                </ul>
                            )}
                            {d.prefix_breaks.volatile_prefixes.length > 0 && (
                                <p className="text-xs text-muted-foreground">
                                    Prompts sent with a different prefix in each conversation (something per-call sits in the prefix):{" "}
                                    {d.prefix_breaks.volatile_prefixes.map((v) => `${v.prompt_fingerprint} (${v.distinct_first_prefixes} prefixes in ${v.conversations} conversations)`).join("; ")}
                                </p>
                            )}
                        </div>
                    )}
                </Panel>
            </div>

            <Panel title="What each vendor's cache does" query={query}>
                {(d) => (
                    <TableRegion label="Cache capabilities by vendor">
                        <table className="w-full min-w-[640px] text-sm">
                            <Head labels={["Vendor", "Caches", "How", "Minimum tokens", "We ask for it", "Read ×", "Write ×"]} />
                            <tbody>
                                {d.capabilities
                                    .filter((c) => c.model === "")
                                    .map((c) => (
                                        <tr key={c.provider} className="border-t border-border tabular-nums">
                                            <Cell>{c.provider}</Cell>
                                            <Cell right>
                                                <StateBadge state={c.supported === true ? "ok" : c.supported === false ? "unavailable" : "unknown"} label={shown(c.supported)} />
                                            </Cell>
                                            <Cell right>{words(String(c.mechanism))}</Cell>
                                            <Cell right>{shown(c.min_cacheable_tokens)}</Cell>
                                            <Cell right>{shown(c.platform_requests_cache)}</Cell>
                                            <Cell right>{shown(c.cache_read_multiplier)}</Cell>
                                            <Cell right>{shown(c.cache_write_multiplier)}</Cell>
                                        </tr>
                                    ))}
                            </tbody>
                        </table>
                    </TableRegion>
                )}
            </Panel>
        </div>
    );
}
