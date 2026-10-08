"use client";

/**
 * What Settings -> Models adds with `model_inheritance` (screen 26): for each
 * slot, where its value comes from, whether it can actually run here (never
 * "ready" on a guess), its configured fallback and whether Sarvam is the one
 * speaking; and below, each agent's own state -- "Inherits workspace" or the
 * override it runs.
 */

import { CheckCircle2, CircleAlert, Lock } from "lucide-react";

import { cn } from "@/lib/utils";

export type InheritanceExtras = {
    key: string;
    label: string;
    source: "platform" | "workspace";
    readiness: "ready" | "missing_credential" | "needs_setup" | "managed";
    readiness_reason: string;
    revision: string;
    fallback: string[];
    provider?: string | null;
    sarvam?: { in_use: boolean; offered: boolean };
};

export type AgentOverride = {
    workflow_id: number;
    name: string;
    slots: Record<string, { inherits: boolean; label: string }>;
};

const READINESS: Record<InheritanceExtras["readiness"], { label: string; tone: string; Icon: typeof CheckCircle2 }> = {
    ready: { label: "Ready", tone: "text-[#075A39] dark:text-emerald-300", Icon: CheckCircle2 },
    missing_credential: { label: "Needs your key", tone: "text-[#705500] dark:text-amber-300", Icon: CircleAlert },
    needs_setup: { label: "Needs setup", tone: "text-[#705500] dark:text-amber-300", Icon: CircleAlert },
    managed: { label: "Managed elsewhere", tone: "text-muted-foreground", Icon: Lock },
};

export function InheritanceDetail({ slot }: { slot: InheritanceExtras }) {
    const ready = READINESS[slot.readiness] ?? READINESS.needs_setup;
    return (
        <div className="mt-1.5 flex flex-col gap-1 text-xs" data-testid={`inheritance-${slot.key}`} data-readiness={slot.readiness}>
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="rounded-[var(--radius-pill)] border border-border px-1.5 text-muted-foreground">
                    {slot.source === "workspace" ? "This workspace's choice" : "Decibyl's default"}
                </span>
                <span className={cn("inline-flex items-center gap-1", ready.tone)}>
                    <ready.Icon aria-hidden className="h-3.5 w-3.5" /> {ready.label}
                </span>
            </p>
            {slot.readiness !== "ready" && <p className="text-muted-foreground">{slot.readiness_reason}</p>}
            {slot.fallback.length > 0 && <p className="text-muted-foreground">If it fails: {slot.fallback.join(", then ")}</p>}
            {slot.sarvam && (slot.key === "stt" || slot.key === "tts") && (
                <p className="text-muted-foreground">
                    {slot.sarvam.in_use
                        ? "Sarvam is the one running this."
                        : slot.sarvam.offered
                          ? "Sarvam is available for this; it is not the one running now."
                          : "Sarvam is not available for this here."}
                </p>
            )}
        </div>
    );
}

const SLOT_LABEL: Record<string, string> = { llm: "Brain", stt: "Hearing", tts: "Voice" };

export function InheritanceAgents({ agents, precedence }: { agents: AgentOverride[]; precedence: string[] }) {
    return (
        <section aria-label="Agents" className="mt-8" data-testid="inheritance-agents">
            <h2 className="text-[15px] font-medium">Agents</h2>
            <p className="text-[13px] text-muted-foreground">
                Each agent inherits the workspace unless it has its own. Changes apply to new calls and chats; one in progress keeps
                what it started with.
            </p>
            {precedence.length > 0 && (
                <ol className="mt-2 list-decimal pl-5 text-xs text-muted-foreground">
                    {precedence.map((line) => (
                        <li key={line}>{line}</li>
                    ))}
                </ol>
            )}
            {agents.length === 0 ? (
                <p className="mt-3 text-sm text-muted-foreground">No agents yet.</p>
            ) : (
                <ul className="mt-3 divide-y divide-[var(--line)] border-y border-[var(--line)]">
                    {agents.map((agent) => (
                        <li key={agent.workflow_id} className="flex flex-col gap-1 py-3 sm:flex-row sm:items-start sm:justify-between">
                            <span className="min-w-0 break-words text-sm">{agent.name}</span>
                            <dl className="grid grid-cols-[5rem_1fr] gap-x-2 text-xs sm:text-right">
                                {Object.entries(agent.slots).map(([key, slot]) => (
                                    <div key={key} className="contents">
                                        <dt className="text-muted-foreground sm:text-right">{SLOT_LABEL[key] ?? key}</dt>
                                        <dd className={slot.inherits ? "text-muted-foreground" : "font-medium"}>{slot.label}</dd>
                                    </div>
                                ))}
                            </dl>
                        </li>
                    ))}
                </ul>
            )}
        </section>
    );
}
