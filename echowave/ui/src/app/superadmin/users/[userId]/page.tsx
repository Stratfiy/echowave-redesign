"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { CommandFlow } from "@/components/staff/CommandFlow";
import { Empty, Field, Fields, Panel, StateBadge } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { useStaffData } from "@/lib/staff/data";
import { count, when, words } from "@/lib/staff/format";

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
    workspaces: Array<{ id: number; name: string; role: string; kind: string; selected: boolean; kyc_status: string | null }>;
    invitation: { invite_id: number; door: string; redeemed_at: string | null } | null;
    assisted_access: string;
    connections: Array<{ toolkit: string; organization_id: number; state: string; since: string | null }>;
    limits: {
        state: string;
        reason?: string;
        allowances: Array<{ kind: string; used: number; limit: number; remaining: number }>;
        grants: Array<{ id: number; kind: string; extra: number; expires_at: string; reason: string }>;
    };
};

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
    const { can } = useStaffConsole();
    const detail = useStaffData<Detail>(Number.isFinite(userId) ? `/api/v1/admin/staff/users/${userId}` : null);
    const [tab, setTab] = useState<Tab>("overview");
    const [workspace, setWorkspace] = useState<number | null>(null);
    const [action, setAction] = useState<null | "suspend" | "unsuspend" | "allowance">(null);
    const [allowance, setAllowance] = useState({ kind: "model_turns", extra: 20, hours: 24 });
    const selected = workspace ?? detail.data?.workspaces.find((w) => w.selected)?.id ?? detail.data?.workspaces[0]?.id ?? null;
    const tasks = useStaffData<{ tasks: Task[] }>(
        tab === "tasks" && Number.isFinite(userId) ? `/api/v1/admin/staff/users/${userId}/tasks` : null,
        selected ? { organization_id: selected } : undefined,
    );
    useReportFreshness(detail.state, detail.refreshedAt);

    return (
        <div className="space-y-4">
            <Panel query={detail} skeletonHeight={80} className="sticky top-14 z-[5] bg-background">
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
                        <section className="rounded-lg border border-border p-4 text-sm">
                            <h2 className="mb-2 text-base font-semibold">Support</h2>
                            <p className="flex items-center gap-2">
                                <StateBadge state="needs_setup" /> Tickets for this person appear here once the support stream lands.
                            </p>
                        </section>
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
                                    <p>Assisted access: {words(d.assisted_access)}. It is the separate, audited impersonation flow and is never turned on from here.</p>
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
                                command={action === "allowance" ? "allowance.grant" : action === "suspend" ? "user.suspend" : "user.unsuspend"}
                                target={action === "allowance" ? { user_id: userId, ...allowance } : { user_id: userId }}
                                targetLabel={`${detail.data.user.email ?? `User ${userId}`} (user ${userId})`}
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
