"use client";

/**
 * Choose a language by its own name (screen 02). Searchable in either the
 * native or the English name, so "tamil" and "தமிழ்" both find it. Long
 * native names wrap at Indic line height rather than clipping, and every
 * option is a 44px target. A language without voice says so here, at the
 * moment of choosing, and stays fully usable for text.
 */

import { Check, Search } from "lucide-react";
import { useId, useMemo, useState } from "react";

import type { LanguageOption } from "@/components/early-access/WaitlistForm";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export function matchesLanguage(language: LanguageOption, query: string): boolean {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return (
        language.native.toLowerCase().includes(q) ||
        language.english.toLowerCase().includes(q) ||
        language.code.toLowerCase() === q
    );
}

export function LanguagePicker({
    languages,
    value,
    onChange,
}: {
    languages: readonly LanguageOption[];
    value: string;
    onChange: (code: string) => void;
}) {
    const [query, setQuery] = useState("");
    const searchId = useId();
    const shown = useMemo(() => languages.filter((language) => matchesLanguage(language, query)), [languages, query]);
    const chosen = languages.find((language) => language.code === value);
    return (
        <div className="flex flex-col gap-2">
            <label htmlFor={searchId} className="sr-only">
                Search languages
            </label>
            <div className="relative">
                <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                    id={searchId}
                    type="search"
                    placeholder="Search languages"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    className="min-h-11 pl-9 text-base"
                />
            </div>
            <div
                role="radiogroup"
                aria-label="Language"
                className="flex max-h-72 flex-col overflow-y-auto rounded-[var(--radius-control)] border border-border"
            >
                {shown.length === 0 && <p className="px-3 py-3 text-sm text-muted-foreground">No language matches “{query}”.</p>}
                {shown.map((language) => {
                    const selected = language.code === value;
                    return (
                        <button
                            key={language.code}
                            type="button"
                            role="radio"
                            aria-checked={selected}
                            onClick={() => onChange(language.code)}
                            className={cn(
                                "motion-m1 flex min-h-11 w-full items-center gap-3 border-b border-border px-3 py-2 text-left last:border-b-0",
                                selected ? "bg-accent" : "hover:bg-accent/60",
                            )}
                        >
                            <span className="min-w-0 flex-1 leading-[1.6]">
                                <span className="text-base font-medium" lang={language.code}>
                                    {language.native}
                                </span>
                                {language.native !== language.english && (
                                    <span className="ml-2 text-sm text-muted-foreground">{language.english}</span>
                                )}
                                {!language.voice && <span className="block text-xs text-muted-foreground">Text only for now</span>}
                            </span>
                            {selected && <Check aria-hidden className="h-4 w-4 shrink-0" />}
                        </button>
                    );
                })}
            </div>
            {chosen && !chosen.voice && (
                <p role="status" className="text-sm text-muted-foreground">
                    Live voice and dictation are not available in {chosen.english} yet. You can type and read in it.
                </p>
            )}
        </div>
    );
}

export default LanguagePicker;
