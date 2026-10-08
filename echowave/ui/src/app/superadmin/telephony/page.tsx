"use client";

import Link from "next/link";
import { useState } from "react";

import { Empty, PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { staffPost, useStaffData } from "@/lib/staff/data";
import { count, when, words } from "@/lib/staff/format";

type PhoneNumber = {
    id: number;
    address: string;
    label: string | null;
    country_code: string | null;
    organization_id: number | null;
    organization_name: string | null;
    telephony_configuration_id: number | null;
    configuration_name: string | null;
    provider: string | null;
    is_platform_managed: boolean;
    inbound_workflow_id: number | null;
    is_shared_outbound: boolean;
    is_active: boolean;
    status: string | null;
    provisioned_at: string | null;
};

type Config = { id: number; organization_id: number; name: string; provider: string; is_platform_managed: boolean; is_default_outbound: boolean; created_at: string | null };

/**
 * Phone numbers and telephony (phase 3): every number on the platform, which
 * workspace and carrier configuration it sits on, what it answers and whether
 * it is lent to every account as a shared outbound caller ID. Changes are
 * audited (admin_action_log); the managed-carrier switch stays on the
 * workspace's account page, where its KYC and billing context is.
 */
export default function TelephonyPage() {
    const [q, setQ] = useState("");
    const [search, setSearch] = useState("");
    const [onlyShared, setOnlyShared] = useState(false);
    const numbers = useStaffData<{ numbers: PhoneNumber[] }>("/api/v1/admin/telephony/phone-numbers", {
        ...(search ? { q: search } : {}),
        ...(onlyShared ? { shared: true } : {}),
    });
    const managed = useStaffData<{ configurations: Config[] }>("/api/v1/admin/telephony/configurations");
    const [busy, setBusy] = useState<number | null>(null);
    const [error, setError] = useState<string | null>(null);
    useReportFreshness(numbers.state, numbers.refreshedAt);

    const toggleShared = async (n: PhoneNumber) => {
        setBusy(n.id);
        setError(null);
        const r = await staffPost(`/api/v1/admin/telephony/phone-numbers/${n.id}/shared-outbound`, { shared: !n.is_shared_outbound });
        if (!r.ok) setError(`${n.address}: ${r.error}`);
        setBusy(null);
        void numbers.refresh();
    };

    return (
        <div className="space-y-4">
            <PageHeader
                title="Phone numbers and telephony"
                description="Every number across workspaces, its carrier configuration and what it answers. Lending a number as a shared outbound caller ID is audited."
                actions={
                    <>
                        <Button asChild variant="outline" className="min-h-11 md:min-h-9">
                            <Link href="/superadmin/telephony/shared-outbound">Shared outbound pool</Link>
                        </Button>
                        <Button asChild variant="outline" className="min-h-11 md:min-h-9">
                            <Link href="/superadmin/telephony/demo-agent">Demo agent</Link>
                        </Button>
                    </>
                }
            />
            <form
                className="flex flex-wrap items-end gap-2"
                onSubmit={(e) => {
                    e.preventDefault();
                    setSearch(q.trim());
                }}
            >
                <label className="flex w-full min-w-0 flex-col gap-1 text-sm sm:w-auto sm:max-w-xs sm:flex-1">
                    Number
                    <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="+91 or any digits" className="min-h-11 text-base md:min-h-9 md:text-sm" />
                </label>
                <label className="flex min-h-11 items-center gap-2 text-sm md:min-h-9">
                    <input type="checkbox" checked={onlyShared} onChange={(e) => setOnlyShared(e.target.checked)} /> Only shared outbound
                </label>
                <Button type="submit" className="min-h-11 md:min-h-9">
                    Search
                </Button>
            </form>
            {error && (
                <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                    {error}
                </p>
            )}
            <Panel title="Numbers" query={numbers}>
                {(d) =>
                    d.numbers.length === 0 ? (
                        <Empty>{search || onlyShared ? "No number matches." : "No phone numbers on this platform yet."}</Empty>
                    ) : (
                        <>
                            <ul className="divide-y divide-border text-sm md:hidden" data-testid="numbers-list">
                                {d.numbers.map((n) => (
                                    <li key={n.id} className="space-y-1 py-2">
                                        <p className="flex flex-wrap items-center gap-2">
                                            <span className="font-mono">{n.address}</span>
                                            <StateBadge state={n.is_active ? "active" : "disabled_by_policy"} label={words(n.status ?? (n.is_active ? "active" : "inactive"))} />
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            {n.organization_name ?? "No workspace"} · {n.provider ?? "no carrier"}
                                            {n.is_platform_managed ? " (managed)" : ""} · {n.inbound_workflow_id ? `answers with agent #${n.inbound_workflow_id}` : "no inbound agent"}
                                        </p>
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={busy === n.id}
                                            className="min-h-11"
                                            onClick={() => void toggleShared(n)}
                                        >
                                            {n.is_shared_outbound ? "Stop lending" : "Lend as shared outbound"}
                                        </Button>
                                    </li>
                                ))}
                            </ul>
                            <div className="hidden md:block">
                                <TableRegion label="Numbers">
                                    <table className="w-full min-w-[760px] text-sm">
                                        <thead>
                                            <tr className="text-left text-xs text-muted-foreground">
                                                <th className="py-1 font-normal">Number</th>
                                                <th className="py-1 font-normal">Workspace</th>
                                                <th className="py-1 font-normal">Carrier</th>
                                                <th className="py-1 font-normal">Inbound</th>
                                                <th className="py-1 font-normal">State</th>
                                                <th className="py-1 font-normal">Shared outbound</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-border">
                                            {d.numbers.map((n) => (
                                                <tr key={n.id}>
                                                    <td className="py-1.5 font-mono text-xs">
                                                        {n.address}
                                                        {n.label ? <span className="block font-sans text-muted-foreground">{n.label}</span> : null}
                                                    </td>
                                                    <td className="py-1.5">
                                                        {n.organization_id ? (
                                                            <Link className="underline underline-offset-2" href={`/superadmin/billing/accounts/${n.organization_id}`}>
                                                                {n.organization_name}
                                                            </Link>
                                                        ) : (
                                                            "None"
                                                        )}
                                                    </td>
                                                    <td className="py-1.5">
                                                        {n.provider ?? "None"}
                                                        {n.is_platform_managed && <span className="block text-xs text-muted-foreground">managed by Decibyl</span>}
                                                    </td>
                                                    <td className="py-1.5">{n.inbound_workflow_id ? `Agent #${n.inbound_workflow_id}` : "None"}</td>
                                                    <td className="py-1.5">
                                                        <StateBadge state={n.is_active ? "active" : "disabled_by_policy"} label={words(n.status ?? (n.is_active ? "active" : "inactive"))} />
                                                    </td>
                                                    <td className="py-1.5">
                                                        <Button size="sm" variant="outline" disabled={busy === n.id} className="min-h-8" onClick={() => void toggleShared(n)}>
                                                            {n.is_shared_outbound ? "Lent · stop" : "Lend"}
                                                        </Button>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </TableRegion>
                            </div>
                        </>
                    )
                }
            </Panel>
            <Panel title="Platform-managed carrier configurations" query={managed}>
                {(d) =>
                    d.configurations.length === 0 ? (
                        <Empty>No configuration is on Decibyl&apos;s carrier account. Mark one managed from its workspace&apos;s account page.</Empty>
                    ) : (
                        <ul className="divide-y divide-border text-sm">
                            {d.configurations.map((c) => (
                                <li key={c.id} className="flex flex-wrap items-center gap-2 py-1.5">
                                    <span className="font-medium">{c.name}</span>
                                    <span className="text-xs text-muted-foreground">
                                        {c.provider} · workspace {c.organization_id}
                                        {c.is_default_outbound ? " · default outbound" : ""} · since {when(c.created_at)}
                                    </span>
                                    <Link className="ml-auto text-xs underline underline-offset-2" href={`/superadmin/billing/accounts/${c.organization_id}`}>
                                        Account
                                    </Link>
                                </li>
                            ))}
                        </ul>
                    )
                }
            </Panel>
            <p className="text-xs text-muted-foreground">{numbers.data ? `${count(numbers.data.numbers.length)} shown (newest first, up to 100).` : null}</p>
        </div>
    );
}
