"use client";

/**
 * What each model earns, and what each model costs us.
 *
 * Both halves of this have always been stored — every cost line carries
 * `cost_paise` (what the customer paid) beside `provider_cost_paise` (what the
 * vendor charged us) — but nothing had ever grouped them by the model that
 * incurred them. The Tokens screen answers the same question for language
 * models only, and only in what was charged.
 *
 * Two design decisions carry this screen:
 *
 * **Margin is drawn as a diverging bar, not a share.** A model priced below
 * what the vendor charges is the single thing worth finding here, and a
 * percentage-of-revenue chart cannot show a negative. Zero is a real axis
 * position; anything left of it is a model losing money on every call.
 *
 * **Raw usage is never totalled.** A row's units are seconds, characters or
 * tokens depending on what its rate is quoted against, so the column carries
 * its own unit per row and no footer sums it. Money is the only quantity here
 * that is comparable across components.
 */

import { AlertTriangle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    ReferenceLine,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

import { getModelUsageApiV1AdminBillingModelUsageGet } from "@/client/sdk.gen";
import { COST_COMPONENTS, seriesColor, STATUS } from "@/components/charts/chartTheme";
import {
    axisProps,
    ChartCard,
    gridStroke,
    LoadingBlock,
    OrderedLegend,
    PanelMessage,
    StatTile,
    useAuthReady,
    useChartMode,
} from "@/components/charts/primitives";
import { Button } from "@/components/ui/button";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { detailFromError } from "@/lib/apiError";
import {
    formatNumber,
    formatPaise,
    formatPaiseCompact,
    formatPercent,
} from "@/lib/billing/format";

type ModelRow = {
    component: string;
    provider: string;
    model: string;
    calls: number;
    units: number;
    unit: string;
    unit_label: string;
    rate_mpaise: number;
    charged_paise: number;
    our_cost_paise: number;
    margin_paise: number;
    margin_pct: number | null;
    paise_per_call: number | null;
};

type ComponentRow = {
    component: string;
    charged_paise: number;
    our_cost_paise: number;
    margin_paise: number;
    models: number;
};

type Report = {
    models: ModelRow[];
    by_component: ComponentRow[];
    totals: {
        charged_paise: number;
        our_cost_paise: number;
        margin_paise: number;
        margin_pct: number | null;
    };
};

const FILTERS = [
    { key: null, label: "All components" },
    ...COST_COMPONENTS.map((c) => ({ key: c.key as string | null, label: c.label })),
] as const;

function componentLabel(key: string): string {
    return COST_COMPONENTS.find((c) => c.key === key)?.label ?? key;
}

function componentSlot(key: string): number {
    return COST_COMPONENTS.find((c) => c.key === key)?.slot ?? 0;
}

/**
 * "openai · gpt-4o-mini".
 *
 * Not every line has both halves: telephony is priced per provider with no
 * model, and the platform fee has neither. Rendering the server's
 * "(unspecified)" placeholder for those would read as missing data rather than
 * as a line that legitimately has no model, so each is named for what it is.
 */
function rowName(row: ModelRow): string {
    if (row.component === "platform") return "Platform fee";
    const hasModel = row.model && row.model !== "(unspecified)";
    if (!row.provider || row.provider === "—") return hasModel ? row.model : "—";
    return hasModel ? `${row.provider} · ${row.model}` : row.provider;
}

export default function ModelEconomicsPage() {
    const mode = useChartMode();
    const authReady = useAuthReady();

    const [component, setComponent] = useState<string | null>(null);
    const [data, setData] = useState<Report | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async (filter: string | null) => {
        setLoading(true);
        const result = await getModelUsageApiV1AdminBillingModelUsageGet({
            query: filter ? { component: filter } : {},
        });
        if (result.error) {
            setError(detailFromError(result.error, "Failed to load model usage"));
        } else {
            setData((result.data as unknown as Report) ?? null);
            setError(null);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (!authReady) return;
        void load(component);
    }, [authReady, component, load]);

    if (loading && !data) return <LoadingBlock label="Loading model economics" />;
    if (error && !data) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={200}>
                {error}
            </PanelMessage>
        );
    }

    const models = data?.models ?? [];
    const byComponent = data?.by_component ?? [];
    const totals = data?.totals;

    // The ten that move the most money. A margin chart of forty rows is a
    // texture, not a reading — and the tail is in the table below either way.
    const chartRows = [...models]
        .sort((a, b) => Math.abs(b.margin_paise) - Math.abs(a.margin_paise))
        .slice(0, 10)
        .map((row) => ({ ...row, name: rowName(row) }));

    const losing = models.filter((row) => row.margin_paise < 0);

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-center gap-2">
                {FILTERS.map((filter) => (
                    <Button
                        key={filter.key ?? "all"}
                        size="sm"
                        variant={component === filter.key ? "default" : "outline"}
                        onClick={() => setComponent(filter.key)}
                    >
                        {filter.label}
                    </Button>
                ))}
                <span className="text-xs text-muted-foreground">
                    Attributed to the day the call happened, not the day costing ran.
                </span>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile
                    label="Charged"
                    value={formatPaiseCompact(totals?.charged_paise)}
                    sub="What customers paid across these lines"
                />
                <StatTile
                    label="Our cost"
                    value={formatPaiseCompact(totals?.our_cost_paise)}
                    sub="What the vendors charged us"
                />
                <StatTile
                    label="Margin"
                    value={formatPaiseCompact(totals?.margin_paise)}
                    sub={formatPercent(totals?.margin_pct)}
                    tone={
                        totals && totals.margin_paise < 0 ? "critical" : undefined
                    }
                />
                <StatTile
                    label="Losing money"
                    value={String(losing.length)}
                    sub={
                        losing.length
                            ? `${losing.map((r) => r.model).slice(0, 2).join(", ")}${
                                  losing.length > 2 ? "…" : ""
                              }`
                            : "Every model priced above its vendor cost"
                    }
                    tone={losing.length ? "critical" : undefined}
                />
            </div>

            {/* Diverging, anchored at zero. A model to the left of the line is
                sold below what the vendor charges — the one finding on this
                screen that a share-of-revenue chart could not express at all. */}
            <ChartCard
                title="Margin by model"
                description="What each model earns after the vendor is paid. Left of zero is a loss."
                loading={loading}
                error={error}
                isEmpty={chartRows.length === 0}
                emptyMessage="No costed calls in this window yet."
                height={Math.max(220, chartRows.length * 34)}
            >
                <ResponsiveContainer
                    width="100%"
                    height={Math.max(220, chartRows.length * 34)}
                >
                    <BarChart
                        data={chartRows}
                        layout="vertical"
                        margin={{ top: 4, right: 24, bottom: 4, left: 8 }}
                        barCategoryGap={8}
                    >
                        <CartesianGrid horizontal={false} stroke={gridStroke(mode)} />
                        <XAxis
                            type="number"
                            tickFormatter={(v: number) => formatPaiseCompact(v)}
                            {...axisProps(mode)}
                        />
                        <YAxis
                            type="category"
                            dataKey="name"
                            width={190}
                            {...axisProps(mode)}
                        />
                        {/* Zero is the whole point of the chart, so it is drawn
                            rather than left implied by where the bars start. */}
                        <ReferenceLine x={0} stroke={gridStroke(mode)} />
                        <RTooltip
                            cursor={{ fill: "transparent" }}
                            content={({ active, payload }) => {
                                if (!active || !payload?.length) return null;
                                const row = payload[0].payload as ModelRow & {
                                    name: string;
                                };
                                const rows: Array<[string, string]> = [
                                    ["Charged", formatPaise(row.charged_paise)],
                                    ["Our cost", formatPaise(row.our_cost_paise)],
                                    ["Margin", formatPaise(row.margin_paise)],
                                    ["Margin %", formatPercent(row.margin_pct)],
                                    ["Calls", formatNumber(row.calls)],
                                    [
                                        row.unit_label
                                            .charAt(0)
                                            .toUpperCase() + row.unit_label.slice(1),
                                        formatNumber(row.units),
                                    ],
                                ];
                                return (
                                    <div className="glass-panel rounded-xl px-3 py-2 text-xs">
                                        <p className="mb-1 font-medium text-foreground">
                                            {row.name}
                                        </p>
                                        <p className="mb-1.5 text-[11px] text-muted-foreground">
                                            {componentLabel(row.component)}
                                        </p>
                                        <div className="space-y-0.5">
                                            {rows.map(([label, value]) => (
                                                <div
                                                    key={label}
                                                    className="flex items-center justify-between gap-4"
                                                >
                                                    <span className="text-muted-foreground">
                                                        {label}
                                                    </span>
                                                    <span className="font-medium tabular-nums text-foreground">
                                                        {value}
                                                    </span>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                );
                            }}
                        />
                        <Bar dataKey="margin_paise" radius={[0, 3, 3, 0]} maxBarSize={22}>
                            {chartRows.map((row) => (
                                <Cell
                                    key={`${row.component}-${row.provider}-${row.model}`}
                                    // Colour follows the component so filtering
                                    // never repaints a survivor — except for a
                                    // loss, which is a *state* and takes the
                                    // reserved critical colour.
                                    fill={
                                        row.margin_paise < 0
                                            ? STATUS.critical
                                            : seriesColor(
                                                  componentSlot(row.component),
                                                  mode,
                                              )
                                    }
                                />
                            ))}
                        </Bar>
                    </BarChart>
                </ResponsiveContainer>
                <OrderedLegend
                    items={[
                        ...COST_COMPONENTS.map((c) => ({
                            key: c.key,
                            label: c.label,
                            color: seriesColor(c.slot, mode),
                        })),
                        { key: "loss", label: "Loss", color: STATUS.critical },
                    ]}
                />
            </ChartCard>

            <ChartCard
                title="By component"
                description="Where the margin comes from before you look at any one model"
                isEmpty={byComponent.length === 0}
                height={140}
            >
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                    {byComponent.map((row) => (
                        <div key={row.component} className="glass-tile px-4 py-3">
                            <div className="flex items-center gap-2">
                                <span
                                    aria-hidden
                                    className="inline-block h-2 w-2 shrink-0 rounded-[1px]"
                                    style={{
                                        backgroundColor: seriesColor(
                                            componentSlot(row.component),
                                            mode,
                                        ),
                                    }}
                                />
                                <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                                    {componentLabel(row.component)}
                                </p>
                            </div>
                            <p className="mt-1.5 text-xl font-semibold tabular-nums tracking-[-0.03em] text-foreground">
                                {formatPaiseCompact(row.margin_paise)}
                            </p>
                            <p className="mt-0.5 text-xs text-muted-foreground">
                                {formatPaiseCompact(row.charged_paise)} charged ·{" "}
                                {row.models} model{row.models === 1 ? "" : "s"}
                            </p>
                        </div>
                    ))}
                </div>
            </ChartCard>

            {/* The table is not a fallback for the chart. It is where the tail
                lives, where exact figures are read, and the only place the raw
                usage counts appear — each beside the unit it is counted in,
                because there is no total that could legitimately combine them. */}
            <ChartCard
                title="Every model"
                description="Usage in its own unit, charged, our cost, and the margin between."
                isEmpty={models.length === 0}
                height={200}
            >
                <div className="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Model</TableHead>
                                <TableHead>Component</TableHead>
                                <TableHead className="text-right">Calls</TableHead>
                                <TableHead className="text-right">Usage</TableHead>
                                <TableHead className="text-right">Charged</TableHead>
                                <TableHead className="text-right">Our cost</TableHead>
                                <TableHead className="text-right">Margin</TableHead>
                                <TableHead className="text-right">Margin %</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {models.map((row) => (
                                <TableRow
                                    key={`${row.component}-${row.provider}-${row.model}`}
                                >
                                    <TableCell className="font-medium">
                                        {rowName(row)}
                                    </TableCell>
                                    <TableCell className="text-muted-foreground">
                                        {componentLabel(row.component)}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {formatNumber(row.calls)}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums text-muted-foreground">
                                        {formatNumber(row.units)}{" "}
                                        <span className="text-xs">{row.unit_label}</span>
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {formatPaise(row.charged_paise)}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums text-muted-foreground">
                                        {formatPaise(row.our_cost_paise)}
                                    </TableCell>
                                    <TableCell
                                        className={
                                            row.margin_paise < 0
                                                ? "text-right font-medium tabular-nums text-destructive"
                                                : "text-right tabular-nums"
                                        }
                                    >
                                        {formatPaise(row.margin_paise)}
                                    </TableCell>
                                    <TableCell
                                        className={
                                            row.margin_paise < 0
                                                ? "text-right font-medium tabular-nums text-destructive"
                                                : "text-right tabular-nums text-muted-foreground"
                                        }
                                    >
                                        {formatPercent(row.margin_pct)}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            </ChartCard>
        </div>
    );
}
