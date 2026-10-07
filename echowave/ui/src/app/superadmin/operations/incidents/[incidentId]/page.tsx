"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { CommandFlow } from "@/components/staff/CommandFlow";
import { OpsCommand } from "@/components/staff/OpsCommand";
import { Field, Fields, Panel, StateBadge } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { useStaffData } from "@/lib/staff/data";
import { when, words } from "@/lib/staff/format";

type Incident = {
    id: number;
    title: string;
    impact: string;
    severity: string;
    state: string;
    environment: string;
    owner_user_id: number | null;
    links: { release?: string | null; providers?: string[]; support_cases?: number[]; ops_commands?: number[] };
    revision: number;
    opened_at: string;
    resolved_at: string | null;
    steps: Array<{ sequence: number; kind: string; summary: string; outcome: string | null; ops_command_id: number | null; actor_user_id: number; at: string }>;
};

type Draft = { kind: string; summary: string; outcome: string; ops_command_id: string };

const KINDS = ["preflight", "approval", "execution", "verification", "note"];

/**
 * Incident and approved runbook (screen 41): impact and owner in the
 * header; a vertical stepper of preflight, approvals, execution and
 * verification; links to the release, providers, support cases and ops
 * commands beside it. Execution is an approved ops command, never a
 * terminal; resolving needs a passed verification after it.
 */
export default function IncidentPage() {
    const params = useParams<{ incidentId: string }>();
    const { can } = useStaffConsole();
    const query = useStaffData<Incident>(`/api/v1/admin/staff/incidents/${params?.incidentId}`);
    const [step, setStep] = useState<Draft | null>(null);
    const [resolving, setResolving] = useState(false);
    const [runbook, setRunbook] = useState<string | null>(null);
    useReportFreshness(query.state, query.refreshedAt);
    const manage = can("incidents.manage");

    return (
        <div className="space-y-4">
            <Panel query={query} className="sticky top-0 z-[5] bg-background lg:top-14">
                {(i) => (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <h1 className="text-xl font-semibold">
                            #{i.id} {i.title}
                        </h1>
                        <StateBadge state={i.state} />
                        <span className="text-xs uppercase">{i.severity}</span>
                        <span className="w-full text-sm">Impact: {i.impact}</span>
                        <span className="text-xs text-muted-foreground">
                            Owner: user {i.owner_user_id ?? "none"} · {i.environment} · opened {when(i.opened_at)}
                        </span>
                    </div>
                )}
            </Panel>
            <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
                <Panel title="Runbook" query={query}>
                    {(i) => (
                        <div className="space-y-4">
                            {i.steps.length === 0 ? (
                                <p className="text-sm text-muted-foreground">No steps yet. Start with a preflight check.</p>
                            ) : (
                                <ol className="relative space-y-3 border-l border-border pl-4" aria-label="Runbook steps">
                                    {i.steps.map((s) => (
                                        <li key={s.sequence} className="text-sm" data-kind={s.kind}>
                                            <span aria-hidden className="absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full bg-muted-foreground" />
                                            <span className="font-medium">
                                                {s.sequence}. {words(s.kind)}
                                            </span>{" "}
                                            {s.outcome && <StateBadge state={s.outcome} />}
                                            <span className="block">{s.summary}</span>
                                            <span className="text-xs text-muted-foreground">
                                                user {s.actor_user_id} · {when(s.at)}
                                                {s.ops_command_id && ` · ops command #${s.ops_command_id}`}
                                            </span>
                                        </li>
                                    ))}
                                </ol>
                            )}
                            {manage && i.state !== "resolved" && (
                                <div className="flex flex-wrap gap-2">
                                    <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => setStep({ kind: "preflight", summary: "", outcome: "passed", ops_command_id: "" })}>
                                        Add a step
                                    </Button>
                                    <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => setRunbook("workers.drain")}>
                                        Run an approved command…
                                    </Button>
                                    <Button className="min-h-11 md:min-h-9" onClick={() => setResolving(true)}>
                                        Resolve…
                                    </Button>
                                </div>
                            )}
                            {step && (
                                <section className="space-y-2 rounded-md border border-border p-3" aria-label="New step">
                                    <div className="grid gap-2 sm:grid-cols-2">
                                        <label className="flex flex-col gap-1 text-sm">
                                            Kind
                                            <select value={step.kind} onChange={(e) => setStep({ ...step, kind: e.target.value })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm">
                                                {KINDS.map((k) => (
                                                    <option key={k} value={k}>
                                                        {words(k)}
                                                    </option>
                                                ))}
                                            </select>
                                        </label>
                                        <label className="flex flex-col gap-1 text-sm">
                                            Outcome
                                            <select value={step.outcome} onChange={(e) => setStep({ ...step, outcome: e.target.value })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm">
                                                {["passed", "failed", "pending", "unknown", ""].map((o) => (
                                                    <option key={o} value={o}>
                                                        {o ? words(o) : "None (note)"}
                                                    </option>
                                                ))}
                                            </select>
                                        </label>
                                        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
                                            What was done or checked
                                            <input value={step.summary} onChange={(e) => setStep({ ...step, summary: e.target.value })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm" />
                                        </label>
                                        {step.kind === "execution" && (
                                            <label className="flex flex-col gap-1 text-sm">
                                                Ops command id
                                                <input inputMode="numeric" value={step.ops_command_id} onChange={(e) => setStep({ ...step, ops_command_id: e.target.value.replace(/\D/g, "") })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm" />
                                            </label>
                                        )}
                                    </div>
                                    {step.summary.trim().length >= 2 && (
                                        <CommandFlow
                                            key={JSON.stringify(step) + i.revision}
                                            command="incident.step"
                                            target={{
                                                incident_id: i.id,
                                                revision: i.revision,
                                                kind: step.kind,
                                                summary: step.summary,
                                                ...(step.outcome ? { outcome: step.outcome } : {}),
                                                ...(step.ops_command_id ? { ops_command_id: Number(step.ops_command_id) } : {}),
                                            }}
                                            targetLabel={`incident #${i.id}`}
                                            onDone={() => {
                                                setStep(null);
                                                void query.refresh();
                                            }}
                                            onCancel={() => setStep(null)}
                                        />
                                    )}
                                </section>
                            )}
                            {runbook && (
                                <section className="space-y-2 rounded-md border border-border p-3" aria-label="Approved command">
                                    <label className="flex max-w-xs flex-col gap-1 text-sm">
                                        Command
                                        <select value={runbook} onChange={(e) => setRunbook(e.target.value)} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm">
                                            {["workers.drain", "workers.restart", "queue.inspect", "backups.status", "deployments.status"].map((c) => (
                                                <option key={c} value={c}>
                                                    {c}
                                                </option>
                                            ))}
                                        </select>
                                    </label>
                                    <OpsCommand key={runbook} command={runbook} target={runbook.startsWith("workers.") ? { group: "worker" } : {}} targetLabel={`${i.environment} · incident #${i.id}`} />
                                    <p className="text-xs text-muted-foreground">Record the command&apos;s id as an execution step once it has run, then verify.</p>
                                </section>
                            )}
                            {resolving && (
                                <CommandFlow
                                    key={`resolve-${i.revision}`}
                                    command="incident.state"
                                    target={{ incident_id: i.id, revision: i.revision, state: "resolved" }}
                                    targetLabel={`incident #${i.id}`}
                                    effect="Resolves only if a passed verification follows the last execution step."
                                    onDone={() => {
                                        setResolving(false);
                                        void query.refresh();
                                    }}
                                    onCancel={() => setResolving(false)}
                                />
                            )}
                        </div>
                    )}
                </Panel>
                <Panel title="Linked" query={query}>
                    {(i) => (
                        <Fields>
                            <Field label="Release">{i.links.release ?? "—"}</Field>
                            <Field label="Providers">{i.links.providers?.length ? i.links.providers.join(", ") : "—"}</Field>
                            <Field label="Support cases">{i.links.support_cases?.length ? i.links.support_cases.map((c) => `#${c}`).join(", ") : "—"}</Field>
                            <Field label="Ops commands">{i.links.ops_commands?.length ? i.links.ops_commands.map((c) => `#${c}`).join(", ") : "—"}</Field>
                            <Field label="Other">
                                <Link href="/superadmin/operations?tab=infrastructure" className="inline-flex min-h-11 items-center underline md:min-h-0">
                                    Infrastructure
                                </Link>
                            </Field>
                        </Fields>
                    )}
                </Panel>
            </div>
        </div>
    );
}
