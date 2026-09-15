"use client";

/**
 * Skills: procedures a bot is taught, as files.
 *
 * A skill is not a capability. It is how to chase an unpaid invoice, what to
 * check before promising a delivery date, how to run a pipeline review — the
 * procedure. The tools a bot can call are attached separately, and a skill
 * that names an action does not receive it.
 *
 * Installed first, then the rest by division, because what you already have
 * is what you came back for. Adding one to a bot is a multi-select: the set
 * of ticked bots is the whole answer, so unticking removes it from that bot.
 */

import { Check, Plus, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
    getWorkflowsApiV1WorkflowFetchGet,
    listSkillsApiV1SkillsGet,
    setSkillBotsApiV1SkillsBotsPost,
    uninstallSkillApiV1SkillsUninstallPost,
} from "@/client/sdk.gen";
import type { SkillCard as SkillCardType } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { toneFor } from "@/lib/marketplace";
import { cn } from "@/lib/utils";

type Bot = { id: number; name: string };

/** One skill: what it is, and who has it. */
function SkillRow({
    skill,
    onAdd,
    onRemove,
    busy,
}: {
    skill: SkillCardType;
    onAdd: () => void;
    onRemove?: () => void;
    busy: boolean;
}) {
    const on = skill.on_bots ?? [];
    return (
        <div className="flex items-start gap-3 rounded-xl px-2 py-2 hover:bg-muted/40" data-testid="skill-row">
            <span
                aria-hidden="true"
                className={cn(
                    "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-base",
                    toneFor(skill.division),
                )}
            >
                {skill.emoji || skill.title.slice(0, 1).toUpperCase()}
            </span>
            <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{skill.title}</p>
                <p className="line-clamp-2 text-xs text-muted-foreground">{skill.description}</p>
                {on.length > 0 ? (
                    <p className="mt-1 text-xs text-muted-foreground">
                        On {on.map((b) => b.name).join(", ")}
                    </p>
                ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-1">
                <Button
                    size="sm"
                    variant="outline"
                    className="rounded-full"
                    disabled={busy}
                    onClick={onAdd}
                    aria-label={`Add ${skill.title} to a bot`}
                >
                    <Plus className="mr-1 h-3 w-3" aria-hidden="true" />
                    Add to bot
                </Button>
                {onRemove ? (
                    <Button
                        size="sm"
                        variant="ghost"
                        className="rounded-full text-muted-foreground"
                        disabled={busy}
                        onClick={onRemove}
                        aria-label={`Remove ${skill.title}`}
                    >
                        <X className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                ) : null}
            </div>
        </div>
    );
}

export function SkillsShelf({ query }: { query: string }) {
    const { user, loading: authLoading } = useAuth();
    const [installed, setInstalled] = useState<SkillCardType[]>([]);
    const [rest, setRest] = useState<SkillCardType[]>([]);
    const [divisions, setDivisions] = useState<string[]>([]);
    const [credits, setCredits] = useState<{ source: string; license: string }[]>([]);
    const [bots, setBots] = useState<Bot[]>([]);
    const [division, setDivision] = useState<string>("all");
    const [loading, setLoading] = useState(true);
    const [failed, setFailed] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    // The skill whose bots are being picked, and the ticks so far.
    const [picking, setPicking] = useState<SkillCardType | null>(null);
    const [ticked, setTicked] = useState<number[]>([]);

    const load = useCallback(async () => {
        try {
            const response = await listSkillsApiV1SkillsGet();
            if (response.error || !response.data) {
                setFailed(true);
                return;
            }
            setInstalled(response.data.installed ?? []);
            setRest(response.data.skills ?? []);
            setDivisions(response.data.divisions ?? []);
            setCredits(response.data.attributions ?? []);
        } catch {
            setFailed(true);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (authLoading || !user) return;
        void load();
        void (async () => {
            const response = await getWorkflowsApiV1WorkflowFetchGet();
            if (response.error || !response.data) return;
            setBots(response.data.map((w) => ({ id: w.id, name: w.name })));
        })();
    }, [authLoading, user, load]);

    const openPicker = (skill: SkillCardType) => {
        setError(null);
        setPicking(skill);
        setTicked((skill.on_bots ?? []).map((b) => b.id));
    };

    const save = async () => {
        if (!picking) return;
        setBusy(true);
        setError(null);
        const response = await setSkillBotsApiV1SkillsBotsPost({
            body: { slug: picking.slug, workflow_ids: ticked },
        });
        setBusy(false);
        if (response.error) {
            setError(detailFromResult(response, "Could not save that."));
            return;
        }
        setPicking(null);
        await load();
    };

    const remove = async (skill: SkillCardType) => {
        setBusy(true);
        await uninstallSkillApiV1SkillsUninstallPost({ body: { slug: skill.slug } });
        setBusy(false);
        await load();
    };

    const needle = query.trim().toLowerCase();
    const matches = useCallback(
        (s: SkillCardType) =>
            !needle ||
            [s.title, s.description, s.division].some((f) =>
                (f ?? "").toLowerCase().includes(needle),
            ),
        [needle],
    );
    const shownInstalled = useMemo(() => installed.filter(matches), [installed, matches]);
    const shownRest = useMemo(
        () => rest.filter(matches).filter((s) => division === "all" || needle || s.division === division),
        [rest, matches, division, needle],
    );

    if (failed) {
        return (
            <div className="rounded-xl border border-border bg-card py-8 text-center text-sm text-muted-foreground">
                The skills shelf could not be loaded. This is us, not you.
            </div>
        );
    }
    if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>;

    return (
        <>
            {!needle ? (
                <div className="flex flex-wrap gap-2" role="group" aria-label="Divisions">
                    <button
                        type="button"
                        aria-pressed={division === "all"}
                        onClick={() => setDivision("all")}
                        className={cn(
                            "rounded-full border px-3 py-1 text-sm transition-colors",
                            division === "all"
                                ? "border-foreground bg-foreground text-background"
                                : "border-border bg-card hover:bg-muted/40",
                        )}
                    >
                        All
                    </button>
                    {divisions.map((d) => (
                        <button
                            key={d}
                            type="button"
                            aria-pressed={division === d}
                            onClick={() => setDivision((cur) => (cur === d ? "all" : d))}
                            className={cn(
                                "rounded-full border px-3 py-1 text-sm transition-colors",
                                division === d
                                    ? "border-foreground bg-foreground text-background"
                                    : "border-border bg-card hover:bg-muted/40",
                            )}
                        >
                            {d}
                        </button>
                    ))}
                </div>
            ) : null}

            {shownInstalled.length > 0 ? (
                <section className="space-y-2">
                    <h2 className="text-base font-semibold tracking-tight">Installed</h2>
                    <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
                        {shownInstalled.map((skill) => (
                            <SkillRow
                                key={skill.slug}
                                skill={skill}
                                busy={busy}
                                onAdd={() => openPicker(skill)}
                                onRemove={() => void remove(skill)}
                            />
                        ))}
                    </div>
                </section>
            ) : null}

            <section className="space-y-2">
                <h2 className="text-base font-semibold tracking-tight">
                    {shownInstalled.length > 0 ? "Explore" : "Skills"}
                </h2>
                {shownRest.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Nothing else here matches.</p>
                ) : (
                    <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
                        {shownRest.map((skill) => (
                            <SkillRow
                                key={skill.slug}
                                skill={skill}
                                busy={busy}
                                onAdd={() => openPicker(skill)}
                            />
                        ))}
                    </div>
                )}
            </section>

            {credits.length > 0 ? (
                <p className="text-xs text-muted-foreground">
                    Skills from{" "}
                    {credits.map((c, i) => (
                        <span key={c.source}>
                            {i > 0 ? ", " : ""}
                            <a
                                href={`https://github.com/${c.source}`}
                                target="_blank"
                                rel="noreferrer"
                                className="underline underline-offset-4"
                            >
                                {c.source}
                            </a>{" "}
                            ({c.license})
                        </span>
                    ))}
                    .
                </p>
            ) : null}

            <Dialog open={picking !== null} onOpenChange={(open) => !open && setPicking(null)}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Add {picking?.title} to</DialogTitle>
                        <DialogDescription>
                            Tick every bot that should follow this procedure. Unticking one takes
                            it off that bot.
                        </DialogDescription>
                    </DialogHeader>
                    {bots.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                            You have no bots yet. Add one first and this becomes a tick.
                        </p>
                    ) : (
                        <div className="max-h-72 space-y-1 overflow-y-auto">
                            {bots.map((bot) => {
                                const on = ticked.includes(bot.id);
                                return (
                                    <button
                                        key={bot.id}
                                        type="button"
                                        role="checkbox"
                                        aria-checked={on}
                                        aria-label={bot.name}
                                        onClick={() =>
                                            setTicked((cur) =>
                                                cur.includes(bot.id)
                                                    ? cur.filter((id) => id !== bot.id)
                                                    : [...cur, bot.id],
                                            )
                                        }
                                        className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm hover:bg-muted/40"
                                    >
                                        <span
                                            className={cn(
                                                "flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                                                on
                                                    ? "border-foreground bg-foreground text-background"
                                                    : "border-input",
                                            )}
                                        >
                                            {on ? <Check className="h-3 w-3" aria-hidden="true" /> : null}
                                        </span>
                                        {bot.name}
                                    </button>
                                );
                            })}
                        </div>
                    )}
                    {error ? <p className="text-sm text-destructive">{error}</p> : null}
                    <DialogFooter>
                        <Button variant="ghost" onClick={() => setPicking(null)}>
                            Cancel
                        </Button>
                        <Button onClick={() => void save()} disabled={busy || bots.length === 0}>
                            {busy ? "Saving…" : "Save"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}
