"use client";

/**
 * Choose a language, searchable by its own name or its English one
 * ("தமிழ்" or "Tamil") -- screen 18: "Language selection is searchable and
 * script-aware". Languages voice cannot speak yet stay listed and say so,
 * rather than vanishing.
 */

import { Check } from "lucide-react";
import { useId, useMemo, useState } from "react";

import type { SettingsLanguage as LanguageOption } from "@/client/types.gen";
import { cn } from "@/lib/utils";

import { INPUT } from "./SettingsForm";

export function LanguagePicker({
    id,
    languages,
    value,
    onChange,
    allowNone = false,
    noneLabel = "Not set",
    voiceNote = false,
}: {
    id: string;
    languages: LanguageOption[];
    value: string | null;
    onChange: (tag: string | null) => void;
    allowNone?: boolean;
    noneLabel?: string;
    /** Say which languages voice cannot speak yet. */
    voiceNote?: boolean;
}) {
    const [query, setQuery] = useState("");
    const listId = useId();
    const shown = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return languages;
        return languages.filter(
            (l) => l.native.toLowerCase().includes(q) || l.english.toLowerCase().includes(q) || l.tag.toLowerCase().startsWith(q),
        );
    }, [languages, query]);
    const chosen = languages.find((l) => l.tag === value);

    return (
        <div className="flex flex-col gap-2">
            <input
                id={`${id}-input`}
                type="search"
                className={INPUT}
                placeholder={chosen ? `${chosen.native} · ${chosen.english}` : "Search languages"}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                aria-controls={listId}
                autoComplete="off"
            />
            <ul
                id={listId}
                role="radiogroup"
                aria-label="Languages"
                className="max-h-64 overflow-y-auto rounded-[8px] border border-border"
                data-testid={`${id}-options`}
            >
                {allowNone && !query && (
                    <li>
                        <button
                            type="button"
                            role="radio"
                            aria-checked={value === null}
                            onClick={() => onChange(null)}
                            className="motion-m1 flex min-h-11 w-full items-center gap-2 px-3 text-left text-sm hover:bg-accent"
                        >
                            <Check aria-hidden className={cn("h-4 w-4", value === null ? "" : "invisible")} />
                            {noneLabel}
                        </button>
                    </li>
                )}
                {shown.map((language) => (
                    <li key={language.tag}>
                        <button
                            type="button"
                            role="radio"
                            aria-checked={language.tag === value}
                            onClick={() => {
                                onChange(language.tag);
                                setQuery("");
                            }}
                            className="motion-m1 flex min-h-11 w-full items-center gap-2 px-3 text-left text-sm leading-relaxed hover:bg-accent"
                        >
                            <Check aria-hidden className={cn("h-4 w-4 shrink-0", language.tag === value ? "" : "invisible")} />
                            <span lang={language.tag} className="min-w-0 break-words">
                                {language.native}
                            </span>
                            <span className="text-muted-foreground">{language.english}</span>
                            {voiceNote && !language.voice && (
                                <span className="ml-auto shrink-0 text-xs text-muted-foreground">Text only for now</span>
                            )}
                        </button>
                    </li>
                ))}
                {shown.length === 0 && <li className="px-3 py-3 text-sm text-muted-foreground">No language matches “{query}”.</li>}
            </ul>
        </div>
    );
}

export default LanguagePicker;
