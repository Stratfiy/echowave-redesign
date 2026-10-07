"use client";

/**
 * Search inside one scope at a time: just you, or a workspace (handoff
 * "Scope"). Switching scope clears the results before the next search
 * renders, and an answer that arrives for the previous scope or query is
 * dropped -- a slow personal search must never paint over workspace
 * results, or the other way round.
 */

import { Loader2, Search } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/shell/ErrorState";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export type SearchScope = { id: string; label: string };

type Phase = "idle" | "loading" | "done" | "failed";

export function ScopedSearch<T>({
    scopes,
    scope,
    onScopeChange,
    search,
    renderResult,
    resultKey,
    placeholder = "Search",
    debounceMs = 250,
    className,
}: {
    scopes: readonly SearchScope[];
    scope: string;
    onScopeChange: (scope: string) => void;
    /** Run one search. Rejects or throws on failure. */
    search: (query: string, scope: string) => Promise<T[]>;
    renderResult: (result: T) => ReactNode;
    resultKey: (result: T) => string;
    placeholder?: string;
    debounceMs?: number;
    className?: string;
}) {
    const [query, setQuery] = useState("");
    const [results, setResults] = useState<T[]>([]);
    const [phase, setPhase] = useState<Phase>("idle");
    const latest = useRef(0);
    const inputId = useId();
    const scopeLabel = scopes.find((s) => s.id === scope)?.label ?? scope;

    const run = async (q: string, s: string) => {
        const ticket = ++latest.current;
        if (!q.trim()) {
            setResults([]);
            setPhase("idle");
            return;
        }
        setPhase("loading");
        try {
            const found = await search(q.trim(), s);
            if (ticket !== latest.current) return; // a newer search or scope won
            setResults(found);
            setPhase("done");
        } catch {
            if (ticket !== latest.current) return;
            setResults([]);
            setPhase("failed");
        }
    };

    // A new scope empties the list first, so nothing from the last scope is
    // ever on screen under the new scope's name.
    useEffect(() => {
        latest.current += 1;
        setResults([]);
        setPhase("idle");
        if (query.trim()) void run(query, scope);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope]);

    useEffect(() => {
        const timer = setTimeout(() => void run(query, scope), debounceMs);
        return () => clearTimeout(timer);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [query]);

    return (
        <div className={cn("flex flex-col gap-3", className)} data-testid="scoped-search">
            <div className="flex flex-wrap items-center gap-2">
                <div role="radiogroup" aria-label="Search in" className="inline-flex rounded-[var(--radius-button)] border border-border p-0.5">
                    {scopes.map((option) => (
                        <button
                            key={option.id}
                            type="button"
                            role="radio"
                            aria-checked={option.id === scope}
                            onClick={() => onScopeChange(option.id)}
                            className={cn(
                                "motion-m1 min-h-11 rounded-md px-3 text-sm md:min-h-8",
                                option.id === scope ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent",
                            )}
                        >
                            {option.label}
                        </button>
                    ))}
                </div>
                <div className="relative min-w-0 flex-1">
                    <label htmlFor={inputId} className="sr-only">
                        Search {scopeLabel}
                    </label>
                    <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                        id={inputId}
                        type="search"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder={placeholder}
                        className="min-h-11 pl-9 text-base md:min-h-9 md:text-sm"
                    />
                </div>
            </div>
            <div aria-live="polite" className="sr-only">
                {phase === "done" ? `${results.length} results in ${scopeLabel}` : ""}
            </div>
            {phase === "loading" && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" /> Searching {scopeLabel}…
                </p>
            )}
            {phase === "failed" && (
                <ErrorState title={`Could not search ${scopeLabel}`} onRetry={() => void run(query, scope)} />
            )}
            {phase === "done" && results.length === 0 && (
                <EmptyState title={`Nothing in ${scopeLabel} matches that.`} description="Try other words, or the other scope." />
            )}
            {phase === "done" && results.length > 0 && (
                <ul className="flex flex-col gap-1" aria-label={`Results in ${scopeLabel}`}>
                    {results.map((result) => (
                        <li key={resultKey(result)} className="motion-m6-enter">
                            {renderResult(result)}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

export default ScopedSearch;
