"use client";

import Link from "next/link";
import { useState } from "react";

import { CommandFlow } from "@/components/staff/CommandFlow";
import { Empty, PageHeader, Panel, StateBadge } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { useStaffData } from "@/lib/staff/data";
import { when } from "@/lib/staff/format";

type Incident = { id: number; title: string; severity: string; state: string; opened_at: string; impact: string };

/** Incidents (screen 41): open ones first, each opening its runbook. */
export default function IncidentsPage() {
    const { can } = useStaffConsole();
    const query = useStaffData<{ incidents: Incident[] }>("/api/v1/admin/staff/incidents");
    const [opening, setOpening] = useState(false);
    const [form, setForm] = useState({ title: "", impact: "", severity: "sev2" });
    useReportFreshness(query.state, query.refreshedAt);
    const ready = form.title.trim().length >= 4 && form.impact.trim().length >= 4;
    return (
        <div className="space-y-4">
            <PageHeader
                title="Incidents"
                actions={
                    can("incidents.manage") ? (
                        <Button className="min-h-11 md:min-h-9" onClick={() => setOpening(true)}>
                            Open an incident
                        </Button>
                    ) : null
                }
            />
            {opening && (
                <section className="space-y-3 rounded-lg border border-border p-4" aria-label="Open an incident">
                    <label className="flex flex-col gap-1 text-sm">
                        Title
                        <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm" />
                    </label>
                    <label className="flex flex-col gap-1 text-sm">
                        Impact
                        <input value={form.impact} onChange={(e) => setForm({ ...form, impact: e.target.value })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm" />
                    </label>
                    <label className="flex max-w-xs flex-col gap-1 text-sm">
                        Severity
                        <select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm">
                            <option value="sev1">Sev 1</option>
                            <option value="sev2">Sev 2</option>
                            <option value="sev3">Sev 3</option>
                        </select>
                    </label>
                    {ready && (
                        <CommandFlow
                            key={JSON.stringify(form)}
                            command="incident.open"
                            target={form}
                            targetLabel={form.title}
                            onDone={() => {
                                setOpening(false);
                                void query.refresh();
                            }}
                            onCancel={() => setOpening(false)}
                        />
                    )}
                </section>
            )}
            <Panel query={query} setupHint="Turn on staff_incidents to keep incidents here.">
                {(d) =>
                    d.incidents.length === 0 ? (
                        <Empty>No incidents recorded.</Empty>
                    ) : (
                        <ul className="divide-y divide-border text-sm">
                            {[...d.incidents]
                                .sort((a, b) => Number(a.state === "resolved") - Number(b.state === "resolved"))
                                .map((i) => (
                                    <li key={i.id} className="flex flex-wrap items-center gap-2 py-2">
                                        <Link href={`/superadmin/operations/incidents/${i.id}`} className="min-h-11 font-medium underline-offset-2 hover:underline md:min-h-0">
                                            #{i.id} {i.title}
                                        </Link>
                                        <span className="text-xs uppercase">{i.severity}</span>
                                        <StateBadge state={i.state} />
                                        <span className="ml-auto text-xs text-muted-foreground">{when(i.opened_at)}</span>
                                    </li>
                                ))}
                        </ul>
                    )
                }
            </Panel>
        </div>
    );
}
