"use client";

/**
 * One contact (PEOPLE.md): who they are to you (the brief, editable), how
 * to reach them, where the contact came from, and the timeline of what
 * Decibyl did with them. Sharing shows a colleague the card only -- never
 * the brief, never the history -- and says so before it happens.
 */

import { ArrowLeft, Calendar, Loader2, Mail, MessageCircle, Pencil, Phone, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    deletePersonApiV1PeoplePersonIdDelete,
    editPersonApiV1PeoplePersonIdPatch,
    myPersonApiV1PeoplePersonIdGet,
    peopleColleaguesApiV1PeopleColleaguesGet,
    rewriteBriefApiV1PeoplePersonIdBriefPost,
    sharePersonApiV1PeoplePersonIdSharePost,
    unsharePersonApiV1PeoplePersonIdShareUserIdDelete,
} from "@/client/sdk.gen";
import type { Colleague, PersonDetail } from "@/client/types.gen";
import { ErrorState } from "@/components/shell/ErrorState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { ago, channelLabel, sourceLabel } from "@/lib/people/format";

const CHANNEL_ICONS = {
    call: Phone,
    whatsapp: MessageCircle,
    email: Mail,
    meeting: Calendar,
} as const;

export function PersonPage({ personId }: { personId: string }) {
    const { user, loading: authLoading } = useAuth();
    const router = useRouter();
    const [person, setPerson] = useState<PersonDetail | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [missing, setMissing] = useState(false);
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState("");
    const [busy, setBusy] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [colleagues, setColleagues] = useState<Colleague[] | null>(null);
    const [shareWith, setShareWith] = useState<string>("");

    const load = useCallback(async () => {
        const response = await myPersonApiV1PeoplePersonIdGet({
            path: { person_id: personId },
        });
        if (response.response?.status === 404) {
            setMissing(true);
            return;
        }
        if (response.error || !response.data) {
            setError(detailFromResult(response, "Could not load this contact"));
            return;
        }
        setError(null);
        setPerson(response.data);
    }, [personId]);

    const fetched = useRef(false);
    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void load();
    }, [authLoading, user, load]);

    const run = async (
        key: string,
        call: () => Promise<{
            error?: unknown;
            data?: PersonDetail | unknown;
            response?: Response;
        }>,
        fallback: string,
    ) => {
        setBusy(key);
        setActionError(null);
        const result = await call();
        setBusy(null);
        if (result.error) {
            setActionError(detailFromResult(result, fallback));
            return false;
        }
        if (result.data && typeof result.data === "object" && "id" in (result.data as object)) setPerson(result.data as PersonDetail);
        return true;
    };

    const saveBrief = async () => {
        const ok = await run(
            "brief",
            () =>
                editPersonApiV1PeoplePersonIdPatch({
                    path: { person_id: personId },
                    body: { brief: draft.trim() || null },
                }),
            "Could not save the brief",
        );
        if (ok) setEditing(false);
    };

    const openShare = async () => {
        if (colleagues !== null) return;
        const response = await peopleColleaguesApiV1PeopleColleaguesGet();
        setColleagues(response.data?.colleagues ?? []);
    };

    if (missing) {
        return (
            <div className="mx-auto w-full max-w-[720px] px-4 py-10">
                <ErrorState
                    title="No such contact"
                    description="It may have been deleted or merged into another."
                    action={
                        <Button asChild variant="outline" className="min-h-11">
                            <Link href="/people">All people</Link>
                        </Button>
                    }
                />
            </div>
        );
    }

    return (
        <div className="mx-auto flex w-full max-w-[720px] flex-col gap-5 px-4 py-6 md:px-6">
            <Link href="/people" className="inline-flex min-h-11 items-center gap-1 self-start text-sm text-muted-foreground hover:text-foreground">
                <ArrowLeft aria-hidden className="h-4 w-4" />
                People
            </Link>

            {error && <ErrorState title="Could not load this contact" description={error} onRetry={() => void load()} />}
            {!error && !person && (
                <div aria-busy className="flex flex-col gap-2">
                    <Skeleton className="h-10 w-2/3" />
                    <Skeleton className="h-24 w-full" />
                    <Skeleton className="h-32 w-full" />
                </div>
            )}

            {person && (
                <>
                    <header>
                        <h1 className="break-words text-2xl font-semibold leading-8" data-testid="person-name">
                            {person.name}
                        </h1>
                        {(person.company || person.relation) && (
                            <p className="text-sm text-muted-foreground">{[person.relation, person.company].filter(Boolean).join(", ")}</p>
                        )}
                        <ul className="mt-2 flex flex-col gap-1 text-sm">
                            {(person.phones ?? []).map((p) => (
                                <li key={p}>
                                    <a href={`tel:${p}`} className="inline-flex min-h-11 items-center gap-2 break-all hover:underline md:min-h-0">
                                        <Phone aria-hidden className="h-4 w-4 text-muted-foreground" />
                                        {p}
                                    </a>
                                </li>
                            ))}
                            {(person.emails ?? []).map((e) => (
                                <li key={e}>
                                    <a href={`mailto:${e}`} className="inline-flex min-h-11 items-center gap-2 break-all hover:underline md:min-h-0">
                                        <Mail aria-hidden className="h-4 w-4 text-muted-foreground" />
                                        {e}
                                    </a>
                                </li>
                            ))}
                        </ul>
                        <p className="mt-2 text-xs text-muted-foreground">From: {(person.sources ?? []).map(sourceLabel).join(" · ")}</p>
                    </header>

                    {actionError && (
                        <p role="alert" className="text-sm text-destructive">
                            {actionError}
                        </p>
                    )}

                    <section aria-label="Brief" className="rounded-[8px] border border-border p-4" data-testid="person-brief">
                        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                            <h2 className="text-sm font-semibold">Who they are to you</h2>
                            {!editing && (
                                <div className="flex gap-1">
                                    <Button
                                        size="sm"
                                        variant="ghost"
                                        className="min-h-11 md:min-h-8"
                                        onClick={() => {
                                            setDraft(person.brief ?? "");
                                            setEditing(true);
                                        }}
                                    >
                                        <Pencil aria-hidden />
                                        Edit
                                    </Button>
                                    {(person.interactions ?? []).length > 0 && (
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            className="min-h-11 md:min-h-8"
                                            disabled={busy === "rewrite"}
                                            onClick={() =>
                                                void run(
                                                    "rewrite",
                                                    () =>
                                                        rewriteBriefApiV1PeoplePersonIdBriefPost({
                                                            path: { person_id: personId },
                                                        }),
                                                    "Could not rewrite the brief",
                                                )
                                            }
                                        >
                                            {busy === "rewrite" ? <Loader2 aria-hidden className="animate-spin" /> : <RefreshCw aria-hidden />}
                                            Rewrite
                                        </Button>
                                    )}
                                </div>
                            )}
                        </div>
                        {editing ? (
                            <div className="flex flex-col gap-2">
                                <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={600} rows={4} aria-label="Brief" />
                                <div className="flex gap-2">
                                    <Button size="sm" className="min-h-11 md:min-h-9" disabled={busy === "brief"} onClick={() => void saveBrief()}>
                                        {busy === "brief" && <Loader2 aria-hidden className="animate-spin" />}
                                        Save
                                    </Button>
                                    <Button size="sm" variant="outline" className="min-h-11 md:min-h-9" onClick={() => setEditing(false)}>
                                        Cancel
                                    </Button>
                                </div>
                            </div>
                        ) : (
                            <>
                                <p className="whitespace-pre-wrap text-sm">
                                    {person.brief || "No brief yet. Decibyl writes one after you are in touch, or write your own."}
                                </p>
                                <p className="mt-2 text-xs text-muted-foreground">
                                    {person.brief && (person.brief_by === "you" ? "Written by you" : "Written by Decibyl")}
                                    {person.brief && person.brief_at && ` · ${ago(person.brief_at)}`}
                                    {person.brief_pending && `${person.brief ? " · " : ""}Updating after the latest contact`}
                                </p>
                            </>
                        )}
                    </section>

                    <section aria-label="What happened">
                        <h2 className="mb-2 text-sm font-semibold">What happened</h2>
                        {(person.interactions ?? []).length === 0 ? (
                            <p className="text-sm text-muted-foreground">
                                Nothing yet. Calls, messages, mail and meetings with them through Decibyl appear here.
                            </p>
                        ) : (
                            <ol className="flex flex-col divide-y divide-border rounded-[8px] border border-border" data-testid="person-timeline">
                                {(person.interactions ?? []).map((item, index) => {
                                    const Icon = CHANNEL_ICONS[item.channel as keyof typeof CHANNEL_ICONS] ?? MessageCircle;
                                    return (
                                        <li key={`${item.at}-${index}`} className="flex gap-3 px-4 py-3">
                                            <Icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                                            <div className="min-w-0 flex-1 text-sm">
                                                <p className="break-words">{item.line}</p>
                                                <p className="text-xs text-muted-foreground">
                                                    {channelLabel(item.channel)}
                                                    {item.direction === "in" ? ", to you" : item.direction === "out" ? ", from you" : ""} · {ago(item.at)}
                                                </p>
                                            </div>
                                        </li>
                                    );
                                })}
                            </ol>
                        )}
                    </section>

                    <section aria-label="Share" className="rounded-[8px] border border-border p-4">
                        <h2 className="text-sm font-semibold">Share this contact</h2>
                        <p className="text-sm text-muted-foreground">
                            A colleague sees the name, numbers, addresses and company. Never the brief or the history.
                        </p>
                        {(person.shared_with ?? []).length > 0 && (
                            <ul className="mt-2 flex flex-col gap-1 text-sm">
                                {(person.shared_with ?? []).map((uid) => (
                                    <li key={uid} className="flex items-center justify-between gap-2">
                                        <span>{colleagues?.find((c) => c.user_id === uid)?.label ?? `Member ${uid}`}</span>
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            className="min-h-11 md:min-h-8"
                                            onClick={() =>
                                                void run(
                                                    "unshare",
                                                    () =>
                                                        unsharePersonApiV1PeoplePersonIdShareUserIdDelete({
                                                            path: { person_id: personId, user_id: uid },
                                                        }),
                                                    "Could not stop sharing",
                                                )
                                            }
                                        >
                                            Stop sharing
                                        </Button>
                                    </li>
                                ))}
                            </ul>
                        )}
                        <div className="mt-2 flex flex-wrap gap-2">
                            <select
                                className="min-h-11 min-w-0 flex-1 rounded-md border border-input bg-background px-3 text-sm md:min-h-9"
                                value={shareWith}
                                onFocus={() => void openShare()}
                                onChange={(e) => setShareWith(e.target.value)}
                                aria-label="Colleague"
                            >
                                <option value="">Choose a colleague</option>
                                {(colleagues ?? []).map((c) => (
                                    <option key={c.user_id} value={c.user_id}>
                                        {c.label}
                                    </option>
                                ))}
                            </select>
                            <Button
                                size="sm"
                                variant="outline"
                                className="min-h-11 md:min-h-9"
                                disabled={!shareWith || busy === "share"}
                                onClick={async () => {
                                    const ok = await run(
                                        "share",
                                        () =>
                                            sharePersonApiV1PeoplePersonIdSharePost({
                                                path: { person_id: personId },
                                                body: { user_id: Number(shareWith) },
                                            }),
                                        "Could not share",
                                    );
                                    if (ok) setShareWith("");
                                }}
                            >
                                Share
                            </Button>
                        </div>
                    </section>

                    <div>
                        <Button
                            variant="ghost"
                            className="min-h-11 text-destructive hover:text-destructive"
                            disabled={busy === "delete"}
                            onClick={async () => {
                                if (!window.confirm(`Delete ${person.name} from your People? Their brief and history go too.`)) return;
                                const ok = await run(
                                    "delete",
                                    () =>
                                        deletePersonApiV1PeoplePersonIdDelete({
                                            path: { person_id: personId },
                                        }),
                                    "Could not delete",
                                );
                                if (ok) router.push("/people");
                            }}
                        >
                            <Trash2 aria-hidden />
                            Delete contact
                        </Button>
                    </div>
                </>
            )}
        </div>
    );
}

export default PersonPage;
