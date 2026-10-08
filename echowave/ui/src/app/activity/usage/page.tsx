"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { getUsageByAgentApiV1OrganizationsUsageAgentsGet } from "@/client/sdk.gen";
import { BlobFace } from "@/components/brand/BlobFace";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { CALLS_TABS } from "@/components/layout/SectionTabs";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";
import { formatPaise } from "@/lib/billing/format";
import { formatTokens } from "@/lib/chatPresets";
import { cn } from "@/lib/utils";

type ModelLine = {
  slot: "llm" | "stt" | "tts";
  provider: string;
  model: string;
  tokens: number;
  units: number;
  cost_paise: number;
  agents?: number;
};
type AgentLine = {
  workflow_id: number;
  name: string;
  runs: Record<string, number>;
  total_runs: number;
  tokens: number;
  cost_paise: number;
  models: ModelLine[];
};
type Report = {
  agents: AgentLine[];
  models: ModelLine[];
  totals: { runs: number; tokens: number; cost_paise: number };
};

const RANGES = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

const SLOT_LABEL: Record<ModelLine["slot"], string> = { llm: "Brain", stt: "Hearing", tts: "Voice" };
const KIND_LABEL: Record<string, string> = { voice: "calls", text: "chats" };

/** What a model line measured, in its own unit: tokens for a brain, the
 *  receipt's units (seconds, characters) for speech. */
function amount(line: ModelLine): string {
  if (line.slot === "llm") return `${formatTokens(line.tokens)} tokens`;
  if (line.slot === "stt") {
    return line.units < 60 ? `${line.units}s heard` : `${Math.round(line.units / 60)} min heard`;
  }
  return `${formatTokens(line.units)} chars spoken`;
}

function runsLabel(runs: Record<string, number>): string {
  return Object.entries(runs)
    .sort(([, a], [, b]) => b - a)
    .map(([kind, n]) => `${n} ${KIND_LABEL[kind] ?? (n === 1 ? "run" : "runs")}`)
    .join(" · ");
}

/**
 * Activity -> Usage: each agent's runs, the models they ran on, and what that
 * cost. Beside Calls, whose rows open the transcript and recording of one run;
 * this is the same work added up.
 */
export default function UsageByAgentPage() {
  const { user, loading: authLoading } = useAuth();
  const [days, setDays] = useState(30);
  const [report, setReport] = useState<Report | null>(null);
  const [failed, setFailed] = useState(false);
  const ready = useRef(false);

  const load = useCallback(async (range: number) => {
    setReport(null);
    const res = await getUsageByAgentApiV1OrganizationsUsageAgentsGet({ query: { days: range } });
    if (res.error) {
      setFailed(true);
      return;
    }
    setFailed(false);
    setReport(res.data as unknown as Report);
  }, []);

  useEffect(() => {
    if (authLoading || !user) return;
    ready.current = true;
    void load(days);
  }, [authLoading, user, days, load]);

  return (
    <>
      <PageHeader
        tabs={CALLS_TABS}
        title="Usage"
        description="What each agent ran, on which models, and what it cost. Free during the beta."
        actions={
          <div className="flex gap-1 rounded-full bg-[var(--paper-2)] p-1" role="group" aria-label="Range">
            {RANGES.map((range) => (
              <button
                key={range.days}
                type="button"
                aria-pressed={days === range.days}
                onClick={() => setDays(range.days)}
                className={cn(
                  "h-7 rounded-full px-3 text-[13px]",
                  days === range.days ? "bg-[var(--v2-card)] shadow-sm" : "text-muted-foreground",
                )}
              >
                {range.label}
              </button>
            ))}
          </div>
        }
      />
      <PageBody className="max-w-4xl space-y-10">
        {failed && <p className="text-sm text-muted-foreground">Could not load usage. Refresh to try again.</p>}
        {!report && !failed && (
          <div className="space-y-3">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        )}
        {report && (
          <>
            <dl className="grid grid-cols-3 gap-3" data-testid="usage-totals">
              {[
                ["Runs", String(report.totals.runs)],
                ["Tokens", formatTokens(report.totals.tokens)],
                ["Cost", formatPaise(report.totals.cost_paise)],
              ].map(([label, value]) => (
                <div key={label} className="rounded-2xl bg-[var(--paper-2)] px-4 py-3">
                  <dt className="text-[13px] text-muted-foreground">{label}</dt>
                  <dd className="text-xl tabular-nums">{value}</dd>
                </div>
              ))}
            </dl>

            <section aria-labelledby="by-agent" className="space-y-2">
              <h2 id="by-agent" className="text-[13px] text-muted-foreground">
                By agent
              </h2>
              {report.agents.length === 0 ? (
                <p className="text-sm text-muted-foreground">Nothing ran in this range.</p>
              ) : (
                <ul className="divide-y divide-[var(--line)] border-y border-[var(--line)]" data-testid="usage-agents">
                  {report.agents.map((agent) => (
                    <li key={agent.workflow_id} className="py-3.5">
                      <div className="flex items-center gap-3">
                        <BlobFace seed={agent.workflow_id} size={28} />
                        <div className="min-w-0 flex-1">
                          <Link href={`/workflow/${agent.workflow_id}/thread`} className="text-[15px] hover:underline">
                            {agent.name}
                          </Link>
                          <div className="text-[13px] text-muted-foreground">{runsLabel(agent.runs) || "No runs"}</div>
                        </div>
                        <div className="text-right">
                          <div className="text-[15px] tabular-nums">{formatPaise(agent.cost_paise)}</div>
                          <div className="text-[13px] text-muted-foreground">{formatTokens(agent.tokens)} tokens</div>
                        </div>
                      </div>
                      {agent.models.length > 0 && (
                        <ul className="mt-2 flex flex-wrap gap-1.5 pl-10">
                          {agent.models.map((line) => (
                            <li
                              key={`${line.slot}-${line.provider}-${line.model}`}
                              className="rounded-full bg-[var(--paper-2)] px-2.5 py-1 text-xs"
                              title={`${SLOT_LABEL[line.slot]}: ${line.provider} ${line.model}`}
                            >
                              <span className="text-muted-foreground">{SLOT_LABEL[line.slot]}</span> {line.model || line.provider}
                              <span className="text-muted-foreground"> · {amount(line)}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {report.models.length > 0 && (
              <section aria-labelledby="by-model" className="space-y-2">
                <h2 id="by-model" className="text-[13px] text-muted-foreground">
                  By model
                </h2>
                <table className="w-full text-sm" data-testid="usage-models">
                  <thead>
                    <tr className="border-b border-[var(--line)] text-left text-[13px] text-muted-foreground">
                      <th className="py-2 font-normal">Model</th>
                      <th className="py-2 font-normal">Used for</th>
                      <th className="py-2 font-normal">Amount</th>
                      <th className="py-2 text-right font-normal">Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.models.map((line) => (
                      <tr key={`${line.slot}-${line.provider}-${line.model}`} className="border-b border-[var(--line)]">
                        <td className="py-2.5">
                          {line.model || "—"}
                          <span className="ml-1.5 text-xs text-muted-foreground">{line.provider}</span>
                        </td>
                        <td className="py-2.5 text-muted-foreground">
                          {SLOT_LABEL[line.slot]}
                          {line.agents ? ` · ${line.agents} agent${line.agents === 1 ? "" : "s"}` : ""}
                        </td>
                        <td className="py-2.5 tabular-nums text-muted-foreground">{amount(line)}</td>
                        <td className="py-2.5 text-right tabular-nums">{formatPaise(line.cost_paise)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
          </>
        )}
      </PageBody>
    </>
  );
}
