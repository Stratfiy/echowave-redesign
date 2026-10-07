"use client";

/**
 * Settings -> Personal -> Saved items (screen 15).
 *
 * Search in one scope at a time -- yours, or the workspace's -- stated on
 * screen, with the shell's ScopedSearch (which drops an answer that arrives
 * for the scope you just left). Below it, the saved items as name / type /
 * date rows, not a card wall; a row opens its preview beside the list, or
 * full screen on a phone. Rename, return to the conversation, download a
 * file, and delete -- which asks first on a card, says what it does not
 * touch, and can be put back.
 */

import { ChevronLeft, FileText, Link2, MessageSquareText, StickyNote } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    deleteSavedItemApiV1MeSavedItemIdDeletePost,
    mySavedApiV1MeSavedGet,
    renameSavedItemApiV1MeSavedItemIdPatch,
    savedItemApiV1MeSavedItemIdGet,
    searchMineApiV1MeSearchGet,
} from "@/client/sdk.gen";
import type { SavedItem, SettingsCard } from "@/client/types.gen";
import { EmptyState } from "@/components/EmptyState";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { ErrorState } from "@/components/shell/ErrorState";
import { ScopedSearch } from "@/components/shell/ScopedSearch";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

import { SettingsCardPanel } from "../SettingsCardPanel";
import { INPUT } from "../SettingsForm";

type Scope = "personal" | "workspace";
type Hit = { type: "saved" | "memory"; id: number; title: string; snippet: string; kind: string; href: string };

const SCOPES = [
    { id: "personal", label: "Yours" },
    { id: "workspace", label: "Workspace" },
] as const;

const ICON = { reply: MessageSquareText, note: StickyNote, file: FileText, link: Link2 } as const;
const KIND = { reply: "Reply", note: "Note", file: "File", link: "Link" } as const;

function day(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function Preview({ id, onBack, onChanged }: { id: number; onBack: () => void; onChanged: () => void }) {
    const [item, setItem] = useState<SavedItem | null>(null);
    const [failed, setFailed] = useState(false);
    const [renaming, setRenaming] = useState(false);
    const [name, setName] = useState("");
    const [card, setCard] = useState<SettingsCard | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setFailed(false);
        const result = await savedItemApiV1MeSavedItemIdGet({ path: { item_id: id } });
        if (result.error || !result.data) {
            setFailed(true);
            return;
        }
        setItem(result.data);
        setName(result.data.title);
    }, [id]);

    useEffect(() => {
        setCard(null);
        setRenaming(false);
        void load();
    }, [load]);

    if (failed) return <ErrorState title="Could not open this item" description="It may have been deleted." onRetry={() => void load()} />;
    if (!item) return <Skeleton className="h-40 w-full" />;
    const fileUrl = (item.file as { url?: string } | null)?.url;

    return (
        <article aria-label="Saved item" className="flex flex-col gap-3" data-testid="saved-preview">
            <button type="button" onClick={onBack} className="flex min-h-11 items-center gap-1 text-sm text-muted-foreground lg:hidden">
                <ChevronLeft aria-hidden className="h-4 w-4" /> All saved items
            </button>
            {renaming ? (
                <form
                    className="flex flex-col gap-2 sm:flex-row"
                    onSubmit={async (event) => {
                        event.preventDefault();
                        setError(null);
                        const result = await renameSavedItemApiV1MeSavedItemIdPatch({ path: { item_id: item.id }, body: { title: name } });
                        if (result.error || !result.data) {
                            setError(detailFromError(result.error, "Not renamed. Try again."));
                            return;
                        }
                        setItem(result.data);
                        setRenaming(false);
                        onChanged();
                    }}
                >
                    <label htmlFor="saved-rename" className="sr-only">
                        Name
                    </label>
                    <input id="saved-rename" className={INPUT} value={name} maxLength={200} onChange={(event) => setName(event.target.value)} />
                    <Button type="submit" className="min-h-11 md:min-h-9" disabled={!name.trim()}>
                        Save
                    </Button>
                </form>
            ) : (
                <h2 className="break-words text-base font-semibold">{item.title}</h2>
            )}
            <p className="text-xs text-muted-foreground">
                {KIND[item.kind as keyof typeof KIND] ?? item.kind} · saved {day(item.created_at)} · {item.visibility === "workspace" ? "Shared with the workspace" : "Only you"}
            </p>
            {item.body && <p className="whitespace-pre-wrap break-words rounded-md bg-muted/40 p-3 text-sm">{item.body}</p>}
            {!card && (
                <div className="flex flex-wrap gap-2">
                    {item.conversation_href && (
                        <Button asChild variant="outline" className="min-h-11 md:min-h-9">
                            <Link href={item.conversation_href}>Back to the conversation</Link>
                        </Button>
                    )}
                    {fileUrl && (
                        <Button asChild variant="outline" className="min-h-11 md:min-h-9">
                            <a href={fileUrl} download>
                                Download
                            </a>
                        </Button>
                    )}
                    {item.mine && (
                        <Button type="button" variant="outline" className="min-h-11 md:min-h-9" onClick={() => setRenaming((v) => !v)}>
                            Rename
                        </Button>
                    )}
                    {item.mine && (
                        <Button
                            type="button"
                            variant="outline"
                            className="min-h-11 text-destructive md:min-h-9"
                            onClick={async () => {
                                const result = await deleteSavedItemApiV1MeSavedItemIdDeletePost({ path: { item_id: item.id } });
                                if (result.data) setCard(result.data);
                                else setError(detailFromError(result.error, "Could not ask to delete it."));
                            }}
                        >
                            Delete
                        </Button>
                    )}
                </div>
            )}
            {card && (
                <>
                    <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                        {(item.deletion_effects ?? []).map((line) => (
                            <li key={line}>{line}</li>
                        ))}
                    </ul>
                    <SettingsCardPanel card={card} onSettled={() => onChanged()} />
                </>
            )}
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
        </article>
    );
}

export function SavedSettings() {
    const { user, loading: authLoading } = useAuth();
    const router = useRouter();
    const params = useSearchParams();
    const selected = params?.get("item") ? Number(params.get("item")) : null;
    const [scope, setScope] = useState<Scope>("personal");
    const [items, setItems] = useState<SavedItem[] | null>(null);
    const [failed, setFailed] = useState(false);
    const latest = useRef(0);

    const load = useCallback(async (which: Scope) => {
        const mine = ++latest.current;
        setFailed(false);
        setItems(null); // a new scope never shows the last scope's rows
        try {
            const result = await mySavedApiV1MeSavedGet({ query: { scope: which } });
            if (mine !== latest.current) return;
            if (result.error || !result.data) {
                setFailed(true);
                return;
            }
            setItems(result.data.items);
        } catch {
            if (mine === latest.current) setFailed(true);
        }
    }, []);

    // On the scope, not on the user object: a new object each render must
    // not reload the list.
    const ready = !authLoading && Boolean(user);
    useEffect(() => {
        if (!ready) return;
        void load(scope);
    }, [ready, scope, load]);

    const open = (id: number | null) => router.push(id ? `/settings/saved?item=${id}` : "/settings/saved", { scroll: false });

    return (
        <>
            <PageHeader title="Saved items" description="What you kept, and search across it -- in one place at a time." />
            <PageBody className="max-w-[1040px]">
                <div className={cn("grid gap-6", selected && "lg:grid-cols-[minmax(0,1fr)_380px]")}>
                    <div className={cn("flex min-w-0 flex-col gap-6", selected && "hidden lg:flex")}>
                        <ScopedSearch<Hit>
                            scopes={SCOPES}
                            scope={scope}
                            onScopeChange={(next) => setScope(next as Scope)}
                            placeholder={scope === "personal" ? "Search your saved items and memories" : "Search the workspace's"}
                            search={async (query, which) => {
                                const result = await searchMineApiV1MeSearchGet({ query: { q: query, scope: which as Scope } });
                                if (result.error || !result.data) throw new Error("search failed");
                                return result.data.results as Hit[];
                            }}
                            resultKey={(hit) => `${hit.type}-${hit.id}`}
                            renderResult={(hit) => (
                                <Link href={hit.href} className="motion-m1 flex min-h-11 flex-col justify-center rounded-md px-3 py-2 hover:bg-accent">
                                    <span className="text-sm">{hit.title}</span>
                                    <span className="text-xs text-muted-foreground">
                                        {hit.type === "memory" ? "Memory" : "Saved"} · {hit.snippet}
                                    </span>
                                </Link>
                            )}
                        />
                        <section aria-label={scope === "personal" ? "Your saved items" : "The workspace's saved items"}>
                            <h2 className="mb-2 text-sm font-medium">{scope === "personal" ? "Your saved items" : "Shared with the workspace"}</h2>
                            {failed && <ErrorState title="Could not load saved items" description="Nothing was changed." onRetry={() => void load(scope)} />}
                            {!failed && items === null && <Skeleton className="h-24 w-full" />}
                            {items && items.length === 0 && (
                                <EmptyState
                                    title={scope === "personal" ? "Nothing saved yet." : "Nothing shared with the workspace yet."}
                                    description="Save a reply from Chat to find it here."
                                />
                            )}
                            {items && items.length > 0 && (
                                <ul className="divide-y divide-border rounded-[var(--radius)] border border-border" data-testid="saved-list">
                                    {items.map((item) => {
                                        const Icon = ICON[item.kind as keyof typeof ICON] ?? FileText;
                                        return (
                                            <li key={item.id} className="motion-m6-enter">
                                                <button
                                                    type="button"
                                                    onClick={() => open(item.id)}
                                                    aria-current={selected === item.id || undefined}
                                                    className={cn("motion-m1 flex min-h-11 w-full items-center gap-3 px-3 py-2 text-left hover:bg-accent", selected === item.id && "bg-accent")}
                                                >
                                                    <Icon aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
                                                    <span className="min-w-0 flex-1 truncate text-sm">{item.title}</span>
                                                    <span className="shrink-0 text-xs text-muted-foreground">{KIND[item.kind as keyof typeof KIND] ?? item.kind}</span>
                                                    <span className="w-14 shrink-0 text-right text-xs text-muted-foreground">{day(item.updated_at)}</span>
                                                </button>
                                            </li>
                                        );
                                    })}
                                </ul>
                            )}
                        </section>
                    </div>
                    {selected && (
                        <aside className="motion-m3-enter min-w-0 lg:sticky lg:top-4 lg:self-start lg:rounded-[var(--radius)] lg:border lg:border-border lg:p-4">
                            <Preview id={selected} onBack={() => open(null)} onChanged={() => void load(scope)} />
                        </aside>
                    )}
                </div>
            </PageBody>
        </>
    );
}

export default SavedSettings;
