"use client";

import { useParams } from "next/navigation";

import { Empty, Field, Fields, PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness } from "@/components/staff/StaffShell";
import { useStaffData } from "@/lib/staff/data";
import { count, when, words } from "@/lib/staff/format";

type Trace = {
    task: { id: number; organization_id: number; number: number | null; state: string; state_version: number; payload_version: string | null; has_evidence: boolean };
    stages: Array<{ stage: string; at: string | null; state: string | null; reason_code?: string | null; sequence?: number; missing?: string }>;
    run: { id: number; mode: string; language: string | null; created_at: string | null; ended_at: string | null; completed: boolean; costed: boolean } | null;
    turns: Array<{ turn: number; marks_ms: Record<string, number | null>; missing: string[]; perceived_ms: number | null; tool: string | null; tool_ms: number | null }>;
    card: { event_id: number; state: string; ledger_state: string | null; version: string | null; action: string | null; proposed_at: string | null; confirmed_at: string | null } | null;
    content: string;
};

const MARKS = ["user_stopped", "endpoint", "stt_final", "llm_first_token", "tts_first_byte", "audio_out"];

/** The gap between two marks from the same pipeline clock, or unknown when
 *  either is missing. Never subtracts across a gap. */
function gap(marks: Record<string, number | null>, from: string, to: string): number | null {
    const a = marks[from];
    const b = marks[to];
    return a === null || a === undefined || b === null || b === undefined ? null : b - a;
}

/**
 * Task trace (screen 40): the stage timeline above a compact event table.
 * States, codes and times only; a missing timestamp is marked missing and
 * no segment is drawn for it.
 */
export default function TracePage() {
    const params = useParams<{ taskId: string }>();
    const query = useStaffData<Trace>(`/api/v1/admin/staff/operations/trace/${params?.taskId}`);
    useReportFreshness(query.state, query.refreshedAt);
    return (
        <div className="space-y-4">
            <PageHeader title={`Task #${params?.taskId} trace`} description="Private content is not part of a staff trace." />
            <Panel title="Task" query={query}>
                {(t) => (
                    <Fields>
                        <Field label="State">
                            <StateBadge state={t.task.state} />
                        </Field>
                        <Field label="Workspace">{t.task.organization_id}</Field>
                        <Field label="State version">{t.task.state_version}</Field>
                        <Field label="Approved payload">{t.task.payload_version ?? "—"}</Field>
                        <Field label="Evidence">{t.task.has_evidence ? "Recorded" : "None"}</Field>
                    </Fields>
                )}
            </Panel>
            <Panel title="Stages" query={query}>
                {(t) => (
                    <ol className="relative space-y-3 border-l border-border pl-4" aria-label="Stage timeline">
                        {t.stages.map((s, i) => (
                            <li key={i} className="text-sm">
                                <span aria-hidden className="absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full bg-muted-foreground" />
                                <span className="font-medium">{s.stage}</span> {s.state && <StateBadge state={s.state} />}
                                <span className="block text-xs text-muted-foreground">
                                    {s.at ? when(s.at) : <span className="text-[#705500]">Time missing</span>}
                                    {s.reason_code && ` · ${s.reason_code}`}
                                    {s.missing && ` · ${s.missing}`}
                                </span>
                            </li>
                        ))}
                    </ol>
                )}
            </Panel>
            <Panel title="Approval card" query={query}>
                {(t) =>
                    !t.card ? (
                        <Empty>This task did not wait on an approval card.</Empty>
                    ) : (
                        <Fields>
                            <Field label="Card">#{t.card.event_id}</Field>
                            <Field label="Action">{words(t.card.action)}</Field>
                            <Field label="State">
                                <StateBadge state={t.card.state} />
                            </Field>
                            <Field label="Payload version">{t.card.version ?? "—"}</Field>
                            <Field label="Proposed">{when(t.card.proposed_at)}</Field>
                            <Field label="Confirmed">{when(t.card.confirmed_at)}</Field>
                        </Fields>
                    )
                }
            </Panel>
            <Panel title="Voice turns" query={query}>
                {(t) =>
                    !t.run ? (
                        <Empty>No run is linked to this task.</Empty>
                    ) : t.turns.length === 0 ? (
                        <Empty>Run #{t.run.id} recorded no turn timings.</Empty>
                    ) : (
                        <TableRegion label="Voice turn timings">
                            <table className="w-full min-w-[560px] text-sm">
                                <thead className="text-left text-xs text-muted-foreground">
                                    <tr>
                                        <th className="sticky left-0 bg-background py-1 font-normal">Turn</th>
                                        <th className="py-1 text-right font-normal">Speech end → transcript</th>
                                        <th className="py-1 text-right font-normal">→ first token</th>
                                        <th className="py-1 text-right font-normal">→ first audio byte</th>
                                        <th className="py-1 text-right font-normal">Perceived</th>
                                        <th className="py-1 font-normal">Missing</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {t.turns.map((turn) => (
                                        <tr key={turn.turn} className="border-t border-border tabular-nums">
                                            <td className="sticky left-0 bg-background py-1">{turn.turn}</td>
                                            <td className="py-1 text-right">{count(gap(turn.marks_ms, MARKS[0], MARKS[2]))}</td>
                                            <td className="py-1 text-right">{count(gap(turn.marks_ms, MARKS[2], MARKS[3]))}</td>
                                            <td className="py-1 text-right">{count(gap(turn.marks_ms, MARKS[3], MARKS[4]))}</td>
                                            <td className="py-1 text-right">{count(turn.perceived_ms)}</td>
                                            <td className="py-1 text-xs">{turn.missing.length ? turn.missing.join(", ") : "none"}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </TableRegion>
                    )
                }
            </Panel>
        </div>
    );
}
