"use client";

/**
 * What support will see, before it is sent (screen 28; handoff 33).
 *
 * Every section is listed, included or not, with the exact fields it
 * carries -- so the person chooses knowing what each switch sends. Who is
 * asking is always included and says so; the words themselves are off
 * until switched on. What is never shared is said in one line underneath.
 */

import { Eye, EyeOff, Lock } from "lucide-react";
import { useId } from "react";

import type { SharePreview } from "@/lib/support/help";
import { cn } from "@/lib/utils";

export function showShared(value: unknown): string {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value)) {
        const date = new Date(value);
        if (!Number.isNaN(date.getTime())) return date.toLocaleString();
    }
    return String(value);
}

export function SharePreviewPanel({
    preview,
    onToggle,
    disabled = false,
    className,
}: {
    preview: SharePreview;
    onToggle?: (key: string, included: boolean) => void;
    disabled?: boolean;
    className?: string;
}) {
    const baseId = useId();
    return (
        <section
            aria-label="What support will see"
            className={cn("rounded-[var(--radius)] border border-border", className)}
            data-testid="share-preview"
        >
            <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">What support will see</h2>
            <ul className="divide-y divide-border">
                {preview.sections.map((section) => {
                    const id = `${baseId}-${section.key}`;
                    return (
                        <li key={section.key} className="px-4 py-3" data-testid={`share-${section.key}`} data-included={section.included}>
                            <div className="flex items-start gap-3">
                                {section.required ? (
                                    <Lock aria-hidden className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
                                ) : (
                                    <input
                                        id={id}
                                        type="checkbox"
                                        className="mt-0.5 h-5 w-5 shrink-0 accent-[var(--primary)]"
                                        checked={section.included}
                                        disabled={disabled || !onToggle}
                                        onChange={(event) => onToggle?.(section.key, event.target.checked)}
                                    />
                                )}
                                <label htmlFor={section.required ? undefined : id} className="min-h-11 flex-1 cursor-pointer md:min-h-0">
                                    <span className="block text-sm font-medium">{section.label}</span>
                                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                                        {section.required ? (
                                            "Always included, so support knows who to answer."
                                        ) : section.included ? (
                                            <>
                                                <Eye aria-hidden className="h-3.5 w-3.5" /> Shared
                                            </>
                                        ) : (
                                            <>
                                                <EyeOff aria-hidden className="h-3.5 w-3.5" /> Not shared
                                            </>
                                        )}
                                    </span>
                                </label>
                            </div>
                            <dl
                                className={cn(
                                    "mt-2 grid grid-cols-[minmax(6rem,auto)_1fr] gap-x-3 gap-y-1 pl-8 text-sm",
                                    !section.included && "text-muted-foreground line-through decoration-muted-foreground/40",
                                )}
                            >
                                {section.fields.map((field) => (
                                    <div key={field.label} className="contents">
                                        <dt className="text-muted-foreground no-underline">{field.label}</dt>
                                        <dd className="min-w-0 whitespace-pre-wrap break-words">{showShared(field.value)}</dd>
                                    </div>
                                ))}
                            </dl>
                        </li>
                    );
                })}
            </ul>
            <p className="border-t border-border px-4 py-3 text-xs text-muted-foreground">{preview.not_shared}</p>
        </section>
    );
}

export default SharePreviewPanel;
