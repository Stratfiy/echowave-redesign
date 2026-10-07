"use client";

import Link from "next/link";
import { useState } from "react";

import { Empty, PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { staffPost, useStaffData } from "@/lib/staff/data";
import { when, words } from "@/lib/staff/format";

type KeyRow = {
    component: string;
    provider: string;
    environment: string;
    masked_key: string | null;
    label: string | null;
    owner_user_id: number | null;
    health: string;
    validated_at: string | null;
    last_changed_at: string | null;
    last_check_reason: string | null;
    open_rotation: number | null;
    open_rotation_state: string | null;
};
type Rotation = {
    id: number;
    component: string;
    provider: string;
    environment: string;
    state: string;
    staged_key: string;
    previous_key: string | null;
    can_revert: boolean;
    reason: string;
    reason_code: string | null;
    timeline: Record<string, string | null>;
    next_steps: string[];
    provider_revocation_reminder: string | null;
};

const HEALTH: Record<string, string> = { valid: "healthy", rejected: "failed", unchecked: "unknown", disabled: "disabled_by_policy" };

/**
 * Providers and secret lifecycle (screen 42). The table is masked metadata
 * only. Adding or replacing a key stages it through the ops stream's
 * lifecycle (validate, activate, refresh consumers, verify, revoke the old
 * one) and the progress stays on screen; no key value is ever returned, put
 * in a URL, copied or shown in a toast. The form field is cleared the
 * moment it is sent.
 */
export default function ProvidersPage() {
    const { can, me } = useStaffConsole();
    const query = useStaffData<{ keys: KeyRow[]; rotations: Rotation[] }>("/api/v1/admin/ops/credentials");
    const [form, setForm] = useState({ component: "llm", provider: "", reason: "", label: "" });
    const [secret, setSecret] = useState("");
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState<string | null>(null);
    useReportFreshness(query.state, query.refreshedAt);
    const rotate = can("providers.rotate");

    async function stage() {
        setBusy(true);
        setMessage(null);
        const value = secret;
        setSecret("");
        const r = await staffPost<Rotation>("/api/v1/admin/ops/credentials/rotations", { ...form, label: form.label || null, api_key: value });
        setBusy(false);
        setMessage(r.ok ? `Staged as rotation #${r.data.id}. Validate it next.` : r.error);
        await query.refresh();
    }

    async function step(id: number, name: string) {
        setBusy(true);
        setMessage(null);
        const r = await staffPost<Rotation>(`/api/v1/admin/ops/credentials/rotations/${id}/${name}`, { reason: `${name} from the staff console` });
        setBusy(false);
        setMessage(r.ok ? `Rotation #${id}: ${words(r.data.state)}.` : r.error);
        await query.refresh();
    }

    return (
        <div className="space-y-4" data-sensitive="true">
            <PageHeader
                title="Providers and secrets"
                description="Masked metadata only. Storing a key is not the same as the provider issuing it: issue and revoke keys on the provider's own dashboard."
                actions={
                    me.roles.includes("owner") ? (
                        <Link href="/superadmin/provider-keys" className="inline-flex min-h-11 items-center rounded-md border border-border px-3 text-sm md:min-h-9">
                            Existing provider keys screen
                        </Link>
                    ) : null
                }
            />
            <Panel title="Keys" query={query} setupHint="The key lifecycle is the ops stream's service (/admin/ops/credentials), not on this build yet. The existing provider keys screen still works for owners.">
                {(d) =>
                    d.keys.length === 0 ? (
                        <Empty>No platform keys are stored here.</Empty>
                    ) : (
                        <TableRegion label="Provider keys">
                            <table className="w-full min-w-[640px] text-sm">
                                <thead className="text-left text-xs text-muted-foreground">
                                    <tr>
                                        <th className="py-1 font-normal">Provider</th>
                                        <th className="py-1 font-normal">Key</th>
                                        <th className="py-1 font-normal">Health</th>
                                        <th className="py-1 font-normal">Validated</th>
                                        <th className="py-1 font-normal">Changed</th>
                                        <th className="py-1 font-normal">Rotation</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {d.keys.map((k) => (
                                        <tr key={`${k.component}-${k.provider}`} className="border-t border-border">
                                            <td className="py-1">
                                                {k.provider} <span className="text-xs text-muted-foreground">{k.component} · {k.environment}</span>
                                            </td>
                                            <td className="py-1 font-mono text-xs">{k.masked_key ?? "—"}</td>
                                            <td className="py-1">
                                                <StateBadge state={HEALTH[k.health] ?? k.health} label={words(k.health)} />
                                                {k.last_check_reason && <span className="block text-xs text-muted-foreground">{k.last_check_reason}</span>}
                                            </td>
                                            <td className="py-1 text-xs">{when(k.validated_at)}</td>
                                            <td className="py-1 text-xs">{when(k.last_changed_at)}</td>
                                            <td className="py-1 text-xs">{k.open_rotation ? `#${k.open_rotation} ${words(k.open_rotation_state)}` : "—"}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </TableRegion>
                    )
                }
            </Panel>

            {query.state === "ok" && (
                <Panel title="Rotations in progress" query={query}>
                    {(d) =>
                        d.rotations.length === 0 ? (
                            <Empty>No rotation is in progress.</Empty>
                        ) : (
                            <ul className="divide-y divide-border text-sm">
                                {d.rotations.map((r) => (
                                    <li key={r.id} className="space-y-1 py-2">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <span className="font-mono text-xs">#{r.id}</span>
                                            <span>
                                                {r.provider} ({r.component})
                                            </span>
                                            <StateBadge state={r.state} />
                                            <span className="font-mono text-xs">{r.staged_key}</span>
                                        </div>
                                        {r.provider_revocation_reminder && <p className="text-xs">{r.provider_revocation_reminder}</p>}
                                        {rotate && r.next_steps.length > 0 && (
                                            <div className="flex flex-wrap gap-2">
                                                {r.next_steps.map((s) => (
                                                    <Button key={s} size="sm" variant="outline" className="min-h-11 md:min-h-8" disabled={busy} onClick={() => void step(r.id, s)}>
                                                        {words(s)}
                                                    </Button>
                                                ))}
                                            </div>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        )
                    }
                </Panel>
            )}

            {rotate && query.state === "ok" && (
                <section className="space-y-3 rounded-lg border border-border p-4 ph-no-capture" aria-label="Stage a key" data-ph-no-capture>
                    <h2 className="text-base font-semibold">Add or replace a key</h2>
                    <div className="grid gap-2 sm:grid-cols-2">
                        <label className="flex flex-col gap-1 text-sm">
                            Component
                            <select value={form.component} onChange={(e) => setForm({ ...form, component: e.target.value })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm">
                                {["llm", "stt", "tts", "data"].map((c) => (
                                    <option key={c} value={c}>
                                        {c}
                                    </option>
                                ))}
                            </select>
                        </label>
                        <label className="flex flex-col gap-1 text-sm">
                            Provider
                            <input value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm" />
                        </label>
                        <label className="flex flex-col gap-1 text-sm">
                            Environment
                            <input value={me.environment} readOnly className="min-h-11 rounded-md border border-input bg-muted px-2 text-base md:min-h-9 md:text-sm" />
                        </label>
                        <label className="flex flex-col gap-1 text-sm">
                            Label (optional)
                            <input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm" />
                        </label>
                        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
                            Secret
                            <input
                                type="password"
                                autoComplete="off"
                                spellCheck={false}
                                value={secret}
                                onChange={(e) => setSecret(e.target.value)}
                                onCopy={(e) => e.preventDefault()}
                                className="min-h-11 rounded-md border border-input bg-background px-2 font-mono text-base md:min-h-9 md:text-sm"
                            />
                        </label>
                        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
                            Reason (recorded in the audit)
                            <input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm" />
                        </label>
                    </div>
                    <Button className="min-h-11 md:min-h-9" disabled={busy || secret.length < 8 || !form.provider || form.reason.trim().length < 4} onClick={() => void stage()}>
                        Stage the key
                    </Button>
                </section>
            )}
            {message && (
                <p role="status" className="text-sm">
                    {message}
                </p>
            )}
        </div>
    );
}
