"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { CommandFlow } from "@/components/staff/CommandFlow";
import { Empty, Field, Fields, Panel, StateBadge } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { useAssistedAccessDialog } from "@/components/superadmin/AssistedAccessDialog";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { staffPost, useStaffData } from "@/lib/staff/data";
import { count, when, words } from "@/lib/staff/format";
import { impersonateAsSuperadmin } from "@/lib/utils";

type Detail = {
    user: {
        id: number;
        email: string | null;
        created_at: string | null;
        access_state: string;
        suspended_at: string | null;
        staff: string | null;
        mfa_enabled: boolean;
        email_verified: boolean;
        last_useful_outcome_at: string | null;
    };
    workspaces: Array<{ id: number; name: string; role: string; kind: string; selected: boolean; kyc_status: string | null; summary: WorkspaceSummary | null }>;
    invitation: { invite_id: number; door: string; redeemed_at: string | null } | null;
    assisted_access: { state: string; mode?: string; since?: string; ends_at?: string; by?: number; start_id?: number };
    connections: Array<{ toolkit: string; organization_id: number; state: string; since: string | null }>;
    limits: {
        state: string;
        reason?: string;
        allowances: Array<{ kind: string; used: number; limit: number; remaining: number }>;
        grants: Array<{ id: number; kind: string; extra: number; expires_at: string; reason: string }>;
    };
};

type WorkspaceSummary = {
    plan: string | null;
    plan_is_paid: boolean | null;
    trial_ends_at: string | null;
    balance_paise: number;
    spent_paise_28d: number;
    failed_tasks_28d: number;
    agents: number | null;
    channels_linked: number | null;
    flags: Array<{ name: string; enabled: boolean; source: string }>;
    recent_errors: Array<{ id: number; at: string | null; workflow_id: number | null; workflow_run_id: number | null }>;
    suspended_at: string | null;
};

type Ticket = { id: number; subject: string; status: string; severity: string; created_at: string; next_step: string; workspace_name: string | null };

/** Paise as rupees with two decimals; the console never rounds money. */
function rupees(paise: number): string {
    const sign = paise < 0 ? "-" : "";
    return `${sign}₹${(Math.abs(paise) / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

type Task = { id: number; organization_id: number; number: number | null; state: string; kind: string; has_evidence: boolean; created_at: string | null; finished_at: string | null };

const TABS = ["overview", "tasks", "support", "usage", "access"] as const;
type Tab = (typeof TABS)[number];

/**
 * User and workspace detail (screen 31). Identity and scope stay pinned at
 * the top so nobody acts on the wrong customer. Tasks show states and
 * times, never titles or messages. Grants and suspensions are commands with
 * a preview, a reason and an audit trail; role changes live elsewhere.
 */
export default function UserDetailPage() {
    const params = useParams<{ userId: string }>();
    const userId = Number(params?.userId);
    const { can, me } = useStaffConsole();
    const owner = me.roles.includes("owner");
    const { getAccessToken } = useAuth();
    const { ask, dialog } = useAssistedAccessDialog();
    const [accessError, setAccessError] = useState<string | null>(null);
    const detail = useStaffData<Detail>(Number.isFinite(userId) ? `/api/v1/admin/staff/users/${userId}` : null);
    const [tab, setTab] = useState<Tab>("overview");
    const [workspace, setWorkspace] = useState<number | null>(null);
    const [action, setAction] = useState<null | "suspend" | "unsuspend" | "allowance" | "workspace.suspend" | "workspace.unsuspend">(null);
    const [allowance, setAllowance] = useState({ kind: "model_turns", extra: 20, hours: 24 });
    const selected = workspace ?? detail.data?.workspaces.find((w) => w.selected)?.id ?? detail.data?.workspaces[0]?.id ?? null;
    const tasks = useStaffData<{ tasks: Task[] }>(
        tab === "tasks" && Number.isFinite(userId) ? `/api/v1/admin/staff/users/${userId}/tasks` : null,
        selected ? { organization_id: selected } : undefined,
    );
    const tickets = useStaffData<{ tickets: Ticket[] }>(
        tab === "support" && Number.isFinite(userId) ? "/api/v1/admin/support/tickets" : null,
        { requester_user_id: userId, status: "" },
    );
    useReportFreshness(detail.state, detail.refreshedAt);
    const current = detail.data?.workspaces.find((w) => w.id === selected) ?? null;

    const openAs = async (email: string | null) => {
        setAccessError(null);
        const choice = await ask(email ?? `user ${userId}`);
        if (!choice) return;
        try {
            const token = await getAccessToken();
            if (!token) throw new Error("Your session has expired. Sign in again.");
            await impersonateAsSuperadmin({ accessToken: token, userId, who: email ?? undefined, reason: choice.reason, mode: choice.mode, redirectPath: "/overview", openInNewTab: true });
            window.setTimeout(() => void detail.refresh(), 1500);
        } catch (err) {
            setAccessError(err instanceof Error ? err.message : "Could not open the account.");
        }
    };
    const endAccess = async () => {
        const r = await staffPost(`/api/v1/admin/staff/users/${userId}/assisted-access/end`, {});
        if (!r.ok) setAccessError(r.error);
        void detail.refresh();
    };

    return (
        <div className="space-y-4">
            {dialog}
            <Panel query={detail} skeletonHeight={80} className="sticky top-0 z-[5] bg-background lg:top-14">
                {(d) => (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-2" data-testid="pinned-identity">
                        <div className="min-w-0">
                            <h1 className="break-all text-xl font-semibold">{d.user.email ?? `User ${d.user.id}`}</h1>
                            <p className="text-xs text-muted-foreground">User {d.user.id} · joined {when(d.user.created_at)}</p>
                        </div>
                        <StateBadge state={d.user.access_state} />
                        <label className="flex items-center gap-2 text-sm">
                            <span className="text-muted-foreground">Workspace</span>
                            <select
                                value={selected ?? ""}
                                onChange={(e) => setWorkspace(Number(e.target.value))}
                                className="min-h-11 max-w-[14rem] rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm"
                            >
                                {d.workspaces.map((w) => (
                                    <option key={w.id} value={w.id}>
                                        {w.name} ({w.role})
                                    </option>
                                ))}
                            </select>
                        </label>
                    </div>
                )}
            </Panel>

            <div className="sm:hidden">
                <label className="flex flex-col gap-1 text-sm">
                    <span>Section</span>
                    <select value={tab} onChange={(e) => setTab(e.target.value as Tab)} className="min-h-11 rounded-md border border-input bg-background px-2 text-base">
                        {TABS.map((t) => (
                            <option key={t} value={t}>
                                {words(t)}
                            </option>
                        ))}
                    </select>
                </label>
            </div>
            <div role="tablist" aria-label="User sections" className="hidden flex-wrap gap-1 border-b border-border sm:flex">
                {TABS.map((t) => (
                    <button
                        key={t}
                        role="tab"
                        type="button"
                        aria-selected={tab === t}
                        onClick={() => setTab(t)}
                        className={`motion-m1 min-h-11 border-b-2 px-3 text-sm md:min-h-9 ${tab === t ? "border-foreground font-medium" : "border-transparent text-muted-foreground"}`}
                    >
                        {words(t)}
                    </button>
                ))}
            </div>

            <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
                <div className="min-w-0 space-y-4">
                    {tab === "overview" && (
                        <Panel title="Overview" query={detail}>
                            {(d) => (
                                <Fields>
                                    <Field label="Last useful outcome">{d.user.last_useful_outcome_at ? when(d.user.last_useful_outcome_at) : "None yet"}</Field>
                                    <Field label="Email verified">{d.user.email_verified ? "Yes" : "No"}</Field>
                                    <Field label="Two-step sign-in">{d.user.mfa_enabled ? "On" : "Off"}</Field>
                                    <Field label="Invitation">{d.invitation ? `Code #${d.invitation.invite_id} via ${d.invitation.door}, ${when(d.invitation.redeemed_at)}` : "No invitation on record"}</Field>
                                    <Field label="Workspaces">{count(d.workspaces.length)}</Field>
                                </Fields>
                            )}
                        </Panel>
                    )}
                    {tab === "overview" && (
                        <Panel title={current ? `Workspace: ${current.name}` : "Workspace"} query={detail}>
                            {() =>
                                !current || !current.summary ? (
                                    <Empty>This person is in no workspace.</Empty>
                                ) : (
                                    <div className="space-y-3 text-sm" data-testid="workspace-summary">
                                        <Fields>
                                            <Field label="Plan">
                                                {current.summary.plan ? words(current.summary.plan) : "Not recorded"}
                                                {current.summary.trial_ends_at ? ` (trial until ${when(current.summary.trial_ends_at)})` : ""}
                                            </Field>
                                            <Field label="Credit balance">
                                                <span className="tabular-nums">{rupees(current.summary.balance_paise)}</span>
                                            </Field>
                                            <Field label="Spent, 28 days">
                                                <span className="tabular-nums">{rupees(current.summary.spent_paise_28d)}</span>
                                            </Field>
                                            <Field label="Failed or unknown tasks, 28 days">{count(current.summary.failed_tasks_28d)}</Field>
                                            <Field label="Agents">{current.summary.agents === null ? "Unknown" : count(current.summary.agents)}</Field>
                                            <Field label="Channels linked">{current.summary.channels_linked === null ? "Unknown" : count(current.summary.channels_linked)}</Field>
                                            <Field label="State">
                                                {current.summary.suspended_at ? <StateBadge state="suspended" label={`Suspended ${when(current.summary.suspended_at)}`} /> : <StateBadge state="active" />}
                                            </Field>
                                        </Fields>
                                        <div>
                                            <h3 className="mb-1 font-medium">Flags that differ here</h3>
                                            {current.summary.flags.length === 0 ? (
                                                <Empty>None: this workspace sees every flag as everyone does.</Empty>
                                            ) : (
                                                <ul className="flex flex-wrap gap-1">
                                                    {current.summary.flags.map((f) => (
                                                        <li key={f.name}>
                                                            <StateBadge state={f.enabled ? "on" : "off"} label={`${f.name}: ${f.enabled ? "on" : "off"} (${words(f.source).toLowerCase()})`} />
                                                        </li>
                                                    ))}
                                                </ul>
                                            )}
                                        </div>
                                        <div>
                                            <h3 className="mb-1 font-medium">Recent errors</h3>
                                            {current.summary.recent_errors.length === 0 ? (
                                                <Empty>No agent has reported a failure here.</Empty>
                                            ) : (
                                                <ul className="divide-y divide-border">
                                                    {current.summary.recent_errors.map((e) => (
                                                        <li key={e.id} className="flex flex-wrap items-center gap-2 py-1.5">
                                                            <StateBadge state="failed" label="Could not do it" />
                                                            <span className="text-xs text-muted-foreground">
                                                                {e.workflow_id ? `agent #${e.workflow_id}` : "Decibyl"}
                                                                {e.workflow_run_id ? ` · run #${e.workflow_run_id}` : ""}
                                                            </span>
                                                            {owner && e.workflow_run_id ? (
                                                                <Link className="text-xs underline underline-offset-2" href={`/superadmin/billing/calls/${e.workflow_run_id}`}>
                                                                    Open run
                                                                </Link>
                                                            ) : null}
                                                            <span className="ml-auto text-xs">{when(e.at)}</span>
                                                        </li>
                                                    ))}
                                                </ul>
                                            )}
                                        </div>
                                        {owner && (
                                            <Link className="inline-flex min-h-11 items-center text-sm underline underline-offset-2 md:min-h-0" href={`/superadmin/billing/accounts/${current.id}`}>
                                                Open the workspace account (billing, credit, flags, telephony)
                                            </Link>
                                        )}
                                    </div>
                                )
                            }
                        </Panel>
                    )}
                    {tab === "tasks" && (
                        <Panel title="Tasks in this workspace" query={tasks}>
                            {(t) =>
                                t.tasks.length === 0 ? (
                                    <Empty>No tasks from this person here.</Empty>
                                ) : (
                                    <ul className="divide-y divide-border text-sm">
                                        {t.tasks.map((task) => (
                                            <li key={task.id} className="flex flex-wrap items-center gap-2 py-2">
                                                <Link href={`/superadmin/operations/trace/${task.id}`} className="min-h-11 font-mono text-xs underline underline-offset-2 md:min-h-0">
                                                    #{task.id}
                                                </Link>
                                                <StateBadge state={task.state} />
                                                <span className="text-xs text-muted-foreground">{words(task.kind)}</span>
                                                <span className="text-xs text-muted-foreground">{task.has_evidence ? "evidence recorded" : "no evidence"}</span>
                                                <span className="ml-auto text-xs">{when(task.finished_at ?? task.created_at)}</span>
                                            </li>
                                        ))}
                                    </ul>
                                )
                            }
                        </Panel>
                    )}
                    {tab === "support" && (
                        <Panel title="Support tickets" query={tickets} setupHint="The support inbox (support_inbox) is not switched on here.">
                            {(t) =>
                                t.tickets.length === 0 ? (
                                    <Empty>This person has not asked for help.</Empty>
                                ) : (
                                    <ul className="divide-y divide-border text-sm">
                                        {t.tickets.map((ticket) => (
                                            <li key={ticket.id} className="flex flex-wrap items-center gap-2 py-2">
                                                <Link href={`/superadmin/support/${ticket.id}`} className="min-h-11 font-mono text-xs underline underline-offset-2 md:min-h-0">
                                                    #{ticket.id}
                                                </Link>
                                                <span className="min-w-0 flex-1 break-words">{ticket.subject}</span>
                                                <StateBadge state={ticket.status} />
                                                <span className="text-xs text-muted-foreground">{words(ticket.severity)} · next: {ticket.next_step}</span>
                                                <span className="text-xs">{when(ticket.created_at)}</span>
                                            </li>
                                        ))}
                                    </ul>
                                )
                            }
                        </Panel>
                    )}
                    {tab === "usage" && (
                        <Panel title="Daily allowances" query={detail}>
                            {(d) =>
                                d.limits.state !== "ok" ? (
                                    <p className="flex items-center gap-2 text-sm">
                                        <StateBadge state={d.limits.state} /> {d.limits.reason}
                                    </p>
                                ) : (
                                    <div className="space-y-3 text-sm">
                                        <ul className="divide-y divide-border">
                                            {d.limits.allowances.map((a) => (
                                                <li key={a.kind} className="flex justify-between py-1.5">
                                                    <span>{words(a.kind)}</span>
                                                    <span className="tabular-nums">
                                                        {count(a.used)} of {count(a.limit)} today
                                                    </span>
                                                </li>
                                            ))}
                                        </ul>
                                        {d.limits.grants.length > 0 && (
                                            <ul className="text-xs text-muted-foreground">
                                                {d.limits.grants.map((g) => (
                                                    <li key={g.id}>
                                                        +{g.extra} {words(g.kind).toLowerCase()} until {when(g.expires_at)}: {g.reason}
                                                    </li>
                                                ))}
                                            </ul>
                                        )}
                                        {can("users.allowance") && (
                                            <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => setAction("allowance")}>
                                                Grant a temporary allowance
                                            </Button>
                                        )}
                                    </div>
                                )
                            }
                        </Panel>
                    )}
                    {tab === "access" && (
                        <Panel title="Access" query={detail}>
                            {(d) => (
                                <div className="space-y-3 text-sm">
                                    <ul className="divide-y divide-border">
                                        {d.workspaces.map((w) => (
                                            <li key={w.id} className="flex flex-wrap justify-between gap-2 py-1.5">
                                                <span>
                                                    {w.name} <span className="text-xs text-muted-foreground">({words(w.kind)})</span>
                                                </span>
                                                <span>{words(w.role)}</span>
                                                <span className="text-xs">KYC: {w.kyc_status ? words(w.kyc_status) : "not started"}</span>
                                            </li>
                                        ))}
                                    </ul>
                                    <div className="space-y-2 rounded-md border border-border p-3" data-testid="assisted-access">
                                        <p className="flex flex-wrap items-center gap-2">
                                            Assisted access:
                                            <StateBadge
                                                state={d.assisted_access.state === "off" ? "off" : "running"}
                                                label={
                                                    d.assisted_access.state === "off"
                                                        ? "Off"
                                                        : `${d.assisted_access.state === "read_only" ? "Read-only view" : "Impersonation"} open since ${when(d.assisted_access.since ?? null)} by staff #${d.assisted_access.by}`
                                                }
                                            />
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            Opens their account in a new tab for one hour with a reason, logged and bannered. A read-only view is refused every change by the server.
                                        </p>
                                        <div className="flex flex-wrap gap-2">
                                            {owner && !d.user.staff && (
                                                <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => void openAs(d.user.email)}>
                                                    View as or impersonate…
                                                </Button>
                                            )}
                                            {d.assisted_access.state !== "off" && can("users.suspend.request") && (
                                                <Button variant="destructive" className="min-h-11 md:min-h-9" onClick={() => void endAccess()}>
                                                    End it now
                                                </Button>
                                            )}
                                        </div>
                                        {accessError && (
                                            <p role="alert" className="text-xs text-[#772322] dark:text-red-300">
                                                {accessError}
                                            </p>
                                        )}
                                    </div>
                                    {d.user.staff ? (
                                        <p className="text-muted-foreground">Staff accounts are managed under Controls and audit.</p>
                                    ) : can("users.suspend.request") ? (
                                        <Button
                                            variant={d.user.access_state === "suspended" ? "outline" : "destructive"}
                                            className="min-h-11 md:min-h-9"
                                            onClick={() => setAction(d.user.access_state === "suspended" ? "unsuspend" : "suspend")}
                                        >
                                            {d.user.access_state === "suspended" ? "Restore access" : "Suspend account"}
                                        </Button>
                                    ) : null}
                                    {current && can("users.suspend.request") && (
                                        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
                                            <span>Workspace {current.name}:</span>
                                            <Button
                                                variant={current.summary?.suspended_at ? "outline" : "destructive"}
                                                className="min-h-11 md:min-h-9"
                                                onClick={() => setAction(current.summary?.suspended_at ? "workspace.unsuspend" : "workspace.suspend")}
                                            >
                                                {current.summary?.suspended_at ? "Restore workspace" : "Suspend workspace"}
                                            </Button>
                                        </div>
                                    )}
                                </div>
                            )}
                        </Panel>
                    )}
                    {action && detail.data && (
                        <section className="rounded-lg border border-border p-4" aria-label="Action">
                            {action === "allowance" && (
                                <div className="mb-3 grid gap-2 sm:grid-cols-3">
                                    <label className="flex flex-col gap-1 text-sm">
                                        Allowance
                                        <select
                                            value={allowance.kind}
                                            onChange={(e) => setAllowance({ ...allowance, kind: e.target.value })}
                                            className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm"
                                        >
                                            {["model_turns", "voice_minutes", "outbound_messages", "browser_minutes"].map((k) => (
                                                <option key={k} value={k}>
                                                    {words(k)}
                                                </option>
                                            ))}
                                        </select>
                                    </label>
                                    <label className="flex flex-col gap-1 text-sm">
                                        Extra per day
                                        <input type="number" min={1} max={10000} value={allowance.extra} onChange={(e) => setAllowance({ ...allowance, extra: Number(e.target.value) })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm" />
                                    </label>
                                    <label className="flex flex-col gap-1 text-sm">
                                        For hours
                                        <input type="number" min={1} max={744} value={allowance.hours} onChange={(e) => setAllowance({ ...allowance, hours: Number(e.target.value) })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm" />
                                    </label>
                                </div>
                            )}
                            <CommandFlow
                                key={`${action}-${JSON.stringify(allowance)}`}
                                command={
                                    action === "allowance"
                                        ? "allowance.grant"
                                        : action === "suspend"
                                          ? "user.suspend"
                                          : action === "unsuspend"
                                            ? "user.unsuspend"
                                            : action
                                }
                                target={
                                    action === "allowance"
                                        ? { user_id: userId, ...allowance }
                                        : action.startsWith("workspace.")
                                          ? { organization_id: selected }
                                          : { user_id: userId }
                                }
                                targetLabel={
                                    action.startsWith("workspace.")
                                        ? `${current?.name ?? "Workspace"} (workspace ${selected})`
                                        : `${detail.data.user.email ?? `User ${userId}`} (user ${userId})`
                                }
                                onDone={() => void detail.refresh()}
                                onCancel={() => setAction(null)}
                            />
                        </section>
                    )}
                </div>
                <aside className="min-w-0 space-y-4" aria-label="Connections and limits">
                    <Panel title="Connections" query={detail}>
                        {(d) =>
                            d.connections.length === 0 ? (
                                <Empty>No apps connected.</Empty>
                            ) : (
                                <ul className="space-y-1 text-sm">
                                    {d.connections.map((c) => (
                                        <li key={`${c.organization_id}-${c.toolkit}`} className="flex items-center justify-between gap-2">
                                            <span>{c.toolkit}</span>
                                            <StateBadge state={c.state} />
                                        </li>
                                    ))}
                                </ul>
                            )
                        }
                    </Panel>
                    <Panel title="Effective limits" query={detail}>
                        {(d) =>
                            d.limits.state !== "ok" ? (
                                <StateBadge state={d.limits.state} />
                            ) : (
                                <ul className="space-y-1 text-sm">
                                    {d.limits.allowances.map((a) => (
                                        <li key={a.kind} className="flex justify-between">
                                            <span>{words(a.kind)}</span>
                                            <span className="tabular-nums">{count(a.limit)}/day</span>
                                        </li>
                                    ))}
                                </ul>
                            )
                        }
                    </Panel>
                </aside>
            </div>
        </div>
    );
}
