/**
 * The task board's two small inputs (TB-3): labels, and a thread box that
 * offers an agent's @handle as it is typed.
 */

"use client";

import { X } from "lucide-react";
import { useId, useRef, useState } from "react";

import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { type BotRef, insertMention, labelTone, mentionAt, mentionChoices } from "./tasks";

export function LabelChip({ label, onRemove }: { label: string; onRemove?: () => void }) {
    return (
        <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium", labelTone(label))}>
            {label}
            {onRemove && (
                <button type="button" aria-label={`Remove label ${label}`} className="opacity-70 hover:opacity-100" onClick={onRemove}>
                    <X className="h-3 w-3" aria-hidden />
                </button>
            )}
        </span>
    );
}

/** The labels on a task: chips, and a box that adds one on Enter or comma,
 *  suggesting the labels already on the board. */
export function LabelPicker({
    id,
    value,
    known,
    onChange,
    disabled,
}: {
    id?: string;
    value: string[];
    known: string[];
    onChange: (labels: string[]) => void;
    disabled?: boolean;
}) {
    const [draft, setDraft] = useState("");
    const listId = useId();
    const has = (l: string) => value.some((v) => v.toLowerCase() === l.toLowerCase());
    const add = (raw: string) => {
        const label = raw.replace(/,/g, " ").split(/\s+/).filter(Boolean).join(" ");
        setDraft("");
        if (!label || has(label)) return;
        onChange([...value, label]);
    };
    return (
        <div className="mt-1 flex flex-wrap items-center gap-1 rounded-md border border-input bg-background px-2 py-1">
            {value.map((l) => (
                <LabelChip key={l} label={l} onRemove={disabled ? undefined : () => onChange(value.filter((v) => v !== l))} />
            ))}
            <input
                id={id}
                list={listId}
                disabled={disabled}
                className="min-w-[6rem] flex-1 bg-transparent py-1 text-sm outline-none"
                placeholder={value.length ? "" : "Add a label"}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === ",") {
                        e.preventDefault();
                        add(draft);
                    } else if (e.key === "Backspace" && !draft && value.length) {
                        onChange(value.slice(0, -1));
                    }
                }}
                onBlur={() => draft.trim() && add(draft)}
            />
            <datalist id={listId}>
                {known.filter((l) => !has(l)).map((l) => (
                    <option key={l} value={l} />
                ))}
            </datalist>
        </div>
    );
}

/** A thread box that, while an @word is being typed, lists the agents it
 *  could name. Arrow keys move, Enter or Tab picks, Escape closes; picking
 *  writes the agent's @handle in place. */
export function MentionTextarea({
    value,
    onChange,
    bots,
    onSubmit,
    ...rest
}: {
    value: string;
    onChange: (text: string) => void;
    bots: BotRef[];
    onSubmit?: () => void;
} & Omit<React.ComponentProps<typeof Textarea>, "value" | "onChange" | "onSubmit">) {
    const ref = useRef<HTMLTextAreaElement>(null);
    const [caret, setCaret] = useState(0);
    const [active, setActive] = useState(0);
    const [closed, setClosed] = useState(false);
    const listId = useId();

    const at = mentionAt(value, caret);
    const choices = at && !closed ? mentionChoices(bots, at.query) : [];
    const open = choices.length > 0;

    const pick = (bot: BotRef) => {
        if (!at || !bot.handle) return;
        const next = insertMention(value, at.start, caret, bot.handle);
        onChange(next.text);
        setCaret(next.caret);
        setActive(0);
        requestAnimationFrame(() => {
            ref.current?.focus();
            ref.current?.setSelectionRange(next.caret, next.caret);
        });
    };

    return (
        <div className="relative">
            <Textarea
                {...rest}
                ref={ref}
                value={value}
                role="combobox"
                aria-expanded={open}
                aria-controls={listId}
                aria-autocomplete="list"
                aria-activedescendant={open ? `${listId}-${active}` : undefined}
                onChange={(e) => {
                    onChange(e.target.value);
                    setCaret(e.target.selectionStart ?? e.target.value.length);
                    setClosed(false);
                    setActive(0);
                }}
                onSelect={(e) => setCaret((e.target as HTMLTextAreaElement).selectionStart ?? 0)}
                onKeyDown={(e) => {
                    if (open) {
                        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                            e.preventDefault();
                            const step = e.key === "ArrowDown" ? 1 : -1;
                            setActive((i) => (i + step + choices.length) % choices.length);
                            return;
                        }
                        if (e.key === "Enter" || e.key === "Tab") {
                            e.preventDefault();
                            pick(choices[Math.min(active, choices.length - 1)]);
                            return;
                        }
                        if (e.key === "Escape") {
                            e.preventDefault();
                            setClosed(true);
                            return;
                        }
                    }
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSubmit?.();
                }}
            />
            {open && (
                <ul id={listId} role="listbox" aria-label="Agents to mention" className="absolute left-2 top-full z-20 mt-1 w-64 overflow-hidden rounded-md border border-border bg-popover text-sm shadow-md">
                    {choices.map((bot, i) => (
                        <li
                            key={bot.id}
                            id={`${listId}-${i}`}
                            role="option"
                            aria-selected={i === active}
                            className={cn("flex cursor-pointer items-center justify-between gap-2 px-3 py-1.5", i === active && "bg-muted")}
                            onMouseEnter={() => setActive(i)}
                            onMouseDown={(e) => {
                                // Before the textarea loses focus and the caret with it.
                                e.preventDefault();
                                pick(bot);
                            }}
                        >
                            <span className="font-medium">@{bot.handle}</span>
                            <span className="truncate text-xs text-muted-foreground">{bot.name}</span>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
