"use client";

/**
 * Settings -> Personal -> Memory (screen 16; handoff 8 "Memory").
 *
 * Above the list: memory on or off (it starts off until chosen) and what a
 * temporary conversation does. Then the facts as editable rows -- what it
 * is, where it came from, when it was saved, whose it is -- with Yours and
 * the workspace's apart. A row opens its detail: provenance and every change,
 * edit (a new revision, refused if it changed under you), forget (a card you
 * confirm, with Put it back), and share to a team (a preview of exactly who
 * will see it, first). A failed read is an error with Retry, never "nothing
 * remembered".
 */

import { ChevronLeft, ChevronRight, Lock, Users } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
    confirmMemoryFactApiV1MeMemoryFactIdConfirmPost,
    editMemoryFactApiV1MeMemoryFactIdPatch,
    forgetMemoryFactApiV1MeMemoryFactIdForgetPost,
    memoryDestinationsApiV1MeMemoryDestinationsGet,
    memoryFactApiV1MeMemoryFactIdGet,
    myMemoryApiV1MeMemoryGet,
    setMemorySwitchApiV1MeMemorySwitchPut,
    shareMemoryFactApiV1MeMemoryFactIdSharePost,
    shareMemoryPreviewApiV1MeMemoryFactIdSharePreviewGet,
} from "@/client/sdk.gen";
import type { MemoryFact, MemoryOverview, MemorySharePreview,SettingsCard } from "@/client/types.gen";
import { EmptyState } from "@/components/EmptyState";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { ErrorState } from "@/components/shell/ErrorState";
import { SettingsSection } from "@/components/shell/SettingsSection";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

import { SettingsCardPanel } from "../SettingsCardPanel";
import { INPUT } from "../SettingsForm";
import { TemporaryConversationButton } from "../TemporaryConversationButton";

type Phase = "loading" | "failed" | "ready";

function when(iso?: string | null): string {
    if (!iso) return "";
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function title(fact: MemoryFact): string {
    // A sentence taught as it is has a made-up key; the sentence is the fact.
    if (fact.key.startsWith("note_")) return fact.value;
    const about = fact.subject ? `${fact.subject.key} · ` : "";
    return `${about}${fact.key.replace(/_/g, " ")}`;
}

function MemorySwitch({ overview, onChanged }: { overview: MemoryOverview; onChanged: (next: MemoryOverview) => void }) {
    const [pending, setPending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [shown, setShown] = useState(overview.memory_enabled);
    useEffect(() => setShown(overview.memory_enabled), [overview.memory_enabled]);

    const toggle = async (on: boolean) => {
        setShown(on); // optimistic, rolled back below on any refusal
        setPending(true);
        setError(null);
        try {
            const result = await setMemorySwitchApiV1MeMemorySwitchPut({ body: { memory_enabled: on, revision: overview.revision } });
            if (result.error || !result.data) {
                setShown(overview.memory_enabled);
                setError(
                    result.response?.status === 409
                        ? "This was changed somewhere else. Showing what is saved now."
                        : `Your change was not saved. ${detailFromError(result.error, "Try again.")}`,
                );
                const fresh = await myMemoryApiV1MeMemoryGet();
                if (fresh.data) onChanged(fresh.data);
                return;
            }
            onChanged(result.data);
        } catch {
            setShown(overview.memory_enabled);
            setError("Your change was not saved. Check your connection.");
        } finally {
            setPending(false);
        }
    };

    return (
        <div>
            <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3">
                <span className="text-sm">
                    <span className="font-medium">Remember things from my conversations</span>
                    <span className="block text-xs text-muted-foreground">
                        {shown
                            ? "On: what you tell Decibyl can be kept, and shows here."
                            : overview.memory_chosen
                              ? "Off: nothing new is kept. What is below stays until you forget it."
                              : "Off until you choose. Nothing new is kept."}
                    </span>
                </span>
                <Switch
                    checked={shown}
                    disabled={pending}
                    onCheckedChange={(on) => void toggle(on)}
                    aria-label="Remember things from my conversations"
                    data-testid="memory-switch"
                />
            </label>
            <p aria-live="polite" className="text-xs text-muted-foreground">
                {pending ? "Saving…" : ""}
            </p>
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}

function FactRow({ fact, onOpen, selected }: { fact: MemoryFact; onOpen: () => void; selected: boolean }) {
    return (
        <li className="motion-m6-enter">
            <button
                type="button"
                onClick={onOpen}
                aria-current={selected || undefined}
                className={cn(
                    "motion-m1 flex min-h-11 w-full items-start gap-3 px-3 py-3 text-left hover:bg-accent",
                    selected && "bg-accent",
                )}
                data-testid="memory-row"
            >
                <span className="min-w-0 flex-1">
                    <span className="block break-words text-sm font-medium">{title(fact)}</span>
                    {!fact.key.startsWith("note_") && <span className="block break-words text-sm">{fact.value}</span>}
                    <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                        <span>{fact.source.line}</span>
                        <span>· {when(fact.saved_at)}</span>
                        {fact.status === "learned" && (
                            <span className="rounded-[var(--radius-pill)] border border-[#705500]/50 px-1.5 text-[#705500] dark:text-amber-300">
                                Suggested
                            </span>
                        )}
                        <span className="inline-flex items-center gap-1 rounded-[var(--radius-pill)] border border-border px-1.5">
                            {fact.scope === "mine" ? <Lock aria-hidden className="h-3 w-3" /> : <Users aria-hidden className="h-3 w-3" />}
                            {fact.scope === "mine" ? "Only you" : "Workspace"}
                        </span>
                    </span>
                </span>
                <ChevronRight aria-hidden className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
            </button>
        </li>
    );
}

function ShareFlow({ fact, onShared }: { fact: MemoryFact; onShared: () => void }) {
    const [places, setPlaces] = useState<{ organization_id: number; name: string }[] | null>(null);
    const [destination, setDestination] = useState<number | null>(null);
    const [preview, setPreview] = useState<MemorySharePreview | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [done, setDone] = useState<string | null>(null);

    useEffect(() => {
        void (async () => {
            const result = await memoryDestinationsApiV1MeMemoryDestinationsGet();
            if (result.error || !result.data) {
                setError("Could not load your workspaces.");
                return;
            }
            const list = result.data as { organization_id: number; name: string }[];
            setPlaces(list);
            if (list.length === 1) setDestination(list[0].organization_id);
        })();
    }, []);

    useEffect(() => {
        if (destination === null) return;
        setPreview(null);
        void (async () => {
            const result = await shareMemoryPreviewApiV1MeMemoryFactIdSharePreviewGet({
                path: { fact_id: fact.id },
                query: { destination },
            });
            if (result.error || !result.data) {
                setError(detailFromError(result.error, "Could not prepare the preview."));
                return;
            }
            setPreview(result.data);
        })();
    }, [destination, fact.id]);

    if (done) return <p className="text-sm text-[#075A39] dark:text-emerald-300">{done}</p>;
    return (
        <div className="flex flex-col gap-3" data-testid="share-flow">
            {places && places.length > 1 && (
                <label className="text-sm">
                    <span className="mb-1 block font-medium">Share with</span>
                    <select
                        className={INPUT}
                        value={destination ?? ""}
                        onChange={(event) => setDestination(event.target.value ? Number(event.target.value) : null)}
                    >
                        <option value="">Choose a workspace</option>
                        {places.map((place) => (
                            <option key={place.organization_id} value={place.organization_id}>
                                {place.name}
                            </option>
                        ))}
                    </select>
                </label>
            )}
            {places && places.length === 0 && <p className="text-sm text-muted-foreground">You are not in a team workspace to share with.</p>}
            {preview && (
                <section aria-label="What will be shared" className="rounded-[var(--radius)] border border-border p-3 text-sm" data-testid="share-preview">
                    <p className="font-medium">Share with {String(preview.destination.name)}</p>
                    <p className="mt-2 rounded-md bg-muted/50 px-2 py-1.5">
                        {preview.key.replace(/_/g, " ")}: {preview.value}
                    </p>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
                        {preview.lines.map((line) => (
                            <li key={line}>{line}</li>
                        ))}
                    </ul>
                    <Button
                        type="button"
                        className="motion-m1 mt-3 min-h-11 md:min-h-9"
                        disabled={busy}
                        onClick={async () => {
                            setBusy(true);
                            setError(null);
                            const result = await shareMemoryFactApiV1MeMemoryFactIdSharePost({
                                path: { fact_id: fact.id },
                                body: { destination_organization_id: Number(preview.destination.organization_id), expected_value: preview.value },
                            });
                            setBusy(false);
                            if (result.error) {
                                setError(
                                    result.response?.status === 409
                                        ? "It changed since the preview. Nothing was shared."
                                        : detailFromError(result.error, "Not shared. Try again."),
                                );
                                return;
                            }
                            setDone(`Shared with ${String(preview.destination.name)}.`);
                            onShared();
                        }}
                    >
                        Share exactly this
                    </Button>
                </section>
            )}
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}

function FactDetail({ factId, onBack, onChanged }: { factId: number; onBack: () => void; onChanged: () => void }) {
    const [fact, setFact] = useState<MemoryFact | null>(null);
    const [phase, setPhase] = useState<Phase>("loading");
    const [editing, setEditing] = useState(false);
    const [value, setValue] = useState("");
    const [message, setMessage] = useState<string | null>(null);
    const [card, setCard] = useState<SettingsCard | null>(null);
    const [sharing, setSharing] = useState(false);
    const [busy, setBusy] = useState(false);

    const load = useCallback(async () => {
        setPhase("loading");
        const result = await memoryFactApiV1MeMemoryFactIdGet({ path: { fact_id: factId } });
        if (result.error || !result.data) {
            setPhase("failed");
            return;
        }
        setFact(result.data);
        setValue(result.data.value);
        setPhase("ready");
    }, [factId]);

    useEffect(() => {
        setCard(null);
        setEditing(false);
        setSharing(false);
        setMessage(null);
        void load();
    }, [load]);

    if (phase === "loading") return <Skeleton className="h-48 w-full" />;
    if (phase === "failed" || !fact) {
        return <ErrorState title="Could not open this memory" description="It may have been forgotten or shared." onRetry={() => void load()} />;
    }

    const saveEdit = async () => {
        setBusy(true);
        setMessage(null);
        const result = await editMemoryFactApiV1MeMemoryFactIdPatch({ path: { fact_id: fact.id }, body: { value, expected_value: fact.value } });
        setBusy(false);
        if (result.response?.status === 409) {
            const stored = (result.error as { detail?: { stored?: MemoryFact } })?.detail?.stored;
            setMessage(`This changed while you were editing. Saved now: “${stored?.value ?? "?"}”. Yours is still in the box.`);
            if (stored) setFact(stored);
            return;
        }
        if (result.error || !result.data) {
            setMessage(detailFromError(result.error, "Your change was not saved. Try again."));
            return;
        }
        setFact(result.data);
        setEditing(false);
        onChanged();
    };

    return (
        <article aria-label="Memory detail" className="flex flex-col gap-4" data-testid="memory-detail">
            <button type="button" onClick={onBack} className="flex min-h-11 items-center gap-1 text-sm text-muted-foreground lg:hidden">
                <ChevronLeft aria-hidden className="h-4 w-4" /> All memories
            </button>
            <header>
                <h2 className="break-words text-base font-semibold">{title(fact)}</h2>
                <p className="text-xs text-muted-foreground">{fact.scope === "mine" ? "Only you can see this." : "Everyone in the workspace and its agents can use this."}</p>
            </header>

            {editing ? (
                <div className="flex flex-col gap-2">
                    <label htmlFor="memory-edit" className="text-sm font-medium">
                        What it should say
                    </label>
                    <textarea id="memory-edit" className="min-h-24 w-full rounded-[8px] border border-[#7B8491]/60 p-3 text-base md:text-sm" value={value} onChange={(event) => setValue(event.target.value)} />
                    <div className="flex gap-2">
                        <Button type="button" className="motion-m1 min-h-11 md:min-h-9" disabled={busy || !value.trim()} onClick={() => void saveEdit()}>
                            {busy ? "Saving…" : "Save"}
                        </Button>
                        <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => { setEditing(false); setValue(fact.value); setMessage(null); }}>
                            Discard
                        </Button>
                    </div>
                </div>
            ) : (
                <p className="whitespace-pre-wrap break-words rounded-md bg-muted/40 p-3 text-sm">{fact.value}</p>
            )}
            {message && (
                <p role="alert" className="text-sm text-destructive">
                    {message}
                </p>
            )}

            <section aria-label="Where it came from">
                <h3 className="text-sm font-medium">Where it came from</h3>
                <dl className="mt-1 grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-sm">
                    <dt className="text-muted-foreground">Source</dt>
                    <dd>{fact.source.line}</dd>
                    <dt className="text-muted-foreground">First seen</dt>
                    <dd>{when(fact.source.first_seen_at) || "—"}</dd>
                    <dt className="text-muted-foreground">Last seen</dt>
                    <dd>{when(fact.source.last_seen_at) || "—"}</dd>
                    <dt className="text-muted-foreground">Confirmed</dt>
                    <dd>{fact.source.confirmed_at ? when(fact.source.confirmed_at) : "Not yet: a suggestion"}</dd>
                    {(fact.source.times_seen ?? 0) > 1 && (
                        <>
                            <dt className="text-muted-foreground">Heard</dt>
                            <dd>{fact.source.times_seen} times</dd>
                        </>
                    )}
                </dl>
            </section>

            <section aria-label="Changes">
                <h3 className="text-sm font-medium">Changes</h3>
                {(fact.history ?? []).length === 0 ? (
                    <p className="text-sm text-muted-foreground">No changes since it was saved.</p>
                ) : (
                    <ol className="mt-1 flex flex-col gap-1 text-sm">
                        {(fact.history ?? []).map((change, index) => (
                            <li key={`${change.at}-${index}`} className="break-words">
                                <span className="text-muted-foreground">{when(change.at)} · </span>
                                {change.change === "edited" && `Changed from “${change.before}” to “${change.after}”`}
                                {change.change === "confirmed" && "Kept as right"}
                                {change.change === "forgotten" && "Forgotten"}
                                {change.change === "restored" && "Put back"}
                                {change.change === "shared" && (change.note ?? "Shared")}
                                {change.change === "created" && "Saved"}
                                {change.by_you ? " (you)" : ""}
                            </li>
                        ))}
                    </ol>
                )}
            </section>

            {!card && !sharing && (
                <div className="flex flex-wrap gap-2" role="group" aria-label="Actions">
                    {fact.status === "learned" && (
                        <Button
                            type="button"
                            className="motion-m1 min-h-11 md:min-h-9"
                            onClick={async () => {
                                const result = await confirmMemoryFactApiV1MeMemoryFactIdConfirmPost({ path: { fact_id: fact.id } });
                                if (result.data) {
                                    setFact(result.data);
                                    onChanged();
                                } else setMessage(detailFromError(result.error, "Not kept. Try again."));
                            }}
                        >
                            It is right, keep it
                        </Button>
                    )}
                    <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => setEditing(true)}>
                        Edit
                    </Button>
                    {fact.scope === "mine" && (
                        <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => setSharing(true)}>
                            Share with a team
                        </Button>
                    )}
                    <Button
                        type="button"
                        variant="outline"
                        className="motion-m1 min-h-11 text-destructive md:min-h-9"
                        onClick={async () => {
                            const result = await forgetMemoryFactApiV1MeMemoryFactIdForgetPost({ path: { fact_id: fact.id } });
                            if (result.data) setCard(result.data);
                            else setMessage(detailFromError(result.error, "Could not ask to forget it. Try again."));
                        }}
                    >
                        Forget
                    </Button>
                </div>
            )}
            {card && <SettingsCardPanel card={card} onSettled={() => onChanged()} />}
            {sharing && (
                <div className="flex flex-col gap-2">
                    <ShareFlow fact={fact} onShared={onChanged} />
                    <Button type="button" variant="ghost" className="min-h-11 self-start md:min-h-9" onClick={() => setSharing(false)}>
                        Close
                    </Button>
                </div>
            )}
        </article>
    );
}

export function MemorySettings() {
    const { user, loading: authLoading } = useAuth();
    const router = useRouter();
    const params = useSearchParams();
    const selected = params?.get("fact") ? Number(params.get("fact")) : null;
    const [overview, setOverview] = useState<MemoryOverview | null>(null);
    const [phase, setPhase] = useState<Phase>("loading");
    const [tab, setTab] = useState<"mine" | "workspace">("mine");
    const started = useRef(false);

    const load = useCallback(async () => {
        try {
            const result = await myMemoryApiV1MeMemoryGet();
            if (result.error || !result.data) {
                // Keep what was on screen (stale but labelled), never an empty list.
                setPhase((was) => (was === "ready" ? "ready" : "failed"));
                return;
            }
            setOverview(result.data);
            setPhase("ready");
        } catch {
            setPhase((was) => (was === "ready" ? "ready" : "failed"));
        }
    }, []);

    useEffect(() => {
        if (authLoading || !user || started.current) return;
        started.current = true;
        void load();
    }, [authLoading, user, load]);

    const facts = useMemo(() => (overview ? (tab === "mine" ? overview.mine : overview.workspace) : []), [overview, tab]);
    const open = (id: number | null) => router.push(id ? `/settings/memory?fact=${id}` : "/settings/memory", { scroll: false });

    return (
        <>
            <PageHeader title="Memory" description="What Decibyl remembers, where each thing came from, and who can use it." />
            <PageBody className="max-w-[1040px]">
                {phase === "loading" && (
                    <div className="flex flex-col gap-3" aria-busy="true">
                        {[0, 1, 2, 3].map((i) => (
                            <Skeleton key={i} className="h-16 w-full" />
                        ))}
                    </div>
                )}
                {phase === "failed" && (
                    <ErrorState
                        title="Could not load what is remembered"
                        description="This is not an empty memory: nothing was loaded. Try again."
                        onRetry={() => {
                            setPhase("loading");
                            void load();
                        }}
                    />
                )}
                {phase === "ready" && overview && (
                    <div className={cn("grid gap-6", selected && "lg:grid-cols-[minmax(0,1fr)_400px]")}>
                        <div className={cn("flex min-w-0 flex-col gap-6", selected && "hidden lg:flex")}>
                            <SettingsSection id="memory-switch" title="Memory" scope="Just you">
                                <MemorySwitch overview={overview} onChanged={setOverview} />
                            </SettingsSection>
                            <SettingsSection id="temporary" title="Temporary conversation" scope="Just you">
                                <TemporaryConversationButton retention={overview.temporary_retention} />
                            </SettingsSection>
                            <div>
                                <div role="tablist" aria-label="Whose memories" className="mb-2 inline-flex rounded-[10px] border border-border p-0.5">
                                    {(["mine", "workspace"] as const).map((key) => (
                                        <button
                                            key={key}
                                            role="tab"
                                            type="button"
                                            aria-selected={tab === key}
                                            onClick={() => setTab(key)}
                                            className={cn(
                                                "motion-m1 min-h-11 rounded-md px-3 text-sm md:min-h-8",
                                                tab === key ? "bg-primary text-primary-foreground" : "text-muted-foreground",
                                            )}
                                        >
                                            {key === "mine" ? `Yours (${overview.mine.length})` : `Workspace (${overview.workspace.length})`}
                                        </button>
                                    ))}
                                </div>
                                {tab === "mine" && !overview.personal_memory && (
                                    <p className="mb-2 text-sm text-muted-foreground" data-testid="personal-memory-off">
                                        Personal memory is not on in this workspace: what you tell Decibyl here is kept as the workspace&apos;s.
                                    </p>
                                )}
                                {facts.length === 0 ? (
                                    <EmptyState
                                        title={tab === "mine" ? "Nothing of yours is remembered." : "Nothing is remembered for the workspace yet."}
                                        description={tab === "mine" && !overview.memory_enabled ? "Memory is off, so nothing new is kept." : undefined}
                                    />
                                ) : (
                                    <ul className="divide-y divide-border rounded-[var(--radius)] border border-border" aria-label={tab === "mine" ? "Your memories" : "The workspace's memories"}>
                                        {facts.map((fact) => (
                                            <FactRow key={fact.id} fact={fact} selected={selected === fact.id} onOpen={() => open(fact.id)} />
                                        ))}
                                    </ul>
                                )}
                            </div>
                        </div>
                        {selected && (
                            <aside className="motion-m3-enter min-w-0 lg:sticky lg:top-4 lg:self-start lg:rounded-[var(--radius)] lg:border lg:border-border lg:p-4">
                                <FactDetail factId={selected} onBack={() => open(null)} onChanged={() => void load()} />
                            </aside>
                        )}
                    </div>
                )}
            </PageBody>
        </>
    );
}

export default MemorySettings;
