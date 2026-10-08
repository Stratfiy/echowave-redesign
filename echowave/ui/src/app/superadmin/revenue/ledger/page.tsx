"use client";

import { useMemo, useState } from "react";

import { client } from "@/client/client.gen";
import { CommandFlow } from "@/components/staff/CommandFlow";
import { Empty, Field, Fields, PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useStaffData } from "@/lib/staff/data";
import { money, when, words } from "@/lib/staff/format";

type Row = {
    id: number;
    organization_id: number;
    organization: string;
    status: string;
    refund_state: string | null;
    currency: string;
    collected_minor: number;
    refunded_minor: number;
    pending_refund_minor: number;
    payment_ref: string | null;
    created_at: string | null;
    paid_at: string | null;
};
type Ledger = {
    payments: Row[];
    totals: Record<string, { collected_minor: number; refunded_minor: number; pending_refund_minor: number; payments: number }>;
    refunds: { state: string; reason?: string; provider?: string };
    as_of: string;
};
type Transaction = {
    payment: Row & { provider: string; net_paise: number; gst_paise: Record<string, number | null>; order_ref: string | null; pack_code: string | null };
    eligible_minor: number;
    refunds: Array<{ id: number; amount_minor: number; currency: string; state: string; provider_refund_id: string | null; command_id: number; created_at: string; reconciled_at: string | null }>;
    timeline: Array<{ at: string | null; event: string }>;
    refund_provider: { state: string; reason?: string };
    refunds_enabled: boolean;
};

/**
 * Ledger, transaction and refund (screen 38): a dense, filtered list with
 * exact figures; a row opens its references and status timeline; refund is
 * finance-only and runs through `refund.request` -- preview of the eligible
 * amount, a second finance person, one external effect, and "refunded"
 * only after the provider confirms. Export uses exactly the same filters.
 */
export default function LedgerPage() {
    const { can } = useStaffConsole();
    const [status, setStatus] = useState("");
    const filters = useMemo(() => ({ status: status || undefined, limit: 200 }), [status]);
    const ledger = useStaffData<Ledger>("/api/v1/admin/staff/ledger", filters);
    const [open, setOpen] = useState<number | null>(null);
    const tx = useStaffData<Transaction>(open ? `/api/v1/admin/staff/ledger/${open}` : null);
    const [refunding, setRefunding] = useState(false);
    const [amount, setAmount] = useState("");
    const [exportError, setExportError] = useState<string | null>(null);
    useReportFreshness(ledger.state, ledger.refreshedAt);

    async function exportCsv() {
        setExportError(null);
        const result = await client.get({ url: "/api/v1/admin/staff/ledger/export.csv", query: filters as never, parseAs: "text" });
        if (result.error !== undefined || !result.response?.ok) {
            setExportError("The export could not be made.");
            return;
        }
        const blob = new Blob([String(result.data)], { type: "text/csv" });
        const href = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = href;
        a.download = "decibyl-ledger.csv";
        a.click();
        URL.revokeObjectURL(href);
    }

    const staleBalance = ledger.state === "stale";
    return (
        <div className="space-y-4">
            <PageHeader
                title="Ledger and refunds"
                description="What the payment provider collected, with each payment's refunds. Amounts are exact; currency is on every value."
                actions={
                    <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => void exportCsv()}>
                        Export these rows (CSV)
                    </Button>
                }
            />
            {exportError && <p role="alert" className="text-sm">{exportError}</p>}
            <label className="flex max-w-xs flex-col gap-1 text-sm">
                Status
                <select value={status} onChange={(e) => setStatus(e.target.value)} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm">
                    <option value="">All</option>
                    <option value="paid">Paid</option>
                    <option value="failed">Payment failed</option>
                    <option value="created">Started, not paid</option>
                </select>
            </label>
            <Panel query={ledger} skeletonHeight={240}>
                {(l) => (
                    <div className="space-y-3">
                        <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            Refunds <StateBadge state={l.refunds.state} label={l.refunds.state === "available" ? `Available (${l.refunds.provider})` : undefined} /> {l.refunds.reason}
                        </p>
                        <ul className="flex flex-wrap gap-4 text-sm">
                            {Object.entries(l.totals).map(([cur, t]) => (
                                <li key={cur}>
                                    {t.payments} payments · collected {money(t.collected_minor, cur)} · refunded {money(t.refunded_minor, cur)}
                                    {t.pending_refund_minor > 0 && ` · pending ${money(t.pending_refund_minor, cur)}`}
                                </li>
                            ))}
                        </ul>
                        {l.payments.length === 0 ? (
                            <Empty>No transactions match.</Empty>
                        ) : (
                            <TableRegion label="Ledger">
                                <table className="w-full min-w-[600px] text-sm">
                                    <thead className="text-left text-xs text-muted-foreground">
                                        <tr>
                                            <th className="py-1 font-normal">Payment</th>
                                            <th className="py-1 font-normal">Account</th>
                                            <th className="py-1 font-normal">Status</th>
                                            <th className="py-1 text-right font-normal">Collected</th>
                                            <th className="py-1 text-right font-normal">Refunded</th>
                                            <th className="py-1 font-normal">Paid</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {l.payments.map((p) => (
                                            <tr key={p.id} className="border-t border-border tabular-nums">
                                                <td className="py-1">
                                                    <button type="button" className="min-h-11 underline underline-offset-2 md:min-h-0" onClick={() => setOpen(p.id)}>
                                                        #{p.id}
                                                    </button>
                                                </td>
                                                <td className="py-1">{p.organization}</td>
                                                <td className="py-1">
                                                    <StateBadge state={p.status} />
                                                    {p.refund_state && <StateBadge state={p.refund_state === "refund_pending" ? "pending" : p.refund_state} label={words(p.refund_state)} />}
                                                </td>
                                                <td className="py-1 text-right">{money(p.collected_minor, p.currency)}</td>
                                                <td className="py-1 text-right">{money(p.refunded_minor, p.currency)}</td>
                                                <td className="py-1 text-xs">{when(p.paid_at)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </TableRegion>
                        )}
                    </div>
                )}
            </Panel>

            <Sheet open={open !== null} onOpenChange={(o) => { if (!o) { setOpen(null); setRefunding(false); } }}>
                <SheetContent side="right" className="w-full max-w-full overflow-y-auto p-4 sm:max-w-lg">
                    <SheetTitle>Transaction #{open}</SheetTitle>
                    <SheetDescription className="sr-only">References, status timeline and refunds</SheetDescription>
                    <Panel query={tx} className="mt-3">
                        {(t) => (
                            <div className="space-y-4 text-sm">
                                <Fields>
                                    <Field label="Account">{t.payment.organization}</Field>
                                    <Field label="Status">
                                        <StateBadge state={t.payment.status} />
                                    </Field>
                                    <Field label="Collected">{money(t.payment.collected_minor, t.payment.currency)}</Field>
                                    <Field label="Net of GST">{money(t.payment.net_paise, "INR")}</Field>
                                    <Field label="Provider">{t.payment.provider}</Field>
                                    <Field label="Payment ref">{t.payment.payment_ref ?? "—"}</Field>
                                    <Field label="Order ref">{t.payment.order_ref ?? "—"}</Field>
                                    <Field label="Refundable now">{money(t.eligible_minor, t.payment.currency)}</Field>
                                </Fields>
                                <ol className="border-l border-border pl-3" aria-label="Status timeline">
                                    {t.timeline.map((e, i) => (
                                        <li key={i} className="py-0.5">
                                            {words(e.event)} <span className="text-xs text-muted-foreground">{when(e.at)}</span>
                                        </li>
                                    ))}
                                </ol>
                                {t.refunds.length > 0 && (
                                    <ul className="space-y-1">
                                        {t.refunds.map((r) => (
                                            <li key={r.id} className="flex flex-wrap items-center gap-2">
                                                Refund #{r.id} {money(r.amount_minor, r.currency)} <StateBadge state={r.state} /> <span className="text-xs text-muted-foreground">command #{r.command_id}</span>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                                {!t.refunds_enabled ? (
                                    <p className="text-xs text-muted-foreground">Refunds are switched off (staff_refunds).</p>
                                ) : !can("refunds.request") ? (
                                    <p className="text-xs text-muted-foreground">Refunds are a finance action; your role does not include them.</p>
                                ) : staleBalance ? (
                                    <p role="alert" className="text-xs">The ledger did not refresh; refresh it before refunding.</p>
                                ) : t.refund_provider.state !== "available" ? (
                                    <p className="flex items-center gap-2 text-xs">
                                        <StateBadge state="needs_setup" /> {t.refund_provider.reason}
                                    </p>
                                ) : refunding ? (
                                    <div className="space-y-2">
                                        <label className="flex flex-col gap-1">
                                            Amount ({t.payment.currency}, minor units; at most {t.eligible_minor})
                                            <input
                                                inputMode="numeric"
                                                value={amount}
                                                onChange={(e) => setAmount(e.target.value.replace(/\D/g, ""))}
                                                className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm"
                                            />
                                        </label>
                                        {Number(amount) > 0 && (
                                            <CommandFlow
                                                key={amount}
                                                command="refund.request"
                                                target={{ payment_id: t.payment.id, organization_id: t.payment.organization_id, amount_minor: Number(amount) }}
                                                targetLabel={`${t.payment.organization}: payment #${t.payment.id}, ${money(Number(amount), t.payment.currency)}`}
                                                onDone={() => {
                                                    void tx.refresh();
                                                    void ledger.refresh();
                                                }}
                                                onCancel={() => setRefunding(false)}
                                            />
                                        )}
                                    </div>
                                ) : (
                                    t.eligible_minor > 0 && (
                                        <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => setRefunding(true)}>
                                            Refund…
                                        </Button>
                                    )
                                )}
                            </div>
                        )}
                    </Panel>
                </SheetContent>
            </Sheet>
        </div>
    );
}
