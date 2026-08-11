"use client";

import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

import {
    getCampaignConcurrencyApiV1AdminBillingCampaignsCampaignIdConcurrencyGet,
    listCampaignsApiV1AdminBillingCampaignsGet,
} from "@/client/sdk.gen";
import { seriesColor } from "@/components/charts/chartTheme";
import {
    axisProps,
    ChartCard,
    ChartTooltip,
    gridStroke,
    LoadingBlock,
    PanelMessage,
    StatTile,
    useAuthReady,
    useChartMode,
} from "@/components/charts/primitives";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { detailFromError } from "@/lib/apiError";
import {
    formatNumber,
    formatPaise,
    formatPercent,
} from "@/lib/billing/format";
import { cn } from "@/lib/utils";

type Campaign = {
    campaign_id: number;
    name: string;
    account: string;
    state: string;
    contacts_total: number | null;
    dialled: number;
    connected: number;
    completed: number;
    answer_rate: number | null;
    completion_rate: number | null;
    spend_paise: number;
    cost_per_completed_paise: number | null;
    started_at: string | null;
};

export default function CampaignsPage() {
    const mode = useChartMode();
    const [campaigns, setCampaigns] = useState<Campaign[]>([]);
    const [selected, setSelected] = useState<number | null>(null);
    const [concurrency, setConcurrency] = useState<Array<Record<string, never>>>([]);
    // The API picks hourly or daily buckets from the campaign's span; the label
    // and tick format follow whichever it chose.
    const [bucket, setBucket] = useState<"hour" | "day">("day");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const authReady = useAuthReady();

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await listCampaignsApiV1AdminBillingCampaignsGet();
            if (cancelled) return;
            if (result.error) {
                setError(detailFromError(result.error, "Failed to load campaigns"));
            } else {
                const list = (result.data?.campaigns ?? []) as Campaign[];
                setCampaigns(list);
                setSelected(list[0]?.campaign_id ?? null);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady]);

    useEffect(() => {
        if (!authReady || selected === null) return;
        let cancelled = false;
        (async () => {
            const result =
                await getCampaignConcurrencyApiV1AdminBillingCampaignsCampaignIdConcurrencyGet({
                    path: { campaign_id: selected },
                });
            if (cancelled || result.error) return;
            setConcurrency((result.data?.series ?? []) as Array<Record<string, never>>);
            setBucket(result.data?.bucket === "hour" ? "hour" : "day");
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, selected]);

    if (loading) return <LoadingBlock label="Loading campaigns" />;
    if (error) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={200}>
                {error}
            </PanelMessage>
        );
    }
    if (campaigns.length === 0) {
        return (
            <Card>
                <CardContent className="pt-5">
                    <PanelMessage height={200}>
                        No outbound campaigns yet. This view fills in once a campaign starts
                        dialling.
                    </PanelMessage>
                </CardContent>
            </Card>
        );
    }

    // bucket_start is already an IST wall-clock stamp with no offset, so it is
    // read literally — handing it to Date() would re-apply a timezone shift.
    const formatBucket = (value: string) => {
        const [datePart, timePart = ""] = String(value).split("T");
        const [y, m, d] = datePart.split("-").map(Number);
        const day = new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-IN", {
            timeZone: "UTC",
            day: "numeric",
            month: "short",
        });
        if (bucket === "day") return day;
        const hour = Number(timePart.slice(0, 2));
        const suffix = hour < 12 ? "am" : "pm";
        return `${day}, ${hour % 12 || 12}${suffix}`;
    };

    const active = campaigns.find((c) => c.campaign_id === selected) ?? campaigns[0];
    const funnel = [
        { stage: "Dialled", value: active.dialled },
        { stage: "Connected", value: active.connected },
        { stage: "Completed", value: active.completed },
    ];

    // Spend so far divided by elapsed days gives a simple burn rate; projecting
    // it across the remaining contacts is the useful number for an operator.
    const elapsedDays = active.started_at
        ? Math.max(
              1,
              Math.ceil((Date.now() - new Date(active.started_at).getTime()) / 86_400_000),
          )
        : 1;
    const burnPerDay = Math.round(active.spend_paise / elapsedDays);
    const remaining = Math.max(0, (active.contacts_total ?? active.dialled) - active.dialled);
    const projectedTotal =
        active.dialled > 0
            ? active.spend_paise + Math.round((active.spend_paise / active.dialled) * remaining)
            : active.spend_paise;

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap gap-2">
                {campaigns.map((campaign) => (
                    <button
                        key={campaign.campaign_id}
                        type="button"
                        onClick={() => setSelected(campaign.campaign_id)}
                        aria-pressed={campaign.campaign_id === active.campaign_id}
                        className={cn(
                            "rounded-md border px-3 py-1.5 text-sm transition-colors",
                            campaign.campaign_id === active.campaign_id
                                ? "border-primary bg-primary/5 font-medium"
                                : "text-muted-foreground hover:text-foreground",
                        )}
                    >
                        {campaign.name}
                    </button>
                ))}
            </div>

            <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                <StatTile
                    label="Cost per completed"
                    value={formatPaise(active.cost_per_completed_paise)}
                    sub="the headline number"
                />
                <StatTile label="Answer rate" value={formatPercent(active.answer_rate)} />
                <StatTile label="Completion rate" value={formatPercent(active.completion_rate)} />
                <StatTile label="Spend to date" value={formatPaise(active.spend_paise)} />
                <StatTile
                    label="Projected total"
                    value={formatPaise(projectedTotal)}
                    sub={`${formatPaise(burnPerDay)}/day burn`}
                />
            </section>

            <div className="grid gap-4 lg:grid-cols-2">
                <ChartCard
                    title="Funnel"
                    description={`${active.account} · ${active.state}`}
                    isEmpty={active.dialled === 0}
                    action={<Badge variant="outline">{active.state}</Badge>}
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <BarChart
                            data={funnel}
                            layout="vertical"
                            margin={{ top: 4, right: 40, bottom: 0, left: 8 }}
                        >
                            <CartesianGrid stroke={gridStroke(mode)} horizontal={false} />
                            <XAxis type="number" {...axisProps(mode)} />
                            <YAxis type="category" dataKey="stage" width={90} {...axisProps(mode)} />
                            <RTooltip
                                cursor={{ fill: gridStroke(mode), opacity: 0.4 }}
                                content={<ChartTooltip formatter={(v) => formatNumber(v)} />}
                            />
                            <Bar dataKey="value" name="Calls" radius={[0, 4, 4, 0]} maxBarSize={32}>
                                {/* An ordinal ramp would be wrong here — these are
                                    stages of one measure, so one hue reads correctly. */}
                                {funnel.map((_, index) => (
                                    <Cell key={index} fill={seriesColor(0, mode)} />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </ChartCard>

                <ChartCard
                    title="Peak concurrency"
                    description={`Most calls in flight at once, per ${bucket}`}
                    isEmpty={concurrency.length === 0}
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <LineChart data={concurrency} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis
                                dataKey="bucket_start"
                                tickFormatter={formatBucket}
                                {...axisProps(mode)}
                            />
                            {/* Concurrency is a whole number of calls — let the
                                axis land on integers rather than 0.5 steps. */}
                            <YAxis
                                width={40}
                                allowDecimals={false}
                                {...axisProps(mode)}
                            />
                            <RTooltip
                                content={
                                    <ChartTooltip
                                        formatter={(v) => `${formatNumber(v)} in flight`}
                                        labelFormatter={formatBucket}
                                    />
                                }
                            />
                            <Line
                                type="monotone"
                                dataKey="peak"
                                name="Peak concurrent"
                                stroke={seriesColor(0, mode)}
                                strokeWidth={2}
                                dot={false}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                </ChartCard>
            </div>

            <Card>
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-medium">All campaigns</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b text-left text-muted-foreground">
                                    <th className="py-2 font-medium">Campaign</th>
                                    <th className="py-2 font-medium">Account</th>
                                    <th className="py-2 text-right font-medium">Dialled</th>
                                    <th className="py-2 text-right font-medium">Connected</th>
                                    <th className="py-2 text-right font-medium">Completed</th>
                                    <th className="py-2 text-right font-medium">Spend</th>
                                    <th className="py-2 text-right font-medium">Cost/completed</th>
                                </tr>
                            </thead>
                            <tbody>
                                {campaigns.map((campaign) => (
                                    <tr key={campaign.campaign_id} className="border-b last:border-0">
                                        <td className="py-2">{campaign.name}</td>
                                        <td className="py-2 text-muted-foreground">
                                            {campaign.account}
                                        </td>
                                        <td className="py-2 text-right tabular-nums">
                                            {formatNumber(campaign.dialled)}
                                        </td>
                                        <td className="py-2 text-right tabular-nums">
                                            {formatNumber(campaign.connected)}
                                        </td>
                                        <td className="py-2 text-right tabular-nums">
                                            {formatNumber(campaign.completed)}
                                        </td>
                                        <td className="py-2 text-right tabular-nums">
                                            {formatPaise(campaign.spend_paise)}
                                        </td>
                                        <td className="py-2 text-right font-medium tabular-nums">
                                            {formatPaise(campaign.cost_per_completed_paise)}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
