"use client";

/**
 * What a speech-to-speech model actually costs, against the call you actually run.
 *
 * Every vendor quotes realtime pricing per million audio tokens, and every
 * comparison article converts that to a per-minute figure by multiplying by a
 * tokenisation rate. Both stop one step early. **A realtime session re-sends
 * the conversation on every turn, audio included**, so turn twelve pays input
 * price on turns one through eleven all over again — the caller's audio and the
 * agent's own replies alike. Cost grows with the square of call length while
 * the quoted figure describes only the first turn.
 *
 * So this screen has a duration control, and it is the most important one on
 * it. Moving it from one minute to ten roughly triples the per-minute cost,
 * which is not a detail — it is the difference between a model being cheaper
 * than a cascaded stack and being twice the price.
 *
 * The prices are vendor list, dated, and shown as such. They are not this
 * account's negotiated rates and this screen never pretends otherwise.
 */

import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { compareRealtimeApiV1CostEstimateRealtimePost } from "@/client/sdk.gen";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { detailFromError } from "@/lib/apiError";
import { formatMicrosUsd } from "@/lib/billing/format";

import { seriesColor } from "../_components/chartTheme";
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
} from "../_components/primitives";

/** Call lengths worth comparing. The spread is the point of the control. */
const DURATIONS = [
  { key: "60", label: "1 minute" },
  { key: "180", label: "3 minutes" },
  { key: "300", label: "5 minutes" },
  { key: "600", label: "10 minutes" },
] as const;

/** How chatty the agent is. Its own speech is billed as output *and* re-billed
 *  as input on every later turn, so this moves the answer more than it looks. */
const TURN_LENGTHS = [
  { key: "5", label: "5s — clipped, transactional" },
  { key: "10", label: "10s — typical" },
  { key: "20", label: "20s — explanatory" },
] as const;

type ModelRow = {
  provider: string;
  model: string;
  label: string;
  micros_usd_per_minute: number;
  total_micros_usd: number;
  fresh_input_micros_usd: number;
  repeated_input_micros_usd: number;
  output_micros_usd: number;
  fresh_input_tokens: number;
  repeated_input_tokens: number;
  output_tokens: number;
  repeated_share: number;
  caching_applied: boolean;
  basis: string;
};

type Comparison = {
  models: ModelRow[];
  turns: number;
  context_multiple: number;
  as_of: string;
  note: string;
};

function formatShare(share: number): string {
  return `${Math.round(share * 100)}%`;
}

export default function RealtimePricingPage() {
  const authReady = useAuthReady();
  const mode = useChartMode();

  const [duration, setDuration] = useState<string>("180");
  const [secondsPerTurn, setSecondsPerTurn] = useState<string>("10");
  const [caching, setCaching] = useState(false);

  const [data, setData] = useState<Comparison | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!authReady) return;
    let cancelled = false;

    (async () => {
      setLoading(true);
      setError(null);
      const response = await compareRealtimeApiV1CostEstimateRealtimePost({
        body: {
          duration_seconds: Number(duration),
          seconds_per_turn: Number(secondsPerTurn),
          caching_enabled: caching,
        },
      });
      if (cancelled) return;
      if (response.error) {
        setError(
          detailFromError(response.error, "Could not price realtime models"),
        );
        setLoading(false);
        return;
      }
      setData(response.data as unknown as Comparison);
      setLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [authReady, duration, secondsPerTurn, caching]);

  const cheapest = data?.models[0] ?? null;
  const chartData =
    data?.models.map((row) => ({
      name: row.label,
      "First pass": row.fresh_input_micros_usd,
      "Re-sent context": row.repeated_input_micros_usd,
      "Generated audio": row.output_micros_usd,
    })) ?? [];

  return (
    <div className="space-y-6">
      <Card className="glass-panel">
        <CardHeader>
          <CardTitle className="text-base">The call being priced</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-6">
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Call length</Label>
            <Select value={duration} onValueChange={setDuration}>
              <SelectTrigger className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DURATIONS.map((option) => (
                  <SelectItem key={option.key} value={option.key}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">
              Seconds per turn
            </Label>
            <Select value={secondsPerTurn} onValueChange={setSecondsPerTurn}>
              <SelectTrigger className="w-60">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TURN_LENGTHS.map((option) => (
                  <SelectItem key={option.key} value={option.key}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-3 pb-2">
            <Switch
              id="caching"
              checked={caching}
              onCheckedChange={setCaching}
            />
            <Label
              htmlFor="caching"
              className="text-sm font-normal text-muted-foreground"
            >
              Assume context caching is being hit
            </Label>
          </div>
        </CardContent>
      </Card>

      {loading && <LoadingBlock label="Pricing models" />}

      {error && (
        <PanelMessage icon={<AlertTriangle className="h-5 w-5" />}>
          {error}
        </PanelMessage>
      )}

      {!loading && !error && data && cheapest && (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <StatTile
              label="Cheapest per minute"
              value={formatMicrosUsd(cheapest.micros_usd_per_minute)}
              sub={cheapest.label}
            />
            <StatTile
              label="Context re-sent"
              value={`${data.context_multiple.toFixed(1)}×`}
              sub={`${data.turns} turns — the audio is billed as input this many times over`}
            />
            <StatTile
              label="Repetition, cheapest model"
              value={formatShare(cheapest.repeated_share)}
              sub="Share of the bill that is context arriving again"
              tone={cheapest.repeated_share > 0.5 ? "warning" : undefined}
            />
          </div>

          <ChartCard
            title="Where the money goes"
            description="Generated audio is charged once. The first pass of input is charged once. Everything else is the same audio, arriving again."
            isEmpty={chartData.length === 0}
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                <XAxis dataKey="name" {...axisProps(mode)} />
                <YAxis
                  {...axisProps(mode)}
                  tickFormatter={(value: number) => formatMicrosUsd(value)}
                />
                <RTooltip
                  content={
                    <ChartTooltip
                      formatter={(value) => formatMicrosUsd(value)}
                    />
                  }
                />
                <Legend />
                <Bar
                  dataKey="First pass"
                  stackId="cost"
                  fill={seriesColor(0, mode)}
                />
                <Bar
                  dataKey="Re-sent context"
                  stackId="cost"
                  fill={seriesColor(1, mode)}
                />
                <Bar
                  dataKey="Generated audio"
                  stackId="cost"
                  fill={seriesColor(2, mode)}
                />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>

          <Card className="glass-panel">
            <CardHeader>
              <CardTitle className="text-base">
                Per model, for this call
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Model</TableHead>
                    <TableHead className="text-right">Per minute</TableHead>
                    <TableHead className="text-right">Whole call</TableHead>
                    <TableHead className="text-right">Repetition</TableHead>
                    <TableHead className="text-right">Caching</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.models.map((row) => (
                    <TableRow key={`${row.provider}:${row.model}`}>
                      <TableCell>
                        <div className="font-medium">{row.label}</div>
                        <div className="text-xs text-muted-foreground">
                          {row.basis}
                        </div>
                      </TableCell>
                      <TableCell className="text-right font-medium">
                        {formatMicrosUsd(row.micros_usd_per_minute)}
                      </TableCell>
                      <TableCell className="text-right">
                        {formatMicrosUsd(row.total_micros_usd)}
                      </TableCell>
                      <TableCell className="text-right">
                        {formatShare(row.repeated_share)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {row.caching_applied
                          ? "applied"
                          : caching
                            ? "not offered"
                            : "off"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {/* Provenance travels with the numbers. These are list
                                prices off a vendor page, not this account's rates,
                                and the distinction matters before anyone quotes a
                                customer from this screen. */}
              <p className="mt-4 text-xs text-muted-foreground">
                Vendor list prices as of {data.as_of}. {data.note}
              </p>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
