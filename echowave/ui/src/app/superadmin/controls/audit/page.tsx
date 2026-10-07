"use client";

import { useMemo, useState } from "react";

import { AuditTimeline, type AuditTimelineEntry } from "@/components/shell";
import { Field, Fields, PageHeader, Panel } from "@/components/staff/parts";
import { useReportFreshness } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useStaffData } from "@/lib/staff/data";
import { commandOf, when, words } from "@/lib/staff/format";

type Entry = {
    key: string;
    source: string;
    id: number;
    created_at: string | null;
    actor_user_id: number | null;
    actor_email: string | null;
    action: string;
    organization_id: number | null;
    organization_name: string | null;
    target_user_id: number | null;
    target_user_email: string | null;
    note: string | null;
    old_value: unknown;
    new_value: unknown;
};
type Page = { entries: Entry[]; next_before: string | null; actions: string[] };

/**
 * Staff audit (screen 44): immutable records, newest first, with a detail
 * drawer. Rows from one staff command are correlated by its id. Recorded
 * values are hidden until asked for; a failed load is said as a failure,
 * never drawn as an empty log.
 */
export default function AuditPage() {
    const [action, setAction] = useState("");
    const [actor, setActor] = useState("");
    const [before, setBefore] = useState<string | null>(null);
    const query = useStaffData<Page>(
        "/api/v1/admin/staff/audit",
        useMemo(() => ({ action: action || undefined, actor_user_id: actor ? Number(actor) : undefined, before: before ?? undefined }), [action, actor, before]),
    );
    const [open, setOpen] = useState<Entry | null>(null);
    const [showValues, setShowValues] = useState(false);
    const [filters, setFilters] = useState(false);
    useReportFreshness(query.state, query.refreshedAt);

    const filterForm = (actions: string[]) => (
        <div className="flex flex-wrap items-end gap-2 text-sm">
            <label className="flex flex-col gap-1">
                Action
                <select value={action} onChange={(e) => { setAction(e.target.value); setBefore(null); }} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm">
                    <option value="">All</option>
                    {actions.map((a) => (
                        <option key={a} value={a}>
                            {words(a)}
                        </option>
                    ))}
                </select>
            </label>
            <label className="flex flex-col gap-1">
                Staff member id
                <input inputMode="numeric" value={actor} onChange={(e) => { setActor(e.target.value.replace(/\D/g, "")); setBefore(null); }} className="min-h-11 w-32 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm" />
            </label>
        </div>
    );

    return (
        <div className="space-y-4">
            <PageHeader title="Audit" description="Every staff action: who, what, to what, why, and the result." />
            <Panel query={query} skeletonHeight={240}>
                {(p) => (
                    <div className="space-y-3">
                        <div className="hidden md:block">{filterForm(p.actions)}</div>
                        <Button variant="outline" className="min-h-11 md:hidden" onClick={() => setFilters(true)}>
                            Filters
                        </Button>
                        {p.entries.length === 0 ? (
                            <p className="text-sm text-muted-foreground">No audit records match these filters.</p>
                        ) : (
                            <ul className="divide-y divide-border text-sm" data-testid="audit-rows">
                                {p.entries.map((e) => (
                                    <li key={e.key}>
                                        <button type="button" onClick={() => { setOpen(e); setShowValues(false); }} className="motion-m1 flex min-h-11 w-full flex-wrap items-center gap-x-3 gap-y-0.5 py-2 text-left hover:bg-muted/40">
                                            <span className="text-xs text-muted-foreground">{when(e.created_at)}</span>
                                            <span className="font-medium">{words(e.action)}</span>
                                            <span className="text-xs">{e.actor_email ?? (e.actor_user_id ? `user ${e.actor_user_id}` : "system")}</span>
                                            {commandOf(e.note) !== null && <span className="font-mono text-xs">command #{commandOf(e.note)}</span>}
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                        {p.next_before && (
                            <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => setBefore(p.next_before)}>
                                Older
                            </Button>
                        )}
                        <Sheet open={filters} onOpenChange={setFilters}>
                            <SheetContent side="bottom" className="p-4">
                                <SheetTitle>Filters</SheetTitle>
                                <SheetDescription className="sr-only">Filter the audit</SheetDescription>
                                <div className="mt-3">{filterForm(p.actions)}</div>
                            </SheetContent>
                        </Sheet>
                    </div>
                )}
            </Panel>
            <Sheet open={open !== null} onOpenChange={(o) => !o && setOpen(null)}>
                <SheetContent side="right" className="w-full max-w-full overflow-y-auto p-4 sm:max-w-md">
                    <SheetTitle>Audit record</SheetTitle>
                    <SheetDescription className="sr-only">One staff action</SheetDescription>
                    {open && (
                        <div className="mt-3 space-y-4 text-sm">
                            <Fields>
                                <Field label="When">{when(open.created_at)}</Field>
                                <Field label="Actor">{open.actor_email ?? open.actor_user_id ?? "system"}</Field>
                                <Field label="Action">{words(open.action)}</Field>
                                <Field label="Account">{open.organization_name ?? open.organization_id ?? "—"}</Field>
                                <Field label="Person">{open.target_user_email ?? open.target_user_id ?? "—"}</Field>
                                <Field label="Note">{open.note ?? "—"}</Field>
                                <Field label="Source">{open.source} #{open.id}</Field>
                            </Fields>
                            {(open.old_value !== null || open.new_value !== null) && (
                                <div>
                                    <Button variant="outline" size="sm" className="min-h-11 md:min-h-8" onClick={() => setShowValues((v) => !v)} aria-expanded={showValues}>
                                        {showValues ? "Hide recorded values" : "Show recorded values"}
                                    </Button>
                                    {showValues && <pre className="mt-2 max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 text-xs">{JSON.stringify({ before: open.old_value, after: open.new_value }, null, 2)}</pre>}
                                </div>
                            )}
                            {commandOf(open.note) !== null && query.data && (
                                <div>
                                    <h3 className="mb-2 text-xs font-medium text-muted-foreground">Everything on this page about command #{commandOf(open.note)}</h3>
                                    <AuditTimeline
                                        entries={query.data.entries
                                            .filter((e) => commandOf(e.note) === commandOf(open.note))
                                            .map(
                                                (e): AuditTimelineEntry => ({
                                                    id: e.key,
                                                    at: e.created_at ?? "",
                                                    actor: e.actor_email ?? `user ${e.actor_user_id}`,
                                                    action: e.action,
                                                    result: e.note?.match(/\[(\w+)\]/)?.[1],
                                                }),
                                            )}
                                    />
                                </div>
                            )}
                        </div>
                    )}
                </SheetContent>
            </Sheet>
        </div>
    );
}
