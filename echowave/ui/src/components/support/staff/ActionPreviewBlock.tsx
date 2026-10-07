"use client";

/**
 * The exact support action (screen 33), laid out like the customer's exact
 * action preview: target and environment first, then old and new values,
 * then impact, dependencies and who must approve. Production is said in
 * words and colour; nothing consequential is hidden behind a toggle.
 */

import { cn } from "@/lib/utils";

export type PreviewLike = {
    title: string;
    changes: { field: string; old: unknown; new: unknown }[];
    impact: string | null;
    dependencies: string[];
    target: { organization_id: number; user_id: number | null; ticket_id: number | null };
    environment: string;
    approvers: string;
};

function show(value: unknown): string {
    if (value === null || value === undefined || value === "") return "—";
    return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function ActionPreviewBlock({ preview, className }: { preview: PreviewLike; className?: string }) {
    const production = preview.environment.toLowerCase() === "production";
    return (
        <section
            aria-label={`Preview: ${preview.title}`}
            className={cn("rounded-[var(--radius)] border p-4", production ? "border-[#772322]/40" : "border-border", className)}
            data-testid="support-action-preview"
        >
            <h2 className="text-base font-semibold">{preview.title}</h2>
            <dl className="mt-3 grid grid-cols-[minmax(6rem,auto)_1fr] gap-x-3 gap-y-1 text-sm">
                <dt className="text-muted-foreground">Workspace</dt>
                <dd>#{preview.target.organization_id}</dd>
                <dt className="text-muted-foreground">Person</dt>
                <dd>{preview.target.user_id ? `#${preview.target.user_id}` : "—"}</dd>
                <dt className="text-muted-foreground">Ticket</dt>
                <dd>{preview.target.ticket_id ? `#${preview.target.ticket_id}` : "None"}</dd>
                <dt className="text-muted-foreground">Environment</dt>
                <dd className={cn(production && "font-semibold text-[#772322] dark:text-red-300")}>{preview.environment}</dd>
            </dl>
            <table className="mt-3 w-full table-fixed text-sm" aria-label="What changes">
                <thead>
                    <tr className="text-left text-xs text-muted-foreground">
                        <th className="w-1/3 font-normal">What</th>
                        <th className="w-1/3 font-normal">Now</th>
                        <th className="w-1/3 font-normal">After</th>
                    </tr>
                </thead>
                <tbody>
                    {preview.changes.map((change) => (
                        <tr key={change.field} className="align-top">
                            <td className="break-words pr-2">{change.field}</td>
                            <td className="break-words pr-2 text-muted-foreground">{show(change.old)}</td>
                            <td className="break-words font-medium">{show(change.new)}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
            {preview.impact && <p className="mt-3 text-sm">Impact: {preview.impact}</p>}
            {preview.dependencies.length > 0 && (
                <ul className="mt-2 list-disc pl-5 text-sm text-muted-foreground" aria-label="Depends on">
                    {preview.dependencies.map((d) => (
                        <li key={d}>{d}</li>
                    ))}
                </ul>
            )}
            <p className="mt-2 text-sm">
                <span className="text-muted-foreground">Approval: </span>
                {preview.approvers}
            </p>
        </section>
    );
}

export default ActionPreviewBlock;
