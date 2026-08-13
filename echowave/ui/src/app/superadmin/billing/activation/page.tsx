"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getActivationApiV1AdminBillingActivationGet } from "@/client/sdk.gen";
import { seriesColor } from "@/components/charts/chartTheme";
import {
  axisProps,
  ChartCard,
  gridStroke,
  StatTile,
  useAuthReady,
  useChartMode,
} from "@/components/charts/primitives";
import { Button } from "@/components/ui/button";
import { detailFromError } from "@/lib/apiError";

type Step = {
  key: string;
  label: string;
  count: number;
  hint: string;
  from_previous: number | null;
  from_top: number | null;
  dropped: number;
};

type Funnel = {
  start: string;
  end: string;
  steps: Step[];
  email_verified: number;
  email_verified_rate: number | null;
};

const RANGES = [
  { label: "30 days", days: 29 },
  { label: "90 days", days: 89 },
  { label: "12 months", days: 364 },
] as const;

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

/**
 * The activation funnel.
 *
 * Every other screen in this area measures calls that happened. This one
 * answers the question none of them can: who arrived and never made one.
 *
 * Drawn as ordered bars rather than a tapering funnel graphic. A funnel shape
 * encodes the drop in *area*, which nobody reads accurately and which flatters
 * whichever stage happens to sit in the middle; bars on a shared baseline are
 * read by length, which is the one comparison the eye is reliable at.
 */
export default function ActivationPage() {
  const mode = useChartMode();
  const authReady = useAuthReady();

  const [rangeDays, setRangeDays] = useState<number>(29);
  const [data, setData] = useState<Funnel | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (days: number) => {
    setIsLoading(true);
    setError(null);
    const response = await getActivationApiV1AdminBillingActivationGet({
      query: { start: isoDaysAgo(days), end: isoDaysAgo(0) },
    });
    if (response.error) {
      setError(detailFromError(response.error, "Could not load activation."));
      setIsLoading(false);
      return;
    }
    setData(response.data as unknown as Funnel);
    setIsLoading(false);
  }, []);

  useEffect(() => {
    if (!authReady) return;
    void load(rangeDays);
  }, [authReady, rangeDays, load]);

  const steps = data?.steps ?? [];
  const top = steps[0]?.count ?? 0;
  const paid = steps[steps.length - 1]?.count ?? 0;

  // The biggest single drop is the thing to act on, so it is computed rather
  // than left for the reader to find by comparing five numbers.
  const worst = steps
    .slice(1)
    .reduce<Step | null>(
      (worstSoFar, step) =>
        !worstSoFar || step.dropped > worstSoFar.dropped ? step : worstSoFar,
      null
    );

  return (
    <div className="space-y-6">
        {/* The billing layout supplies the section header and tabs, so this
            screen contributes only its own filter row — the same shape every
            sibling tab uses. */}
        <div className="flex flex-wrap items-center gap-2">
          {RANGES.map((range) => (
            <Button
              key={range.days}
              size="sm"
              variant={rangeDays === range.days ? "default" : "outline"}
              onClick={() => setRangeDays(range.days)}
            >
              {range.label}
            </Button>
          ))}
          <span className="text-xs text-muted-foreground">
            Of the accounts that signed up in this window, how many ever got to a
            call — and to paying.
          </span>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile label="Signed up" value={isLoading ? "—" : String(top)} />
          <StatTile
            label="Reached a call"
            value={
              isLoading ? "—" : `${steps[2]?.from_top ?? 0}%`
            }
            sub={isLoading ? undefined : `${steps[2]?.count ?? 0} accounts`}
          />
          <StatTile
            label="Paid"
            value={isLoading ? "—" : `${steps[3]?.from_top ?? 0}%`}
            sub={isLoading ? undefined : `${paid} accounts`}
          />
          <StatTile
            label="Verified email"
            value={
              isLoading || data?.email_verified_rate === null
                ? "—"
                : `${data?.email_verified_rate}%`
            }
            sub="Not a gate — reported alongside"
          />
        </div>

        <ChartCard
          title="Where accounts stop"
          description={
            worst && worst.dropped > 0
              ? `Biggest drop: ${worst.dropped} accounts before "${worst.label}".`
              : "Each step counts the accounts that ever reached it."
          }
          loading={isLoading}
          error={error}
          isEmpty={!isLoading && top === 0}
          emptyMessage="No accounts signed up in this window."
          height={300}
        >
          <div style={{ width: "100%", height: 300 }}>
              <ResponsiveContainer>
                <BarChart
                  data={steps}
                  layout="vertical"
                  margin={{ top: 4, right: 56, bottom: 4, left: 8 }}
                  barCategoryGap={10}
                >
                  <CartesianGrid
                    horizontal={false}
                    stroke={gridStroke(mode)}
                  />
                  <XAxis type="number" {...axisProps(mode)} allowDecimals={false} />
                  <YAxis
                    type="category"
                    dataKey="label"
                    width={124}
                    {...axisProps(mode)}
                  />
                  <Tooltip
                    cursor={{ fill: "transparent" }}
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const step = payload[0].payload as Step;
                      const rows: Array<[string, string]> = [
                        ["Accounts", String(step.count)],
                        [
                          "From previous",
                          step.from_previous === null
                            ? "—"
                            : `${step.from_previous}%`,
                        ],
                        [
                          "From signup",
                          step.from_top === null ? "—" : `${step.from_top}%`,
                        ],
                        ["Lost here", String(step.dropped)],
                      ];
                      return (
                        <div className="glass-panel rounded-xl px-3 py-2 text-xs">
                          <p className="mb-1 font-medium text-foreground">
                            {step.label}
                          </p>
                          <div className="space-y-0.5">
                            {rows.map(([name, value]) => (
                              <div
                                key={name}
                                className="flex items-center justify-between gap-4"
                              >
                                <span className="text-muted-foreground">{name}</span>
                                <span className="font-medium tabular-nums text-foreground">
                                  {value}
                                </span>
                              </div>
                            ))}
                          </div>
                          <p className="mt-1.5 max-w-[220px] text-[11px] leading-snug text-muted-foreground">
                            {step.hint}
                          </p>
                        </div>
                      );
                    }}
                  />
                  <Bar dataKey="count" radius={[0, 4, 4, 0]} maxBarSize={26}>
                    {/* One measure, one hue. The stages are an ordered sequence,
                        not separate identities, so recolouring each would encode
                        a distinction that does not exist. */}
                    {steps.map((step) => (
                      <Cell key={step.key} fill={seriesColor(0, mode)} />
                    ))}
                    <LabelList
                      dataKey="count"
                      position="right"
                      className="fill-muted-foreground"
                      style={{ fontSize: 12 }}
                    />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
          </div>
        </ChartCard>

        {/* The table is not a fallback. It is how somebody reads exact rates,
            and it is what makes the chart's information available without
            relying on colour or on hovering. */}
        <ChartCard
          title="Step by step"
          description="Conversion is computed server-side, so every screen quotes the same denominator."
          loading={isLoading}
          error={error}
          height={220}
        >
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-muted-foreground">
                  <th className="py-2 pr-4 font-medium">Step</th>
                  <th className="py-2 pr-4 font-medium text-right">Accounts</th>
                  <th className="py-2 pr-4 font-medium text-right">From previous</th>
                  <th className="py-2 pr-4 font-medium text-right">From signup</th>
                  <th className="py-2 font-medium text-right">Lost here</th>
                </tr>
              </thead>
              <tbody>
                {steps.map((step) => (
                  <tr key={step.key} className="border-b border-border/60 last:border-0">
                    <td className="py-2.5 pr-4">
                      <span className="font-medium">{step.label}</span>
                      <span className="block text-xs text-muted-foreground">
                        {step.hint}
                      </span>
                    </td>
                    <td className="py-2.5 pr-4 text-right tabular-nums">
                      {step.count}
                    </td>
                    <td className="py-2.5 pr-4 text-right tabular-nums text-muted-foreground">
                      {step.from_previous === null ? "—" : `${step.from_previous}%`}
                    </td>
                    <td className="py-2.5 pr-4 text-right tabular-nums text-muted-foreground">
                      {step.from_top === null ? "—" : `${step.from_top}%`}
                    </td>
                    <td className="py-2.5 text-right tabular-nums text-muted-foreground">
                      {step.dropped || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </ChartCard>
    </div>
  );
}
