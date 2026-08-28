"use client";

import { CheckIcon, SearchIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { getExtractionLibraryApiV1ExtractionLibraryCatalogGet } from "@/client/sdk.gen";
import type { LibraryExtraction } from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

/** Cached per catalog: the contents are the same for everyone and never change
 *  within a session, so reopening the dialog should not re-fetch. */
const _cache = new Map<string, LibraryExtraction[]>();
const _order = new Map<string, string[]>();

export interface LibraryPickerDialogProps {
    catalog: string;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    /** Names already on the field, so an entry can say it is already added. */
    existingNames: string[];
    /** Called with the row to append. Shaped by the field's own sub-properties. */
    onAdd: (row: Record<string, unknown>) => void;
}

/**
 * Browse a backend catalog and copy an entry onto a `fixed_collection` field.
 *
 * Shown for any property whose spec carries `renderer_options.library`, so it
 * is not a QA-extraction dialog that happens to be generic — it is the generic
 * one, and QA extractions are its first catalog.
 *
 * **Adding copies.** The entry becomes an ordinary row on the node, editable
 * and owned by whoever added it; nothing keeps a reference back to the catalog.
 * That is what lets these prompts be improved later without silently rewriting
 * an agent that is working.
 */
export function LibraryPickerDialog({
    catalog,
    open,
    onOpenChange,
    existingNames,
    onAdd,
}: LibraryPickerDialogProps) {
    const { user, loading: authLoading } = useAuth();
    const [entries, setEntries] = useState<LibraryExtraction[]>(
        () => _cache.get(catalog) ?? [],
    );
    const [categories, setCategories] = useState<string[]>(
        () => _order.get(catalog) ?? [],
    );
    const [loading, setLoading] = useState(!_cache.has(catalog));
    const [error, setError] = useState<string | null>(null);
    const [activeCategory, setActiveCategory] = useState<string | null>(null);
    const [selectedKey, setSelectedKey] = useState<string | null>(null);
    const [query, setQuery] = useState("");
    const hasFetched = useRef(false);

    useEffect(() => {
        if (!open || authLoading || !user || hasFetched.current) return;
        if (_cache.has(catalog)) {
            setLoading(false);
            return;
        }
        hasFetched.current = true;
        setLoading(true);

        getExtractionLibraryApiV1ExtractionLibraryCatalogGet({
            path: { catalog },
        })
            .then((response) => {
                if (response.error) {
                    setError(
                        detailFromError(response.error, "Could not load the library."),
                    );
                    return;
                }
                const data = response.data;
                if (!data) return;
                _cache.set(catalog, data.extractions);
                _order.set(catalog, data.categories);
                setEntries(data.extractions);
                setCategories(data.categories);
            })
            .catch((err: unknown) => {
                setError(err instanceof Error ? err.message : String(err));
            })
            .finally(() => setLoading(false));
    }, [open, authLoading, user, catalog]);

    const added = useMemo(
        () => new Set(existingNames.map((n) => n.trim().toLowerCase())),
        [existingNames],
    );

    // Searching spans every section, because somebody looking for "refund" does
    // not know it was filed under Customer support. Selecting a category is the
    // way to narrow; typing is the way to ignore categories entirely.
    const matches = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (q) {
            return entries.filter((e) =>
                `${e.display_name} ${e.summary} ${e.name} ${e.category}`
                    .toLowerCase()
                    .includes(q),
            );
        }
        if (!activeCategory) return entries;
        return entries.filter((e) => e.category === activeCategory);
    }, [entries, query, activeCategory]);

    const selected =
        matches.find((e) => e.key === selectedKey) ?? matches[0] ?? null;

    const handleAdd = (entry: LibraryExtraction) => {
        onAdd({
            name: entry.name,
            prompt: entry.prompt,
            answer_type: entry.answer_type ?? "free_text",
            predefined_options: entry.predefined_options ?? "",
            expected_format: entry.expected_format ?? "text",
        });
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="flex h-[min(38rem,85vh)] max-w-4xl flex-col gap-0 overflow-hidden p-0">
                <DialogHeader className="border-b px-6 py-4">
                    <DialogTitle>Extraction library</DialogTitle>
                    <DialogDescription>
                        Ready-made fields with prompts already written for the
                        ways they go wrong. Adding one copies it here — edit it
                        freely afterwards.
                    </DialogDescription>
                </DialogHeader>

                {error ? (
                    <div className="p-6 text-sm text-destructive">{error}</div>
                ) : (
                    <div className="flex min-h-0 flex-1">
                        <div className="flex w-52 shrink-0 flex-col border-r">
                            <div className="p-2">
                                <div className="relative">
                                    <SearchIcon
                                        className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
                                        aria-hidden="true"
                                    />
                                    <Input
                                        value={query}
                                        onChange={(e) => setQuery(e.target.value)}
                                        placeholder="Search"
                                        aria-label="Search the library"
                                        className="h-8 pl-7 text-xs"
                                    />
                                </div>
                            </div>
                            <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
                                <CategoryButton
                                    label="All"
                                    active={!activeCategory}
                                    onClick={() => setActiveCategory(null)}
                                />
                                {categories.map((category) => (
                                    <CategoryButton
                                        key={category}
                                        label={category}
                                        active={activeCategory === category}
                                        onClick={() => setActiveCategory(category)}
                                    />
                                ))}
                            </div>
                        </div>

                        <div className="min-h-0 w-64 shrink-0 overflow-y-auto border-r p-2">
                            {loading ? (
                                <div className="space-y-2 p-1">
                                    {Array.from({ length: 6 }).map((_, i) => (
                                        <Skeleton key={i} className="h-9 w-full" />
                                    ))}
                                </div>
                            ) : matches.length === 0 ? (
                                <p className="p-3 text-xs text-muted-foreground">
                                    Nothing matches that.
                                </p>
                            ) : (
                                matches.map((entry) => (
                                    <button
                                        key={entry.key}
                                        type="button"
                                        onClick={() => setSelectedKey(entry.key)}
                                        className={cn(
                                            "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm",
                                            selected?.key === entry.key
                                                ? "bg-accent text-accent-foreground"
                                                : "hover:bg-accent/50",
                                        )}
                                    >
                                        <span className="min-w-0 flex-1 truncate">
                                            {entry.display_name}
                                        </span>
                                        {added.has(entry.name.toLowerCase()) && (
                                            <CheckIcon
                                                className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                                                aria-label="Already added"
                                            />
                                        )}
                                    </button>
                                ))
                            )}
                        </div>

                        <div className="flex min-w-0 flex-1 flex-col">
                            {selected ? (
                                <>
                                    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
                                        <div className="space-y-1">
                                            <h3 className="text-base font-semibold">
                                                {selected.display_name}
                                            </h3>
                                            <p className="text-sm text-muted-foreground">
                                                {selected.summary}
                                            </p>
                                        </div>

                                        <div className="flex flex-wrap gap-2">
                                            <Badge variant="secondary">
                                                {selected.category}
                                            </Badge>
                                            <Badge variant="outline">
                                                {selected.answer_type === "predefined"
                                                    ? "Fixed set"
                                                    : `Free text · ${selected.expected_format ?? "text"}`}
                                            </Badge>
                                            <Badge variant="outline">
                                                key: {selected.name}
                                            </Badge>
                                        </div>

                                        {selected.answer_type === "predefined" &&
                                            selected.predefined_options && (
                                                <Field label="Options">
                                                    <p className="text-xs">
                                                        {selected.predefined_options}
                                                    </p>
                                                </Field>
                                            )}

                                        <Field label="Instructions">
                                            <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
                                                {selected.prompt}
                                            </pre>
                                        </Field>
                                    </div>

                                    <div className="flex items-center justify-between border-t px-5 py-3">
                                        <p className="text-xs text-muted-foreground">
                                            {added.has(selected.name.toLowerCase())
                                                ? "Already on this node — adding again makes a second copy."
                                                : "Copied onto this node; edit it afterwards."}
                                        </p>
                                        <Button onClick={() => handleAdd(selected)}>
                                            Add extraction
                                        </Button>
                                    </div>
                                </>
                            ) : (
                                <div className="flex flex-1 items-center justify-center p-6">
                                    {!loading && (
                                        <p className="text-sm text-muted-foreground">
                                            Pick one to see what it does.
                                        </p>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </DialogContent>
        </Dialog>
    );
}

function CategoryButton({
    label,
    active,
    onClick,
}: {
    label: string;
    active: boolean;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={cn(
                "w-full rounded-md px-2 py-1.5 text-left text-xs",
                active
                    ? "bg-accent font-medium text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50",
            )}
        >
            {label}
        </button>
    );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div className="space-y-1.5">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {label}
            </p>
            <div className="rounded-md border bg-muted/40 p-3">{children}</div>
        </div>
    );
}
