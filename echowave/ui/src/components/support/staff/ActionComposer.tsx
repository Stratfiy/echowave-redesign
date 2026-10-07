"use client";

/**
 * Screen 33, the request: choose a typed command, fill its typed fields,
 * read the exact preview, give a reason, and ask a second person to
 * approve. Commands that cannot run here are listed with the reason, not
 * hidden. Any change to the fields clears the preview -- what is requested
 * is always what was last previewed -- and one request is one command
 * however often the button is pressed.
 */

import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ErrorState } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { newKey } from "@/lib/support/help";
import {
    type ActionPreviewShape,
    type CommandRow,
    loadCommands,
    previewAction,
    requestAction,
} from "@/lib/support/staff";
import { cn } from "@/lib/utils";

import { ActionPreviewBlock } from "./ActionPreviewBlock";

const FIELD = "w-full min-h-11 rounded-[var(--radius-control)] border border-input bg-background px-3 py-2 text-base md:min-h-9 md:text-sm";
const STATE_LABEL = { available: "Available", needs_setup: "Needs setup", unavailable: "Unavailable" } as const;

export function ActionComposer({
    organizationId,
    targetUserId,
    ticketId,
}: {
    organizationId: number;
    targetUserId: number | null;
    ticketId: number | null;
}) {
    const router = useRouter();
    const [commands, setCommands] = useState<CommandRow[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [kind, setKind] = useState<string>("");
    const [values, setValues] = useState<Record<string, string>>({});
    const [preview, setPreview] = useState<ActionPreviewShape | null>(null);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [reason, setReason] = useState("");
    const [requestError, setRequestError] = useState<string | null>(null);
    const key = useRef(newKey());

    useEffect(() => {
        void loadCommands(organizationId).then((outcome) => {
            if (outcome.ok) setCommands(outcome.value);
            else setError(outcome.error);
        });
    }, [organizationId]);

    const command = commands?.find((c) => c.kind === kind) ?? null;

    function invalidate() {
        setPreview(null);
        setPreviewError(null);
        key.current = newKey();
    }

    function params(): Record<string, unknown> {
        const out: Record<string, unknown> = {};
        for (const field of command?.fields ?? []) {
            const raw = values[field.name] ?? "";
            out[field.name] = field.type === "integer" ? Number(raw) : raw;
        }
        return out;
    }

    const target = () => ({ kind, organization_id: organizationId, target_user_id: targetUserId, ticket_id: ticketId, params: params() });

    async function makePreview() {
        setBusy(true);
        setPreviewError(null);
        const outcome = await previewAction(target());
        setBusy(false);
        if (outcome.ok) setPreview(outcome.value);
        else setPreviewError(outcome.error);
    }

    async function submit() {
        if (!preview || reason.trim().length < 5) return;
        setBusy(true);
        setRequestError(null);
        const outcome = await requestAction(target(), reason.trim(), preview.version, key.current);
        setBusy(false);
        if (!outcome.ok) {
            setRequestError(outcome.error);
            if (outcome.status === 409) invalidate();
            return;
        }
        router.push(`/superadmin/support/actions/${outcome.value.action.id}`);
    }

    if (error) return <ErrorState title="The commands could not load." description={error} />;
    if (!commands)
        return (
            <p role="status" className="text-sm text-muted-foreground">
                Loading commands…
            </p>
        );

    return (
        <div className="flex flex-col gap-5">
            <fieldset className="flex flex-col gap-2">
                <legend className="mb-1 text-sm font-medium">Command</legend>
                {commands.map((c) => {
                    const disabled = c.state !== "available";
                    return (
                        <label
                            key={c.kind}
                            className={cn(
                                "flex min-h-11 cursor-pointer items-start gap-3 rounded-[var(--radius)] border p-3",
                                kind === c.kind ? "border-foreground" : "border-border",
                                disabled && "cursor-not-allowed opacity-70",
                            )}
                            data-testid={`command-${c.kind}`}
                            data-state={c.state}
                        >
                            <input
                                type="radio"
                                name="command"
                                className="mt-1 h-4 w-4"
                                value={c.kind}
                                checked={kind === c.kind}
                                disabled={disabled}
                                onChange={() => {
                                    setKind(c.kind);
                                    setValues({});
                                    invalidate();
                                }}
                            />
                            <span className="min-w-0 flex-1">
                                <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
                                    {c.title}
                                    <span className="text-xs font-normal text-muted-foreground">{STATE_LABEL[c.state]}</span>
                                </span>
                                <span className="block text-xs text-muted-foreground">{c.description}</span>
                                {c.reason && <span className="mt-1 block text-xs text-[#705500] dark:text-amber-300">{c.reason}</span>}
                            </span>
                        </label>
                    );
                })}
            </fieldset>

            {command && (
                <form
                    className="flex flex-col gap-3"
                    onSubmit={(e) => {
                        e.preventDefault();
                        void makePreview();
                    }}
                >
                    {command.fields.map((field) => (
                        <div key={field.name} className="flex flex-col gap-1">
                            <label htmlFor={`field-${field.name}`} className="text-sm font-medium">
                                {field.label}
                            </label>
                            {field.type === "choice" ? (
                                <select
                                    id={`field-${field.name}`}
                                    className={FIELD}
                                    value={values[field.name] ?? ""}
                                    onChange={(e) => {
                                        setValues((v) => ({ ...v, [field.name]: e.target.value }));
                                        invalidate();
                                    }}
                                >
                                    <option value="" disabled>
                                        Choose
                                    </option>
                                    {field.choices?.map((choice) => (
                                        <option key={choice} value={choice}>
                                            {choice.replace(/_/g, " ")}
                                        </option>
                                    ))}
                                </select>
                            ) : (
                                <input
                                    id={`field-${field.name}`}
                                    className={FIELD}
                                    inputMode={field.type === "integer" ? "numeric" : undefined}
                                    type={field.type === "integer" ? "number" : "text"}
                                    min={field.min}
                                    max={field.max}
                                    value={values[field.name] ?? ""}
                                    onChange={(e) => {
                                        setValues((v) => ({ ...v, [field.name]: e.target.value }));
                                        invalidate();
                                    }}
                                />
                            )}
                        </div>
                    ))}
                    <Button type="submit" variant="outline" className="motion-m1 min-h-11 self-start md:min-h-9" disabled={busy}>
                        Preview
                    </Button>
                    {previewError && (
                        <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                            {previewError}
                        </p>
                    )}
                </form>
            )}

            {preview && (
                <div className="motion-m4-enter flex flex-col gap-3">
                    <ActionPreviewBlock preview={preview} />
                    <div className="flex flex-col gap-1">
                        <label htmlFor="action-reason" className="text-sm font-medium">
                            Reason (recorded in the audit)
                        </label>
                        <textarea id="action-reason" rows={2} className={FIELD} value={reason} onChange={(e) => setReason(e.target.value)} />
                    </div>
                    {requestError && (
                        <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                            {requestError}
                        </p>
                    )}
                    <div className="flex flex-wrap gap-2">
                        <Button type="button" className="motion-m1 min-h-11 md:min-h-9" disabled={busy || reason.trim().length < 5} onClick={() => void submit()}>
                            {busy && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                            Request approval
                        </Button>
                        {ticketId && (
                            <Button asChild variant="ghost" className="motion-m1 min-h-11 md:min-h-9">
                                <Link href={`/superadmin/support/${ticketId}`}>Back to the case</Link>
                            </Button>
                        )}
                    </div>
                    <p className="text-xs text-muted-foreground">A second staff member approves it before it can run. Nothing runs from this screen.</p>
                </div>
            )}
        </div>
    );
}

export default ActionComposer;
