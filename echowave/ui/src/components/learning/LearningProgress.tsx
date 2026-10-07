"use client";

/**
 * Screen 14: one learning goal's progress.
 *
 * A 760px page of evidence: skill rows with plain labels ("Practised",
 * "Needs another attempt"), recent exercises and one next step. Every
 * number on it is marked practice -- never messages, never minutes, and no
 * percentage, streak or fluency score. No practice yet says exactly that:
 * no data is not zero ability.
 *
 * Open a skill for its rubric and attempts. The goal's name, language and
 * review reminders change in a small editor that keeps the save contract
 * (a stale save shows what is saved now). Export downloads the whole
 * record; Delete puts up the controls action card, and nothing is deleted
 * until it is approved -- it then runs once, after the undo window.
 *
 * A refresh that fails keeps what was loaded, labelled stale.
 */

import { CheckCircle2, ChevronDown, CircleDashed, Download, Loader2, RotateCcw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    exportLearningGoalApiV1LearningGoalsGoalIdExportGet,
    learningProgressApiV1LearningGoalsGoalIdProgressGet,
    learningSessionApiV1LearningGoalsGoalIdSessionGet,
    learningSkillApiV1LearningGoalsGoalIdSkillsSkillIdGet,
    learningStatusApiV1LearningStatusGet,
    requestLearningDeletionApiV1LearningGoalsGoalIdDeletionPost,
    settleActionApiV1TimelineActionsSettlePost,
    updateLearningGoalApiV1LearningGoalsGoalIdPatch,
} from "@/client/sdk.gen";
import type {
    LearningDeletionCard,
    LearningGoal,
    LearningProgress as Progress,
    LearningSkill,
    LearningSkillDetail,
} from "@/client/types.gen";
import { ActionPreview, type ApprovalStatus } from "@/components/shell/ActionPreview";
import { ErrorState } from "@/components/shell/ErrorState";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { formatDay, languageName, OUTCOME_LABEL, refusalOf, resumeHref } from "@/lib/learning";
import { cn } from "@/lib/utils";

const TARGET = "min-h-11";
const FIELD =
    "min-h-11 w-full rounded-lg border border-[#7B8491]/60 bg-background px-3 py-2 text-base focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#245B9A] focus-visible:ring-offset-2";

function SkillIcon({ status }: { status: string }) {
    if (status === "practised") {
        return <CheckCircle2 aria-hidden className="h-4 w-4 shrink-0 text-[#075A39] dark:text-emerald-300" />;
    }
    if (status === "needs_another_attempt") {
        return <RotateCcw aria-hidden className="h-4 w-4 shrink-0 text-[#705500] dark:text-amber-300" />;
    }
    return <CircleDashed aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />;
}

export function LearningProgress({ goalId }: { goalId: string }) {
    const { user, loading: authLoading } = useAuth();
    const userId = user?.id ?? null;
    const [progress, setProgress] = useState<Progress | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [stale, setStale] = useState<string | null>(null);
    const [loadedAt, setLoadedAt] = useState<Date | null>(null);
    const [languages, setLanguages] = useState<string[]>([]);
    const [open, setOpen] = useState<number | null>(null);
    const [editing, setEditing] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);

    const load = useCallback(async () => {
        const response = await learningProgressApiV1LearningGoalsGoalIdProgressGet({ path: { goal_id: goalId } });
        if (response.error || !response.data) {
            const message = refusalOf(response.error).message;
            // Keep what was shown, labelled, rather than blanking it.
            setProgress((had) => {
                if (had) setStale(message);
                else setError(message);
                return had;
            });
            return;
        }
        setError(null);
        setStale(null);
        setProgress(response.data);
        setLoadedAt(new Date());
    }, [goalId]);

    useEffect(() => {
        if (authLoading || userId === null) return;
        void load();
        void learningStatusApiV1LearningStatusGet().then((r) => setLanguages(r.data?.languages ?? []));
    }, [authLoading, userId, load]);

    if (error && !progress) {
        return (
            <ErrorState
                title="Could not load this goal's progress"
                description={error}
                onRetry={() => void load()}
                action={
                    <Link href="/learning" className="text-sm underline underline-offset-4">
                        All learning goals
                    </Link>
                }
            />
        );
    }
    if (!progress) {
        return (
            <div className="mx-auto w-full max-w-[760px] px-4 py-6" role="status" aria-label="Loading progress">
                <div className="h-6 w-1/2 rounded bg-muted" />
                <div className="mt-4 h-24 rounded-lg bg-muted/60" />
            </div>
        );
    }

    const goal = progress.goal;
    return (
        <div className="mx-auto flex w-full max-w-[760px] flex-col gap-6 px-4 py-6 sm:px-6" data-testid="learning-progress">
            <header className="flex flex-col gap-3">
                <Link href="/learning" className="inline-flex min-h-11 w-fit items-center text-sm text-muted-foreground underline-offset-4 hover:underline">
                    All learning goals
                </Link>
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                        <h1 className="break-words text-2xl font-semibold leading-8">{goal.title}</h1>
                        <p className="text-sm text-muted-foreground">
                            Explained in {languageName(goal.explanation_language)}
                            {goal.studying_for ? ` · For ${goal.studying_for}` : ""}
                        </p>
                    </div>
                    <Link
                        href={resumeHref(goal.goal_id)}
                        className={cn(
                            TARGET,
                            "motion-m1 inline-flex items-center rounded-[10px] bg-foreground px-4 text-sm font-medium text-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#245B9A] focus-visible:ring-offset-2",
                        )}
                    >
                        Continue practice
                    </Link>
                </div>
                <GoalMenu
                    goal={goal}
                    onEdit={() => setEditing((was) => !was)}
                    onNotice={setNotice}
                />
                {notice && (
                    <p role="status" className="text-sm text-muted-foreground">
                        {notice}
                    </p>
                )}
                {stale && (
                    <p role="status" className="text-sm text-[#705500] dark:text-amber-300">
                        Could not refresh ({stale}). Showing what was loaded
                        {loadedAt ? ` at ${loadedAt.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}` : ""}.
                    </p>
                )}
            </header>

            {editing && (
                <GoalEditor
                    goal={goal}
                    languages={languages}
                    onSaved={(saved) => {
                        setProgress({ ...progress, goal: saved });
                        setEditing(false);
                    }}
                />
            )}

            <section aria-labelledby="next-step" className="rounded-lg border border-border p-4">
                <h2 id="next-step" className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Next step
                </h2>
                <p className="mt-1 text-[16px]">{progress.next_step.text}</p>
                {progress.next_step.kind === "review" && progress.next_step.skill_id != null && (
                    <Link
                        href={resumeHref(goal.goal_id, progress.next_step.skill_id)}
                        className={cn(TARGET, "mt-2 inline-flex items-center text-sm underline underline-offset-4")}
                    >
                        Start the review
                    </Link>
                )}
            </section>

            <section aria-labelledby="skills">
                <div className="flex items-baseline justify-between gap-2">
                    <h2 id="skills" className="text-base font-semibold">
                        Skills
                    </h2>
                    <p className="text-sm text-muted-foreground">
                        {progress.practice_count === 1
                            ? "1 practice answer marked"
                            : `${progress.practice_count} practice answers marked`}
                    </p>
                </div>
                {progress.state === "no_practice" ? (
                    <p className="mt-3 text-[15px] text-muted-foreground" data-testid="learning-no-practice">
                        No practice yet. That says nothing about what you know — answer an exercise and it shows here.
                    </p>
                ) : (
                    <ul className="mt-3 flex flex-col divide-y divide-border rounded-lg border border-border">
                        {progress.skills.map((skill) => (
                            <SkillRow
                                key={skill.skill_id}
                                goalId={goal.goal_id}
                                skill={skill}
                                open={open === skill.skill_id}
                                onToggle={() => setOpen((was) => (was === skill.skill_id ? null : skill.skill_id))}
                            />
                        ))}
                    </ul>
                )}
            </section>

            {progress.recent.length > 0 && (
                <section aria-labelledby="recent">
                    <h2 id="recent" className="text-base font-semibold">
                        Recent exercises
                    </h2>
                    <ul className="mt-3 flex flex-col gap-3">
                        {progress.recent.map((attempt) => (
                            <li key={attempt.attempt_id} className="motion-m6-enter rounded-lg border border-border p-3">
                                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                                    <span className="flex items-center gap-1.5 font-medium">
                                        <SkillIcon status={attempt.outcome === "passed" ? "practised" : "needs_another_attempt"} />
                                        {OUTCOME_LABEL[attempt.outcome] ?? attempt.outcome}
                                        {attempt.exercise_kind === "review" ? " · Review" : ""}
                                    </span>
                                    <time className="text-muted-foreground" dateTime={attempt.created_at ?? undefined}>
                                        {formatDay(attempt.created_at)}
                                    </time>
                                </div>
                                <p className="mt-1 line-clamp-2 break-words text-sm text-muted-foreground">{attempt.exercise_prompt}</p>
                                <p className="mt-2 break-words text-sm">{attempt.feedback}</p>
                            </li>
                        ))}
                    </ul>
                </section>
            )}
        </div>
    );
}

function SkillRow({
    goalId,
    skill,
    open,
    onToggle,
}: {
    goalId: string;
    skill: LearningSkill;
    open: boolean;
    onToggle: () => void;
}) {
    const [detail, setDetail] = useState<LearningSkillDetail | null>(null);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        if (!open || detail) return;
        void learningSkillApiV1LearningGoalsGoalIdSkillsSkillIdGet({
            path: { goal_id: goalId, skill_id: skill.skill_id },
        }).then((response) => {
            if (response.error || !response.data) setError(refusalOf(response.error).message);
            else setDetail(response.data);
        });
    }, [open, detail, goalId, skill.skill_id]);

    const panelId = `skill-${skill.skill_id}`;
    return (
        <li>
            <button
                type="button"
                className="motion-m1 flex min-h-11 w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2.5 text-left hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#245B9A]"
                aria-expanded={open}
                aria-controls={panelId}
                onClick={onToggle}
            >
                <span className="flex min-w-0 flex-1 items-center gap-2">
                    <SkillIcon status={skill.status} />
                    <span className="min-w-0 break-words font-medium">{skill.name}</span>
                </span>
                <span className="text-sm">{skill.label}</span>
                <span className="w-full pl-6 text-sm text-muted-foreground sm:w-auto sm:pl-0">
                    {skill.evaluated_attempts} marked
                    {skill.next_review_at
                        ? skill.review_due
                            ? " · Review due"
                            : ` · Review ${formatDay(skill.next_review_at)}`
                        : ""}
                </span>
                <ChevronDown aria-hidden className={cn("h-4 w-4 shrink-0 transition-transform", open && "rotate-180")} />
            </button>
            {open && (
                <div id={panelId} className="motion-m2-enter border-t border-border bg-muted/20 px-3 py-3 text-sm">
                    {error && <p role="alert">{error}</p>}
                    {!detail && !error && <p role="status">Loading…</p>}
                    {detail && (
                        <div className="flex flex-col gap-3">
                            <div>
                                <p className="font-medium">What a good answer shows</p>
                                <ul className="mt-1 list-disc pl-5">
                                    {detail.rubric.map((item) => (
                                        <li key={item.criterion}>
                                            <span className="font-medium">{item.criterion}:</span> {item.description}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                            <div>
                                <p className="font-medium">Attempts</p>
                                <ul className="mt-1 flex flex-col gap-2">
                                    {detail.attempts.map((attempt) => (
                                        <li key={attempt.attempt_id} className="break-words">
                                            <span className="font-medium">{OUTCOME_LABEL[attempt.outcome] ?? attempt.outcome}</span>{" "}
                                            <span className="text-muted-foreground">{formatDay(attempt.created_at)}</span>
                                            <p className="text-muted-foreground">“{attempt.answer}”</p>
                                            <p>{attempt.feedback}</p>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </li>
    );
}

function GoalEditor({
    goal,
    languages,
    onSaved,
}: {
    goal: LearningGoal;
    languages: string[];
    onSaved: (goal: LearningGoal) => void;
}) {
    const [title, setTitle] = useState(goal.title);
    const [language, setLanguage] = useState(goal.explanation_language);
    const [reminders, setReminders] = useState(goal.review_reminders);
    const [revision, setRevision] = useState(goal.revision);
    const [state, setState] = useState<"dirty" | "saving" | "saved">("dirty");
    const [error, setError] = useState<string | null>(null);
    const [stored, setStored] = useState<LearningGoal | null>(null);
    const [askSensitive, setAskSensitive] = useState<string | null>(null);

    const save = async (confirmSensitive = false) => {
        setState("saving");
        setError(null);
        const response = await updateLearningGoalApiV1LearningGoalsGoalIdPatch({
            path: { goal_id: goal.goal_id },
            body: {
                title: title.trim(),
                explanation_language: language,
                review_reminders: reminders,
                revision,
                confirm_sensitive: confirmSensitive,
            },
        });
        if (response.error || !response.data) {
            const refusal = refusalOf(response.error);
            setState("dirty");
            if (refusal.code === "conflict" && refusal.stored) {
                // Keep the draft and show what is saved now; never overwrite.
                setStored(refusal.stored as unknown as LearningGoal);
                setRevision((refusal.stored as unknown as LearningGoal).revision);
                setError(refusal.message);
            } else if (refusal.code === "confirm_sensitive") {
                setAskSensitive(refusal.message);
            } else {
                setError("Your change was not saved. Try again.");
            }
            return;
        }
        setState("saved");
        setAskSensitive(null);
        onSaved(response.data);
    };

    return (
        <form
            className="motion-m2-enter flex flex-col gap-3 rounded-lg border border-border p-4"
            aria-label="Edit goal"
            onSubmit={(event) => {
                event.preventDefault();
                void save();
            }}
        >
            <label className="flex flex-col gap-1.5 text-sm font-medium">
                Goal
                <input className={FIELD} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1.5 text-sm font-medium">
                Explain in
                <select className={cn(FIELD, TARGET)} value={language} onChange={(e) => setLanguage(e.target.value)}>
                    {(languages.length ? languages : [language]).map((tag) => (
                        <option key={tag} value={tag}>
                            {languageName(tag)}
                        </option>
                    ))}
                </select>
            </label>
            <label className="flex min-h-11 items-start gap-3 text-sm">
                <input
                    type="checkbox"
                    className="mt-1 h-5 w-5"
                    checked={reminders}
                    onChange={(e) => setReminders(e.target.checked)}
                />
                <span>
                    Remind me about reviews
                    <span className="block text-muted-foreground">
                        Due reviews always show in Today. Reminder messages are not sent yet; this saves your choice for
                        when they are.
                    </span>
                </span>
            </label>
            {stored && (
                <p className="text-sm text-muted-foreground" data-testid="learning-conflict">
                    Saved now: “{stored.title}”, explained in {languageName(stored.explanation_language)}. Save again to
                    keep yours.
                </p>
            )}
            {askSensitive && (
                <div role="alert" className="rounded-lg border border-[#705500]/40 bg-[#705500]/5 p-3 text-sm">
                    <p>{askSensitive}</p>
                    <Button type="button" className={cn("motion-m1 mt-2", TARGET)} onClick={() => void save(true)}>
                        Save it
                    </Button>
                </div>
            )}
            {error && <p role="alert" className="text-sm text-[#772322] dark:text-red-300">{error}</p>}
            <div>
                <Button type="submit" className={cn("motion-m1", TARGET)} disabled={state === "saving"}>
                    {state === "saving" ? "Saving…" : "Save"}
                </Button>
            </div>
        </form>
    );
}

function GoalMenu({
    goal,
    onEdit,
    onNotice,
}: {
    goal: LearningGoal;
    onEdit: () => void;
    onNotice: (notice: string | null) => void;
}) {
    const router = useRouter();
    const [card, setCard] = useState<LearningDeletionCard | null>(null);
    const [status, setStatus] = useState<ApprovalStatus>("pending");
    const [error, setError] = useState<string | null>(null);
    const [exporting, setExporting] = useState(false);
    const poll = useRef<number | null>(null);

    useEffect(
        () => () => {
            if (poll.current !== null) window.clearInterval(poll.current);
        },
        [],
    );

    const exportIt = async () => {
        setExporting(true);
        const response = await exportLearningGoalApiV1LearningGoalsGoalIdExportGet({ path: { goal_id: goal.goal_id } });
        setExporting(false);
        if (response.error || !response.data) {
            onNotice("The export did not download. Try again.");
            return;
        }
        const blob = new Blob([JSON.stringify(response.data, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `learning-${goal.goal_id}.json`;
        link.click();
        URL.revokeObjectURL(url);
        onNotice("Exported. The file is in your downloads.");
    };

    const askToDelete = async () => {
        setError(null);
        const response = await requestLearningDeletionApiV1LearningGoalsGoalIdDeletionPost({ path: { goal_id: goal.goal_id } });
        if (response.error || !response.data) {
            setError(refusalOf(response.error).message);
            return;
        }
        setCard(response.data);
        setStatus("pending");
    };

    const settle = async (verb: "confirm" | "decline") => {
        if (!card) return;
        setStatus("committing");
        setError(null);
        const response = await settleActionApiV1TimelineActionsSettlePost({
            body: {
                event_id: card.event_id,
                verb,
                version: (card.payload.version as string | undefined) ?? null,
            },
        });
        if (response.error) {
            setStatus("pending");
            setError(refusalOf(response.error).message);
            return;
        }
        if (verb === "decline") {
            setStatus("cancelled");
            return;
        }
        setStatus("approved");
        // It runs after the undo window, once. Follow the card, not a timer:
        // only a done card means the goal is gone.
        let checks = 0;
        poll.current = window.setInterval(async () => {
            checks += 1;
            if (checks > 20) {
                // Not done after a minute: say so rather than spin forever.
                if (poll.current !== null) window.clearInterval(poll.current);
                setError("It has not finished yet. Reload this page in a moment to check.");
                return;
            }
            const check = await learningSessionApiV1LearningGoalsGoalIdSessionGet({
                path: { goal_id: goal.goal_id },
            });
            if (check.error && refusalOf(check.error).code === "not_found") {
                if (poll.current !== null) window.clearInterval(poll.current);
                router.push("/learning?deleted=1");
            }
        }, 3000);
    };

    return (
        <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2" role="group" aria-label="Goal actions">
                <Button type="button" variant="outline" className={cn("motion-m1", TARGET)} onClick={onEdit}>
                    Edit goal
                </Button>
                <Button
                    type="button"
                    variant="outline"
                    className={cn("motion-m1", TARGET)}
                    disabled={exporting}
                    onClick={() => void exportIt()}
                >
                    {exporting ? <Loader2 aria-hidden className="motion-continuous animate-spin" /> : <Download aria-hidden />}
                    Export
                </Button>
                <Button
                    type="button"
                    variant="outline"
                    className={cn("motion-m1 text-[#772322] dark:text-red-300", TARGET)}
                    disabled={card !== null && status !== "cancelled"}
                    onClick={() => void askToDelete()}
                >
                    <Trash2 aria-hidden />
                    Delete…
                </Button>
            </div>
            {card && (
                <ActionPreview
                    preview={{
                        id: String(card.event_id),
                        version: 1,
                        action: "Delete learning goal",
                        content: goal.title,
                        timing: "After you approve, with a few seconds to change your mind on the thread.",
                        consequence:
                            "Its lessons, answers and reviews are deleted. This cannot be undone. No tasks or memories depend on it.",
                    }}
                    status={status}
                    onApprove={() => void settle("confirm")}
                    onCancel={() => void settle("decline")}
                    className="motion-m2-enter"
                />
            )}
            {status === "approved" && (
                <p role="status" className="text-sm text-muted-foreground">
                    Deleting… This page closes when it is done.
                </p>
            )}
            {error && <p role="alert" className="text-sm text-[#772322] dark:text-red-300">{error}</p>}
        </div>
    );
}

export default LearningProgress;
