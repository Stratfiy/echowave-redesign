"use client";

import { ChevronDown, Plus } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  getWorkflowsSummaryApiV1WorkflowSummaryGet,
  listNumbersApiV1VerifiedNumbersGet,
  listPhoneNumbersApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersGet,
  listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet,
  updatePhoneNumberApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersPhoneNumberIdPut,
} from "@/client/sdk.gen";
import type { PhoneNumberResponse, TelephonyConfigurationListItem, VerifiedNumber } from "@/client/types.gen";
import { AgentAvatar } from "@/components/avatar/AgentAvatar";
import { faceOf } from "@/components/avatar/avatar";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { CarriersSection } from "@/components/telephony/CarriersSection";
import { type NumberRow, numberRows, type NumberUse } from "@/components/telephony/numberRows";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type Agent = { id: number; name: string };

const STATUS_LABEL: Record<NumberRow["status"], string> = {
  live: "Live",
  paused: "Paused",
  checking: "Checking",
  released: "Released",
};

/**
 * Settings -> Phone numbers: every number the workspace has, on one list,
 * each with a chip saying who uses it. Tap the chip to change who answers.
 * Bought numbers, your own carrier's numbers and your verified caller IDs
 * used to be three pages; carriers themselves are the section below.
 */
export default function PhoneNumbersPage() {
  const { user, loading: authLoading } = useAuth();
  const [configs, setConfigs] = useState<TelephonyConfigurationListItem[]>([]);
  const [numbers, setNumbers] = useState<Record<number, PhoneNumberResponse[]>>({});
  const [verified, setVerified] = useState<VerifiedNumber[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  // Parts of the list that did not load. A carrier whose numbers failed is
  // not a carrier with no numbers, and an empty agent list after a failure
  // is not "No agents yet" -- so each failure is named, not defaulted to [].
  const [missing, setMissing] = useState<string[]>([]);
  const [agentsFailed, setAgentsFailed] = useState(false);
  const started = useRef(false);

  const load = useCallback(async () => {
    const listed = await listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet();
    if (listed.error) {
      setState("failed");
      return;
    }
    const items = listed.data?.configurations ?? [];
    const failed: string[] = [];
    const pairs = await Promise.all(
      items.map(async (config) => {
        const res = await listPhoneNumbersApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersGet({
          path: { config_id: config.id },
        });
        if (res.error) failed.push(config.is_platform_managed ? "bought numbers" : config.name);
        return [config.id, res.data?.phone_numbers ?? []] as const;
      }),
    );
    const [verifiedRes, agentsRes] = await Promise.all([
      listNumbersApiV1VerifiedNumbersGet(),
      getWorkflowsSummaryApiV1WorkflowSummaryGet({ query: { status: "active" } }),
    ]);
    if (verifiedRes.error) failed.push("verified caller IDs");
    setConfigs(items);
    setNumbers(Object.fromEntries(pairs));
    setVerified((verifiedRes.data as VerifiedNumber[] | undefined) ?? []);
    setAgents((agentsRes.data ?? []).map((w) => ({ id: w.id, name: w.name })));
    setAgentsFailed(Boolean(agentsRes.error));
    setMissing(failed);
    setState("ready");
  }, []);

  useEffect(() => {
    if (authLoading || !user || started.current) return;
    started.current = true;
    void load();
  }, [authLoading, user, load]);

  const names = useMemo(() => new Map(agents.map((a) => [a.id, a.name])), [agents]);
  const rows = useMemo(
    () =>
      numberRows(configs, numbers, verified).map((row) => ({
        ...row,
        uses: row.uses.map((use) =>
          use.kind === "answers" || use.kind === "calls_back" ? { ...use, name: names.get(use.workflowId) ?? use.name } : use,
        ),
      })),
    [configs, numbers, verified, names],
  );

  const assign = async (row: NumberRow, workflowId: number | null) => {
    if (!row.configId || !row.phoneNumberId) return;
    const res = await updatePhoneNumberApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersPhoneNumberIdPut({
      path: { config_id: row.configId, phone_number_id: row.phoneNumberId },
      body: workflowId === null ? { clear_inbound_workflow: true } : { inbound_workflow_id: workflowId },
    });
    if (res.error) {
      toast.error(detailFromError(res.error, "Could not change who answers this number"));
      return;
    }
    toast.success(
      workflowId === null ? `Nobody answers ${row.address} now` : `${names.get(workflowId) ?? "The agent"} answers ${row.address} now`,
    );
    void load();
  };

  return (
    <>
      <PageHeader
        title="Phone numbers"
        description="Every number you have, and who uses it. Tap who answers a number to change it."
        actions={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button className="rounded-full">
                <Plus className="mr-1.5 h-4 w-4" /> Add a number
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuItem asChild>
                <Link href="/numbers">Buy a number</Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="#carriers">Bring your own carrier</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href="/verified-numbers">Verify a caller ID</Link>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />
      <PageBody className="max-w-4xl space-y-10">
        <section aria-label="Your numbers">
          {state === "loading" && (
            <div className="space-y-3">
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </div>
          )}
          {state === "failed" && <p className="text-sm text-muted-foreground">Could not load your numbers. Refresh to try again.</p>}
          {state === "ready" && (missing.length > 0 || agentsFailed) && (
            <p className="mb-3 rounded-2xl bg-[var(--paper-2)] px-4 py-3 text-sm" role="alert" data-testid="numbers-partial">
              {missing.length > 0 && <>Could not load {missing.join(", ")}, so this list may be missing numbers. </>}
              {agentsFailed && <>Could not load your agents, so who answers cannot be changed right now. </>}
              <button type="button" onClick={() => void load()} className="underline underline-offset-2">
                Try again
              </button>
            </p>
          )}
          {state === "ready" && rows.length === 0 && missing.length === 0 && (
            <div className="rounded-2xl bg-[var(--paper-2)] p-6 text-sm">
              <p className="font-medium">No number yet.</p>
              <p className="mt-1 text-muted-foreground">
                Your agents still work on chat, WhatsApp and the web. Add a number when one should take calls.
              </p>
            </div>
          )}
          {state === "ready" && rows.length > 0 && (
            <ul className="divide-y divide-[var(--line)] border-y border-[var(--line)]" data-testid="number-list">
              {rows.map((row) => (
                <li key={row.key} className="flex flex-col gap-3 py-3.5 sm:flex-row sm:items-center sm:gap-4">
                  <div className="w-56 shrink-0">
                    <div className="text-[15px] tabular-nums">{row.address}</div>
                    <div className="text-[13px] text-muted-foreground">
                      {row.type} ·{" "}
                      <span className={cn(row.status === "live" && "text-[var(--live)]", row.status === "checking" && "text-[var(--haldi)]")}>
                        {STATUS_LABEL[row.status]}
                      </span>
                    </div>
                  </div>
                  <div className="flex flex-1 flex-wrap gap-1.5">
                    {row.uses.map((use, i) => (
                      <UseChip
                        key={`${use.kind}-${i}`}
                        row={row}
                        use={use}
                        agents={agents}
                        agentsFailed={agentsFailed}
                        onAssign={assign}
                      />
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <CarriersSection onChanged={() => void load()} />
      </PageBody>
    </>
  );
}

const CHIP = "inline-flex h-[34px] items-center gap-1.5 rounded-full pl-1.5 pr-2.5 text-sm";

function UseChip({
  row,
  use,
  agents,
  agentsFailed,
  onAssign,
}: {
  row: NumberRow;
  use: NumberUse;
  agents: Agent[];
  agentsFailed: boolean;
  onAssign: (row: NumberRow, workflowId: number | null) => void;
}) {
  if (use.kind === "test_only") {
    return <span className={cn(CHIP, "bg-[var(--paper-2)] pl-3 text-muted-foreground")}>Test calls only</span>;
  }
  if (use.kind === "calls_out") {
    return <span className={cn(CHIP, "bg-[var(--paper-2)] pl-3")}>Calls go out from here</span>;
  }
  if (use.kind === "calls_back") {
    return (
      <span className={cn(CHIP, "bg-[var(--paper-2)]")}>
        <AgentAvatar avatar={faceOf(use.workflowId, null)} size={22} animate={false} />
        {use.name}
        <span className="text-xs text-muted-foreground">calls back</span>
      </span>
    );
  }
  // Never offer a reassignment built on an agent list that failed to load.
  const changeable = Boolean(row.configId && row.phoneNumberId) && !agentsFailed;
  const label = use.kind === "answers" ? `${use.name} answers ${row.address}. Change who answers` : `Nobody answers ${row.address}. Choose who answers`;
  const chip =
    use.kind === "answers" ? (
      <>
        <AgentAvatar avatar={faceOf(use.workflowId, null)} size={22} animate={false} />
        {use.name}
        <span className="text-xs text-muted-foreground">answers</span>
      </>
    ) : (
      <span className="pl-1.5 text-muted-foreground">Not in use</span>
    );
  if (!changeable) return <span className={cn(CHIP, "bg-[var(--paper-2)]")}>{chip}</span>;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={label}
          className={cn(
            CHIP,
            "transition-colors",
            use.kind === "answers" ? "bg-[var(--paper-2)] hover:bg-[var(--line)]" : "border border-dashed border-black/20 bg-transparent hover:bg-[var(--paper-2)] dark:border-white/20",
          )}
        >
          {chip}
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 w-60 overflow-y-auto">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Who answers this number?</DropdownMenuLabel>
        {agents.map((agent) => (
          <DropdownMenuItem key={agent.id} onClick={() => onAssign(row, agent.id)} className="gap-2.5">
            <AgentAvatar avatar={faceOf(agent.id, null)} size={22} animate={false} />
            {agent.name}
          </DropdownMenuItem>
        ))}
        {agents.length === 0 && <DropdownMenuItem disabled>No agents yet</DropdownMenuItem>}
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => onAssign(row, null)} disabled={use.kind === "unused"}>
          Nobody
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
