"use client";

import { Check, Minus } from "lucide-react";
import { useState } from "react";

import { CommandFlow } from "@/components/staff/CommandFlow";
import { PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { useStaffData } from "@/lib/staff/data";
import { words } from "@/lib/staff/format";

type Roles = {
    matrix: { roles: string[]; grantable: string[]; capabilities: Array<{ name: string; label: string; roles: string[] }> };
    members: Array<{ user_id: number; email: string | null; tier: string; mfa_enabled: boolean; tier_roles: string[]; grants: Array<{ id: number; role: string; reason: string; created_at: string }> }>;
    grants_enabled: boolean;
};

/**
 * Staff roles (screen 44): the explicit capability matrix, and who holds
 * what. A grant or revocation previews the exact capabilities it adds or
 * removes before it is asked for. On a phone the matrix becomes one role
 * at a time instead of a squeezed grid.
 */
export default function RolesPage() {
    const query = useStaffData<Roles>("/api/v1/admin/staff/roles");
    const [focusRole, setFocusRole] = useState("owner");
    const [change, setChange] = useState<{ command: "role.grant" | "role.revoke"; user_id: number; role: string; email: string | null } | null>(null);
    const [grantRole, setGrantRole] = useState<Record<number, string>>({});
    useReportFreshness(query.state, query.refreshedAt);

    return (
        <div className="space-y-4">
            <PageHeader title="Staff roles" description="Owner manages roles and budgets; support handles permitted customer cases; operations runs runbooks; finance handles refunds; quality manages evaluation datasets." />
            <Panel title="Capability matrix" query={query}>
                {(d) => (
                    <>
                        <div className="md:hidden">
                            <label className="flex flex-col gap-1 text-sm">
                                Role
                                <select value={focusRole} onChange={(e) => setFocusRole(e.target.value)} className="min-h-11 rounded-md border border-input bg-background px-2 text-base">
                                    {d.matrix.roles.map((r) => (
                                        <option key={r} value={r}>
                                            {words(r)}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <ul className="mt-3 space-y-1 text-sm">
                                {d.matrix.capabilities.map((c) => (
                                    <li key={c.name} className="flex items-start gap-2">
                                        {c.roles.includes(focusRole) ? <Check aria-label="Yes" className="mt-0.5 h-4 w-4 shrink-0" /> : <Minus aria-label="No" className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />}
                                        <span className={c.roles.includes(focusRole) ? "" : "text-muted-foreground"}>{c.label}</span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                        <div className="hidden md:block">
                            <TableRegion label="Capability matrix">
                                <table className="w-full text-sm" data-testid="matrix">
                                    <thead className="text-left text-xs text-muted-foreground">
                                        <tr>
                                            <th className="py-1 font-normal">Capability</th>
                                            {d.matrix.roles.map((r) => (
                                                <th key={r} className="px-2 py-1 text-center font-normal">
                                                    {words(r)}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {d.matrix.capabilities.map((c) => (
                                            <tr key={c.name} className="border-t border-border">
                                                <td className="py-1">{c.label}</td>
                                                {d.matrix.roles.map((r) => (
                                                    <td key={r} className="px-2 py-1 text-center">
                                                        {c.roles.includes(r) ? <Check aria-label={`${r}: yes`} className="mx-auto h-4 w-4" /> : <span className="sr-only">{r}: no</span>}
                                                    </td>
                                                ))}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </TableRegion>
                        </div>
                    </>
                )}
            </Panel>
            <Panel title="Staff" query={query}>
                {(d) => (
                    <div className="space-y-2">
                        {!d.grants_enabled && (
                            <p className="flex items-center gap-2 text-sm">
                                <StateBadge state="disabled_by_policy" /> Console roles beyond the staff tier are off (staff_roles). Only owner and support apply.
                            </p>
                        )}
                        <ul className="divide-y divide-border text-sm">
                            {d.members.map((m) => (
                                <li key={m.user_id} className="space-y-2 py-2">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="break-all font-medium">{m.email ?? `User ${m.user_id}`}</span>
                                        <span className="text-xs text-muted-foreground">tier {m.tier}</span>
                                        {!m.mfa_enabled && <StateBadge state="degraded" label="No two-step sign-in" />}
                                        {m.tier_roles.map((r) => (
                                            <StateBadge key={r} state="ok" label={words(r)} />
                                        ))}
                                        {m.grants.map((g) => (
                                            <span key={g.id} className="inline-flex items-center gap-1">
                                                <StateBadge state="active" label={words(g.role)} />
                                                {d.grants_enabled && (
                                                    <Button size="sm" variant="ghost" className="min-h-11 md:min-h-7" onClick={() => setChange({ command: "role.revoke", user_id: m.user_id, role: g.role, email: m.email })}>
                                                        Revoke
                                                    </Button>
                                                )}
                                            </span>
                                        ))}
                                    </div>
                                    {d.grants_enabled && (
                                        <div className="flex flex-wrap items-center gap-2">
                                            <select
                                                aria-label={`Role to grant ${m.email ?? m.user_id}`}
                                                value={grantRole[m.user_id] ?? ""}
                                                onChange={(e) => setGrantRole({ ...grantRole, [m.user_id]: e.target.value })}
                                                className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-8 md:text-xs"
                                            >
                                                <option value="">Add a role…</option>
                                                {d.matrix.grantable
                                                    .filter((r) => !m.tier_roles.includes(r) && !m.grants.some((g) => g.role === r))
                                                    .map((r) => (
                                                        <option key={r} value={r}>
                                                            {words(r)}
                                                        </option>
                                                    ))}
                                            </select>
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="min-h-11 md:min-h-8"
                                                disabled={!grantRole[m.user_id]}
                                                onClick={() => setChange({ command: "role.grant", user_id: m.user_id, role: grantRole[m.user_id], email: m.email })}
                                            >
                                                Preview
                                            </Button>
                                        </div>
                                    )}
                                </li>
                            ))}
                        </ul>
                    </div>
                )}
            </Panel>
            {change && (
                <section className="rounded-lg border border-border p-4" aria-label="Role change">
                    <CommandFlow
                        key={`${change.command}-${change.user_id}-${change.role}`}
                        command={change.command}
                        target={{ user_id: change.user_id, role: change.role }}
                        targetLabel={`${change.email ?? `User ${change.user_id}`}: ${change.role}`}
                        onDone={() => void query.refresh()}
                        onCancel={() => setChange(null)}
                    />
                </section>
            )}
        </div>
    );
}
