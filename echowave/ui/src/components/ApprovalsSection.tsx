"use client";

import { ArrowDown, ArrowUp, Download, Loader2, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
    getApprovalRulesApiV1OrganizationsApprovalRulesGet,
    getAuditApiV1OrganizationsAuditGet,
    getAuditCsvApiV1OrganizationsAuditCsvGet,
    listMembersApiV1OrganizationsMembersGet,
    saveApprovalRulesApiV1OrganizationsApprovalRulesPut,
} from "@/client/sdk.gen";
import type { ApprovalRuleIn, AuditEntryOut, OrganizationMemberResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { detailFromResult } from "@/lib/apiError";

/**
 * The approval matrix and the audit log (KAN-160, E-1 slice 2).
 *
 * Rules are read top to bottom and the first that matches decides, so the
 * order on screen is the order that counts; the arrows move a rule. Amounts
 * are typed in rupees and sent in paise, because the server compares paise
 * and a person thinks in rupees. Nothing is saved until Save: a half-edited
 * matrix that saved on each keystroke would, for a moment, let anyone
 * approve anything.
 */

/** What a rule can be about. The register kinds match formats.KINDS. */
const SUBJECTS: { value: string; label: string }[] = [
    { value: "*", label: "Everything" },
    { value: "purchase_order", label: "Purchase order" },
    { value: "tax_invoice", label: "Tax invoice" },
    { value: "work_order", label: "Work order" },
    { value: "rfq", label: "RFQ" },
    { value: "award_letter", label: "Award letter" },
    { value: "comparative_statement", label: "Comparative statement" },
    { value: "card", label: "A proposed action (card)" },
    { value: "decision", label: "An agent's question" },
];

/** A member's own approver choice is "user:<id>"; a role is the role. */
const ROLE_APPROVERS = [
    { value: "admin", label: "An admin" },
    { value: "owner", label: "The owner" },
];

type Draft = {
    subject: string;
    /** Rupees as typed; "" for an open end. */
    min: string;
    max: string;
    approver: string; // "admin" | "owner" | "user:<id>"
};

function toDraft(rule: ApprovalRuleIn): Draft {
    return {
        subject: rule.subject || "*",
        min: rule.min_amount_paise == null ? "" : String(rule.min_amount_paise / 100),
        max: rule.max_amount_paise == null ? "" : String(rule.max_amount_paise / 100),
        approver:
            rule.approver_user_id != null
                ? `user:${rule.approver_user_id}`
                : rule.approver_role || "admin",
    };
}

function paise(rupees: string): number | null {
    const text = rupees.replace(/[₹,\s]/g, "");
    if (!text) return null;
    const n = Number(text);
    if (!Number.isFinite(n) || n < 0) return null;
    return Math.round(n * 100);
}

export function toRule(draft: Draft): ApprovalRuleIn {
    const byUser = draft.approver.startsWith("user:");
    return {
        subject: draft.subject,
        min_amount_paise: paise(draft.min),
        max_amount_paise: paise(draft.max),
        approver_role: byUser ? null : draft.approver,
        approver_user_id: byUser ? Number(draft.approver.slice(5)) : null,
    };
}

const NEW_RULE: Draft = { subject: "*", min: "", max: "", approver: "admin" };

function describeEntry(entry: AuditEntryOut): string {
    const what = entry.action.replace(/_/g, " ");
    return entry.subject ? `${what}: ${entry.subject}` : what;
}

function when(iso: string | null | undefined): string {
    if (!iso) return "";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function ApprovalsSection() {
    const { isOrganizationAdmin, loaded } = useAccessRoles();
    const [rules, setRules] = useState<Draft[]>([]);
    const [members, setMembers] = useState<OrganizationMemberResponse[]>([]);
    const [entries, setEntries] = useState<AuditEntryOut[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [dirty, setDirty] = useState(false);
    const [downloading, setDownloading] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        const [got, people, audit] = await Promise.all([
            getApprovalRulesApiV1OrganizationsApprovalRulesGet(),
            listMembersApiV1OrganizationsMembersGet(),
            getAuditApiV1OrganizationsAuditGet({ query: { limit: 20 } }),
        ]);
        if (got.error || !got.data) {
            toast.error(detailFromResult(got, "Could not load the approval rules"));
        } else {
            setRules(got.data.rules.map(toDraft));
            setDirty(false);
        }
        if (people.data) setMembers(people.data.members);
        if (audit.data) setEntries(audit.data.entries);
        setLoading(false);
    }, []);

    useEffect(() => {
        if (loaded && isOrganizationAdmin) void load();
    }, [loaded, isOrganizationAdmin, load]);

    const edit = (index: number, patch: Partial<Draft>) => {
        setRules((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
        setDirty(true);
    };
    const move = (index: number, by: -1 | 1) => {
        setRules((prev) => {
            const next = [...prev];
            const target = index + by;
            if (target < 0 || target >= next.length) return prev;
            [next[index], next[target]] = [next[target], next[index]];
            return next;
        });
        setDirty(true);
    };
    const remove = (index: number) => {
        setRules((prev) => prev.filter((_, i) => i !== index));
        setDirty(true);
    };

    const save = async () => {
        setSaving(true);
        const response = await saveApprovalRulesApiV1OrganizationsApprovalRulesPut({
            body: { rules: rules.map(toRule) },
        });
        setSaving(false);
        if (response.error || !response.data) {
            toast.error(detailFromResult(response, "Could not save the approval rules"));
            return;
        }
        setRules(response.data.rules.map(toDraft));
        setDirty(false);
        toast.success("Approval rules saved");
        const audit = await getAuditApiV1OrganizationsAuditGet({ query: { limit: 20 } });
        if (audit.data) setEntries(audit.data.entries);
    };

    const download = async () => {
        setDownloading(true);
        const response = await getAuditCsvApiV1OrganizationsAuditCsvGet({ parseAs: "text" });
        setDownloading(false);
        if (response.error || typeof response.data !== "string") {
            toast.error(detailFromResult(response, "Could not export the audit log"));
            return;
        }
        const blob = new Blob([response.data], { type: "text/csv;charset=utf-8;" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `audit-${new Date().toISOString().slice(0, 10)}.csv`;
        link.style.visibility = "hidden";
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);
    };

    if (loaded && !isOrganizationAdmin) {
        return (
            <p className="text-sm text-muted-foreground">
                Only an admin or the owner can see or change who approves what.
            </p>
        );
    }
    if (loading) {
        return (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading…
            </div>
        );
    }

    const approverOptions = [
        ...ROLE_APPROVERS,
        ...members.map((m) => ({ value: `user:${m.user_id}`, label: m.email ?? `Member ${m.user_id}` })),
    ];

    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <p className="text-sm text-muted-foreground">
                    Read top to bottom; the first rule that matches decides. No matching rule
                    means anyone on the team may. Amounts are in rupees; leave one end blank
                    for an open band.
                </p>
                {rules.length === 0 ? (
                    <p className="text-sm text-muted-foreground" data-testid="no-rules">
                        No rules yet: anyone may confirm a card, answer a question or issue a
                        document.
                    </p>
                ) : (
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead className="w-8">#</TableHead>
                                <TableHead>About</TableHead>
                                <TableHead>From ₹</TableHead>
                                <TableHead>Below ₹</TableHead>
                                <TableHead>Needs</TableHead>
                                <TableHead className="w-28" />
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {rules.map((rule, index) => (
                                <TableRow key={index} data-testid="rule-row">
                                    <TableCell>{index + 1}</TableCell>
                                    <TableCell>
                                        <Select
                                            value={rule.subject}
                                            onValueChange={(v) => edit(index, { subject: v })}
                                        >
                                            <SelectTrigger aria-label={`Rule ${index + 1} subject`}>
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {SUBJECTS.map((s) => (
                                                    <SelectItem key={s.value} value={s.value}>
                                                        {s.label}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </TableCell>
                                    <TableCell>
                                        <Input
                                            aria-label={`Rule ${index + 1} from`}
                                            inputMode="decimal"
                                            placeholder="any"
                                            value={rule.min}
                                            onChange={(e) => edit(index, { min: e.target.value })}
                                        />
                                    </TableCell>
                                    <TableCell>
                                        <Input
                                            aria-label={`Rule ${index + 1} below`}
                                            inputMode="decimal"
                                            placeholder="any"
                                            value={rule.max}
                                            onChange={(e) => edit(index, { max: e.target.value })}
                                        />
                                    </TableCell>
                                    <TableCell>
                                        <Select
                                            value={rule.approver}
                                            onValueChange={(v) => edit(index, { approver: v })}
                                        >
                                            <SelectTrigger aria-label={`Rule ${index + 1} approver`}>
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {approverOptions.map((o) => (
                                                    <SelectItem key={o.value} value={o.value}>
                                                        {o.label}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </TableCell>
                                    <TableCell className="whitespace-nowrap">
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            aria-label={`Move rule ${index + 1} up`}
                                            disabled={index === 0}
                                            onClick={() => move(index, -1)}
                                        >
                                            <ArrowUp className="h-4 w-4" />
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            aria-label={`Move rule ${index + 1} down`}
                                            disabled={index === rules.length - 1}
                                            onClick={() => move(index, 1)}
                                        >
                                            <ArrowDown className="h-4 w-4" />
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            aria-label={`Remove rule ${index + 1}`}
                                            onClick={() => remove(index)}
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                )}
                <div className="flex items-center gap-2">
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                            setRules((prev) => [...prev, { ...NEW_RULE }]);
                            setDirty(true);
                        }}
                    >
                        <Plus className="mr-1 h-4 w-4" /> Add rule
                    </Button>
                    <Button size="sm" onClick={save} disabled={!dirty || saving}>
                        {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
                        Save
                    </Button>
                </div>
            </div>

            <div className="space-y-3">
                <div className="flex items-center justify-between">
                    <h3 className="text-sm font-medium">Audit log</h3>
                    <Button variant="outline" size="sm" onClick={download} disabled={downloading}>
                        {downloading ? (
                            <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                        ) : (
                            <Download className="mr-1 h-4 w-4" />
                        )}
                        Export CSV
                    </Button>
                </div>
                {entries.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Nothing recorded yet.</p>
                ) : (
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>When</TableHead>
                                <TableHead>Who</TableHead>
                                <TableHead>What</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {entries.map((entry) => (
                                <TableRow key={entry.id}>
                                    <TableCell className="whitespace-nowrap text-muted-foreground">
                                        {when(entry.at)}
                                    </TableCell>
                                    <TableCell>{entry.actor}</TableCell>
                                    <TableCell>{describeEntry(entry)}</TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                )}
            </div>
        </div>
    );
}
