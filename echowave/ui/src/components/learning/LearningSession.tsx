"use client";

/**
 * Screen 13: the lesson, inside Chat.
 *
 * A compact goal header, a short explanation, one practice prompt and a
 * feedback block; the progress page is a secondary link. Ask one baseline
 * question, teach, collect an attempt, give specific feedback, then Try
 * again or Next exercise. Resumes from the last exercise the server holds
 * (api/services/learning/core.py), so a reload or another device opens the
 * same exercise with its attempts.
 *
 * States: profile first (adults, handoff 6), new goal, waiting for the
 * first answer, waiting for an answer, evaluating, correction, completed,
 * interrupted (a failed load or submit keeps the draft and says so), and
 * needs setup when no teacher can run here -- never a made-up lesson.
 *
 * An answer is submitted with one idempotency key until it is marked, so a
 * retry after a dropped connection is counted once.
 */

import {
    BookOpen,
    CheckCircle2,
    CircleDashed,
    Loader2,
    Mic,
    RotateCcw,
    Square,
    X,
    XCircle,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    answerLearningBaselineApiV1LearningGoalsGoalIdBaselinePost,
    learningSessionApiV1LearningGoalsGoalIdSessionGet,
    learningStatusApiV1LearningStatusGet,
    nextLearningExerciseApiV1LearningGoalsGoalIdNextPost,
    saveLearnerProfileApiV1LearningProfilePut,
    startLearningGoalApiV1LearningGoalsPost,
    startLearningReviewApiV1LearningGoalsGoalIdReviewPost,
    submitLearningAttemptApiV1LearningGoalsGoalIdAttemptsPost,
} from "@/client/sdk.gen";
import type { LearningAttempt, LearningSession as Session, LearningStatus } from "@/client/types.gen";
import { ReplyFeedback } from "@/components/channel/ReplyFeedback";
import { ErrorState } from "@/components/shell/ErrorState";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import {
    languageName,
    type LearningRefusal,
    newAttemptKey,
    OUTCOME_LABEL,
    refusalOf,
} from "@/lib/learning";
import { appendDictation, canDictate, useDictation } from "@/lib/useDictation";
import { cn } from "@/lib/utils";

const FIELD =
    "min-h-11 w-full rounded-lg border border-[#7B8491]/60 bg-background px-3 py-2 text-base leading-[1.6] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#245B9A] focus-visible:ring-offset-2";
const LABEL = "text-sm font-medium text-foreground";
const TARGET = "min-h-11";

type Busy = null | "loading" | "saving" | "evaluating" | "writing";

export function LearningSession({
    goalId,
    reviewSkillId,
    threadId,
    topic,
    onGoalChange,
    onClose,
}: {
    /** The goal to resume; null starts a new one. */
    goalId: string | null;
    /** For a new one: the course named in Chat, filled in. */
    topic?: string | null;
    /** Open on a review of this skill (from Today). */
    reviewSkillId?: number | null;
    /** The Chat conversation it is started from. */
    threadId?: string | null;
    onGoalChange: (goalId: string | null) => void;
    onClose: () => void;
}) {
    const { user, loading: authLoading } = useAuth();
    // Keyed on the id: a new user object for the same person is not a
    // reason to reload the lesson under their hands.
    const userId = user?.id ?? null;
    const feedbackOn = useFeature("reply_feedback");
    const [status, setStatus] = useState<LearningStatus | null>(null);
    const [statusError, setStatusError] = useState(false);
    const [session, setSession] = useState<Session | null>(null);
    const [sessionError, setSessionError] = useState<string | null>(null);
    const [busy, setBusy] = useState<Busy>(null);
    const [notice, setNotice] = useState<LearningRefusal | null>(null);

    const loadStatus = useCallback(async () => {
        setStatusError(false);
        const response = await learningStatusApiV1LearningStatusGet();
        if (response.error || !response.data) {
            setStatusError(true);
            return;
        }
        setStatus(response.data);
    }, []);

    const loadSession = useCallback(async (id: string) => {
        setSessionError(null);
        setBusy("loading");
        const response = await learningSessionApiV1LearningGoalsGoalIdSessionGet({ path: { goal_id: id } });
        setBusy(null);
        if (response.error || !response.data) {
            setSessionError(refusalOf(response.error).message);
            return null;
        }
        setSession(response.data);
        return response.data;
    }, []);

    useEffect(() => {
        if (authLoading || userId === null) return;
        void loadStatus();
    }, [authLoading, userId, loadStatus]);

    const reviewStarted = useRef<string | null>(null);
    useEffect(() => {
        if (authLoading || userId === null || !goalId) {
            setSession(null);
            return;
        }
        void (async () => {
            const loaded = await loadSession(goalId);
            const key = `${goalId}:${reviewSkillId}`;
            if (loaded && reviewSkillId != null && reviewStarted.current !== key) {
                reviewStarted.current = key;
                setBusy("writing");
                const response = await startLearningReviewApiV1LearningGoalsGoalIdReviewPost({
                    path: { goal_id: goalId },
                    body: { skill_id: reviewSkillId },
                });
                setBusy(null);
                if (response.error || !response.data) setNotice(refusalOf(response.error));
                else setSession(response.data);
            }
        })();
    }, [authLoading, userId, goalId, reviewSkillId, loadSession]);

    const header = (
        <div className="flex items-start justify-between gap-3 border-b border-border/70 pb-3">
            <div className="min-w-0">
                <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    <BookOpen aria-hidden className="h-3.5 w-3.5" />
                    Learning
                    {status?.teacher === "sample" && (
                        <span className="whitespace-nowrap rounded border border-border px-1 py-px normal-case tracking-normal">
                            Sample teacher
                        </span>
                    )}
                </p>
                <h2 className="mt-1 break-words text-lg font-semibold leading-snug">
                    {session?.goal.title ?? "Learn something new"}
                </h2>
                {session && (
                    <p className="text-sm text-muted-foreground">
                        Explained in {languageName(session.goal.explanation_language)}
                        {session.goal.studying_for ? ` · For ${session.goal.studying_for}` : ""}
                    </p>
                )}
            </div>
            <div className="flex shrink-0 items-center gap-1">
                {session && (
                    <Link
                        href={`/learning/${session.goal.goal_id}`}
                        className={cn(
                            TARGET,
                            "inline-flex items-center rounded-md px-3 text-sm underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#245B9A]",
                        )}
                    >
                        Progress
                    </Link>
                )}
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="motion-m1 h-11 w-11"
                    aria-label="Back to the conversation"
                    onClick={onClose}
                >
                    <X aria-hidden className="h-4 w-4" />
                </Button>
            </div>
        </div>
    );

    let body: React.ReactNode;
    if (statusError) {
        body = <ErrorState title="Could not open learning" description="Nothing was lost." onRetry={() => void loadStatus()} />;
    } else if (!status) {
        body = <p className="py-6 text-sm text-muted-foreground" role="status">Opening your lesson…</p>;
    } else if (status.state !== "available") {
        body = (
            <div className="rounded-lg border border-[#705500]/40 bg-[#705500]/5 p-4 text-sm" data-testid="learning-needs-setup">
                <p className="font-medium">Lessons need setup</p>
                <p className="mt-1 text-muted-foreground">{status.reason ?? "Lessons cannot be written here yet."}</p>
            </div>
        );
    } else if (!status.profile.adult_confirmed) {
        body = (
            <ProfileStep
                status={status}
                onSaved={(profile) => setStatus({ ...status, profile })}
            />
        );
    } else if (!goalId) {
        body = (
            <NewGoal
                status={status}
                threadId={threadId ?? null}
                topic={topic ?? null}
                onStarted={(started) => {
                    setSession(started);
                    onGoalChange(started.goal.goal_id);
                }}
            />
        );
    } else if (sessionError && !session) {
        body = (
            <ErrorState
                title="Could not load this lesson"
                description={sessionError}
                onRetry={() => void loadSession(goalId)}
            />
        );
    } else if (!session) {
        body = <p className="py-6 text-sm text-muted-foreground" role="status">Loading your lesson…</p>;
    } else {
        body = (
            <SessionBody
                session={session}
                busy={busy}
                setBusy={setBusy}
                onSession={setSession}
                onNotice={setNotice}
                feedbackOn={feedbackOn}
            />
        );
    }

    return (
        <section
            aria-label="Learning session"
            className="motion-m3-enter flex min-h-0 flex-1 flex-col overflow-y-auto px-4 pb-6 pt-3 sm:px-6"
            data-testid="learning-session"
        >
            {header}
            <div className="mt-4 flex flex-col gap-4">
                {notice && (
                    <div role="alert" className="flex items-start justify-between gap-2 rounded-lg border border-[#772322]/40 bg-[#772322]/5 p-3 text-sm">
                        <span>{notice.message}</span>
                        <button
                            type="button"
                            aria-label="Dismiss"
                            className="motion-m1 -m-2 flex h-11 w-11 shrink-0 items-center justify-center rounded"
                            onClick={() => setNotice(null)}
                        >
                            <X aria-hidden className="h-4 w-4" />
                        </button>
                    </div>
                )}
                {body}
            </div>
        </section>
    );
}

function ProfileStep({
    status,
    onSaved,
}: {
    status: LearningStatus;
    onSaved: (profile: LearningStatus["profile"]) => void;
}) {
    const profile = status.profile;
    const [adult, setAdult] = useState(false);
    const [kind, setKind] = useState(profile.learner_kind || "adult");
    const [studyingFor, setStudyingFor] = useState(profile.studying_for ?? "");
    const [language, setLanguage] = useState(
        profile.explanation_language ?? profile.suggested_language ?? "en-IN",
    );
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const save = async () => {
        if (!adult) {
            setError("Learning is for adults for now. Confirm you are 18 or older.");
            return;
        }
        setSaving(true);
        setError(null);
        const response = await saveLearnerProfileApiV1LearningProfilePut({
            body: {
                adult_confirmed: true,
                learner_kind: kind as "adult" | "student" | "course_learner",
                studying_for: studyingFor.trim() || null,
                explanation_language: language,
                revision: profile.revision,
            },
        });
        setSaving(false);
        if (response.error || !response.data) {
            setError(refusalOf(response.error).message);
            return;
        }
        onSaved(response.data);
    };

    return (
        <form
            className="flex flex-col gap-4"
            onSubmit={(event) => {
                event.preventDefault();
                void save();
            }}
        >
            <p className="text-[15px] leading-[1.6] text-muted-foreground">
                A few things first, so lessons fit you. You can change them later.
            </p>
            <label className="flex min-h-11 items-start gap-3 text-sm">
                <input
                    type="checkbox"
                    className="mt-1 h-5 w-5"
                    checked={adult}
                    onChange={(event) => setAdult(event.target.checked)}
                />
                <span>I am 18 or older.</span>
            </label>
            <fieldset className="flex flex-col gap-2">
                <legend className={LABEL}>I am learning as</legend>
                {[
                    ["adult", "An adult learning for myself"],
                    ["student", "A student"],
                    ["course_learner", "Someone on a course"],
                ].map(([value, label]) => (
                    <label key={value} className="flex min-h-11 items-center gap-3 text-sm">
                        <input
                            type="radio"
                            name="learner-kind"
                            className="h-5 w-5"
                            value={value}
                            checked={kind === value}
                            onChange={() => setKind(value)}
                        />
                        {label}
                    </label>
                ))}
            </fieldset>
            <label className="flex flex-col gap-1.5">
                <span className={LABEL}>Course or exam you are studying for (optional)</span>
                <input
                    className={FIELD}
                    value={studyingFor}
                    maxLength={160}
                    placeholder="For example: CA Foundation, IELTS, Class 12 boards"
                    onChange={(event) => setStudyingFor(event.target.value)}
                />
            </label>
            <LanguagePicker languages={status.languages} value={language} onChange={setLanguage} />
            {error && <p role="alert" className="text-sm text-[#772322] dark:text-red-300">{error}</p>}
            <div>
                <Button type="submit" className={cn("motion-m1", TARGET)} disabled={saving}>
                    {saving && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                    {saving ? "Saving…" : "Continue"}
                </Button>
            </div>
        </form>
    );
}

function LanguagePicker({
    languages,
    value,
    onChange,
}: {
    languages: string[];
    value: string;
    onChange: (value: string) => void;
}) {
    return (
        <label className="flex flex-col gap-1.5">
            <span className={LABEL}>Explain in</span>
            <select className={cn(FIELD, TARGET)} value={value} onChange={(event) => onChange(event.target.value)}>
                {languages.map((tag) => (
                    <option key={tag} value={tag}>
                        {languageName(tag)}
                    </option>
                ))}
            </select>
        </label>
    );
}

function NewGoal({
    status,
    threadId,
    topic,
    onStarted,
}: {
    status: LearningStatus;
    threadId: string | null;
    topic: string | null;
    onStarted: (session: Session) => void;
}) {
    const [title, setTitle] = useState(topic ?? "");
    const [studyingFor, setStudyingFor] = useState(status.profile.studying_for ?? "");
    const [language, setLanguage] = useState(
        status.profile.explanation_language ?? status.profile.suggested_language ?? "en-IN",
    );
    const [notesOpen, setNotesOpen] = useState(false);
    const [notes, setNotes] = useState("");
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [sensitive, setSensitive] = useState<LearningRefusal | null>(null);

    const start = async (confirmSensitive: boolean) => {
        if (!title.trim()) {
            setError("Say what you want to learn.");
            return;
        }
        setSaving(true);
        setError(null);
        const response = await startLearningGoalApiV1LearningGoalsPost({
            body: {
                title: title.trim(),
                studying_for: studyingFor.trim() || null,
                explanation_language: language,
                material: notes.trim() || null,
                thread_id: threadId,
                confirm_sensitive: confirmSensitive,
            },
        });
        setSaving(false);
        if (response.error || !response.data) {
            const refusal = refusalOf(response.error);
            if (refusal.code === "confirm_sensitive") setSensitive(refusal);
            else setError(refusal.message);
            return;
        }
        setSensitive(null);
        onStarted(response.data);
    };

    return (
        <form
            className="flex flex-col gap-4"
            onSubmit={(event) => {
                event.preventDefault();
                void start(false);
            }}
        >
            <label className="flex flex-col gap-1.5">
                <span className={LABEL}>What do you want to learn?</span>
                <input
                    className={FIELD}
                    value={title}
                    maxLength={200}
                    placeholder="Any subject: GST basics, Spanish, statistics, the sitar…"
                    onChange={(event) => setTitle(event.target.value)}
                    autoFocus
                />
            </label>
            <label className="flex flex-col gap-1.5">
                <span className={LABEL}>Course or exam (optional)</span>
                <input
                    className={FIELD}
                    value={studyingFor}
                    maxLength={160}
                    onChange={(event) => setStudyingFor(event.target.value)}
                />
            </label>
            <LanguagePicker languages={status.languages} value={language} onChange={setLanguage} />
            {notesOpen ? (
                <label className="flex flex-col gap-1.5">
                    <span className={LABEL}>Notes to learn from (optional)</span>
                    <span className="text-sm text-muted-foreground">
                        Paste a chapter or your class notes. Lessons will say when they come from them.
                    </span>
                    <textarea
                        className={cn(FIELD, "min-h-32")}
                        value={notes}
                        maxLength={12000}
                        onChange={(event) => setNotes(event.target.value)}
                    />
                </label>
            ) : (
                <div>
                    <Button type="button" variant="outline" className={cn("motion-m1", TARGET)} onClick={() => setNotesOpen(true)}>
                        Add notes to learn from
                    </Button>
                </div>
            )}
            {sensitive && (
                <div role="alert" className="rounded-lg border border-[#705500]/40 bg-[#705500]/5 p-3 text-sm" data-testid="learning-sensitive">
                    <p>{sensitive.message}</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                        <Button type="button" className={cn("motion-m1", TARGET)} disabled={saving} onClick={() => void start(true)}>
                            Save it
                        </Button>
                        <Button type="button" variant="outline" className={cn("motion-m1", TARGET)} onClick={() => setSensitive(null)}>
                            Change it
                        </Button>
                    </div>
                </div>
            )}
            {error && <p role="alert" className="text-sm text-[#772322] dark:text-red-300">{error}</p>}
            <div>
                <Button type="submit" className={cn("motion-m1", TARGET)} disabled={saving}>
                    {saving && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                    {saving ? "Writing your first question…" : "Start"}
                </Button>
            </div>
        </form>
    );
}

function AnswerBox({
    label,
    value,
    onChange,
    disabled,
}: {
    label: string;
    value: string;
    onChange: (value: string) => void;
    disabled?: boolean;
}) {
    // A spoken answer lands in the box as words to read and fix before it
    // is sent (screen 13: an editable transcript before submission).
    const dictation = useDictation((transcript) => onChange(appendDictation(value, transcript)));
    const [dictateOk, setDictateOk] = useState(false);
    useEffect(() => setDictateOk(canDictate()), []);
    return (
        <div className="flex flex-col gap-1.5">
            <label htmlFor="learning-answer" className={LABEL}>
                {label}
            </label>
            <textarea
                id="learning-answer"
                className={cn(FIELD, "min-h-28")}
                value={value}
                maxLength={8000}
                disabled={disabled}
                onChange={(event) => onChange(event.target.value)}
            />
            {dictateOk && (
                <div className="flex items-center gap-2">
                    <Button
                        type="button"
                        variant="ghost"
                        className={cn("motion-m1", TARGET)}
                        disabled={disabled || dictation.transcribing}
                        aria-pressed={dictation.listening}
                        onClick={() => (dictation.listening ? dictation.stop() : void dictation.start())}
                    >
                        {dictation.listening ? <Square aria-hidden className="h-4 w-4" /> : <Mic aria-hidden className="h-4 w-4" />}
                        {dictation.listening ? "Stop" : dictation.transcribing ? "Writing it down…" : "Dictate"}
                    </Button>
                    {dictation.error && <span className="text-sm text-[#772322] dark:text-red-300">{dictation.error}</span>}
                </div>
            )}
        </div>
    );
}

function Marking({ attempt }: { attempt: LearningAttempt }) {
    const passed = attempt.outcome === "passed";
    return (
        <div
            className={cn(
                "motion-m2-enter rounded-lg border p-4",
                passed ? "border-[#075A39]/40 bg-[#075A39]/5" : "border-[#705500]/40 bg-[#705500]/5",
            )}
            data-testid="learning-feedback"
            data-outcome={attempt.outcome}
        >
            <p className="flex items-center gap-1.5 font-medium">
                {passed ? (
                    <CheckCircle2 aria-hidden className="h-4 w-4 text-[#075A39] dark:text-emerald-300" />
                ) : (
                    <RotateCcw aria-hidden className="h-4 w-4 text-[#705500] dark:text-amber-300" />
                )}
                {OUTCOME_LABEL[attempt.outcome] ?? attempt.outcome}
            </p>
            <p className="mt-2 text-[15px] leading-[1.6]">{attempt.feedback}</p>
            {attempt.rubric_results.length > 0 && (
                <ul className="mt-3 flex flex-col gap-1.5 text-sm" aria-label="What the answer showed">
                    {attempt.rubric_results.map((result, index) => (
                        <li key={`${result.criterion}-${index}`} className="flex items-start gap-2">
                            {result.met ? (
                                <CheckCircle2 aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-[#075A39] dark:text-emerald-300" />
                            ) : (
                                <XCircle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-[#772322] dark:text-red-300" />
                            )}
                            <span className="min-w-0 break-words">
                                <span className="font-medium">{result.met ? "Shown" : "Not yet"}: </span>
                                {result.criterion}
                                {result.note ? ` — ${result.note}` : ""}
                            </span>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

function SessionBody({
    session,
    busy,
    setBusy,
    onSession,
    onNotice,
    feedbackOn,
}: {
    session: Session;
    busy: Busy;
    setBusy: (busy: Busy) => void;
    onSession: (session: Session) => void;
    onNotice: (notice: LearningRefusal | null) => void;
    feedbackOn: boolean;
}) {
    const goalId = session.goal.goal_id;
    const exerciseId = session.exercise?.exercise_id ?? null;
    const [answer, setAnswer] = useState("");
    // One key per answer until it is marked: a retry is the same attempt.
    const attemptKey = useRef<string | null>(null);
    const [interrupted, setInterrupted] = useState<string | null>(null);
    const [lessonAnswer, setLessonAnswer] = useState<{ verdict: "yes" | "not_quite"; reasons: string[] } | undefined>();
    const answerRef = useRef<HTMLDivElement | null>(null);

    // A new exercise starts with an empty box and a fresh key. Only a *new*
    // one: the box already starts empty, and resetting on mount too wiped
    // anything typed before React got round to running the mount effects
    // (they run a task after the box is on screen).
    const shownExercise = useRef(exerciseId);
    useEffect(() => {
        if (shownExercise.current === exerciseId) return;
        shownExercise.current = exerciseId;
        setAnswer("");
        attemptKey.current = null;
        setInterrupted(null);
    }, [exerciseId]);

    const act = async (kind: Busy, call: () => Promise<{ data?: Session; error?: unknown }>) => {
        setBusy(kind);
        onNotice(null);
        setInterrupted(null);
        try {
            const response = await call();
            if (response.error || !response.data) {
                onNotice(refusalOf(response.error));
                return false;
            }
            onSession(response.data);
            return true;
        } catch {
            setInterrupted("The connection dropped. Your answer is kept here. Try again.");
            return false;
        } finally {
            setBusy(null);
        }
    };

    const sendBaseline = () =>
        act("evaluating", () =>
            answerLearningBaselineApiV1LearningGoalsGoalIdBaselinePost({
                path: { goal_id: goalId },
                body: { answer: answer.trim() },
            }),
        );

    const next = (easier = false) =>
        act("writing", () =>
            nextLearningExerciseApiV1LearningGoalsGoalIdNextPost({
                path: { goal_id: goalId },
                body: { easier },
            }),
        );

    const submit = async () => {
        if (!exerciseId || !answer.trim()) return;
        attemptKey.current ??= newAttemptKey();
        setBusy("evaluating");
        onNotice(null);
        setInterrupted(null);
        try {
            const response = await submitLearningAttemptApiV1LearningGoalsGoalIdAttemptsPost({
                path: { goal_id: goalId },
                body: { exercise_id: exerciseId, answer: answer.trim() },
                headers: { "Idempotency-Key": attemptKey.current },
            });
            if (response.error || !response.data) {
                onNotice(refusalOf(response.error));
                return;
            }
            attemptKey.current = null;
            setAnswer("");
            onSession(response.data.session);
        } catch {
            setInterrupted("Your answer was not marked: the connection dropped. It is kept here. Try again.");
        } finally {
            setBusy(null);
        }
    };

    const attempts = session.attempts ?? [];
    const lastAttempt = attempts.length > 0 ? attempts[attempts.length - 1] : null;
    const working = busy !== null;

    if (session.state === "baseline") {
        return (
            <form
                className="flex flex-col gap-4"
                onSubmit={(event) => {
                    event.preventDefault();
                    void sendBaseline();
                }}
            >
                <p className="text-sm text-muted-foreground">First, one question so we start in the right place.</p>
                <p className="text-[16px] leading-[1.6]">{session.baseline_question}</p>
                <AnswerBox label="Your answer" value={answer} onChange={setAnswer} disabled={working} />
                {interrupted && <p role="alert" className="text-sm text-[#772322] dark:text-red-300">{interrupted}</p>}
                <div>
                    <Button type="submit" className={cn("motion-m1", TARGET)} disabled={working || !answer.trim()}>
                        {working && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                        {busy === "evaluating" ? "Reading your answer…" : "Send answer"}
                    </Button>
                </div>
            </form>
        );
    }

    if (session.state === "no_exercise" || !session.exercise || !session.lesson) {
        return (
            <div className="flex flex-col gap-3">
                {session.baseline_feedback && <p className="text-[15px] leading-[1.6]">{session.baseline_feedback}</p>}
                <div>
                    <Button type="button" className={cn("motion-m1", TARGET)} disabled={working} onClick={() => void next()}>
                        {working && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                        {busy === "writing" ? "Writing your lesson…" : "Start the first lesson"}
                    </Button>
                </div>
            </div>
        );
    }

    const lesson = session.lesson;
    const exercise = session.exercise;
    const waiting = session.state === "waiting_for_answer";
    const correcting = session.state === "correction";
    const completed = session.state === "completed";

    return (
        <div className="flex flex-col gap-4" ref={answerRef}>
            <article aria-label="Lesson" className="flex flex-col gap-2">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {exercise.kind === "review" ? "Review" : "Lesson"} · {lesson.skill}
                </p>
                <h3 className="text-base font-semibold leading-snug">{lesson.objective}</h3>
                <p className="whitespace-pre-wrap text-[16px] leading-[1.625]">{lesson.explanation}</p>
                <p className="text-xs text-muted-foreground" data-testid="learning-source">
                    {lesson.source_kind === "material" ? "From your notes" : "General explanation, not from your notes"}
                </p>
                {lesson.source_kind === "material" && lesson.sources.length > 0 && (
                    <ul className="flex flex-col gap-1">
                        {lesson.sources.map((source) => (
                            <li key={source}>
                                <blockquote className="border-l-2 border-border pl-3 text-sm text-muted-foreground">
                                    {source}
                                </blockquote>
                            </li>
                        ))}
                    </ul>
                )}
            </article>

            <div className="rounded-lg border border-border bg-muted/30 p-4">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Practice</p>
                <p className="mt-1 whitespace-pre-wrap text-[16px] leading-[1.6]">{exercise.prompt}</p>
            </div>

            {lastAttempt && !waiting && <Marking attempt={lastAttempt} />}

            {busy === "evaluating" && (
                <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
                    <CircleDashed aria-hidden className="motion-continuous h-4 w-4 animate-spin" />
                    Checking your answer…
                </p>
            )}

            {(waiting || correcting) && (
                <form
                    className="flex flex-col gap-3"
                    onSubmit={(event) => {
                        event.preventDefault();
                        void submit();
                    }}
                >
                    <AnswerBox
                        label={correcting ? "Try again" : "Your answer"}
                        value={answer}
                        onChange={setAnswer}
                        disabled={working}
                    />
                    {interrupted && <p role="alert" className="text-sm text-[#772322] dark:text-red-300">{interrupted}</p>}
                    <div className="flex flex-wrap gap-2">
                        <Button type="submit" className={cn("motion-m1", TARGET)} disabled={working || !answer.trim()}>
                            {busy === "evaluating" && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                            {correcting ? "Submit again" : "Submit answer"}
                        </Button>
                        {correcting && (
                            <Button
                                type="button"
                                variant="outline"
                                className={cn("motion-m1", TARGET)}
                                disabled={working}
                                onClick={() => void next()}
                            >
                                {busy === "writing" ? "Writing…" : "Next exercise"}
                            </Button>
                        )}
                        {correcting && (
                            <Button
                                type="button"
                                variant="ghost"
                                className={cn("motion-m1", TARGET)}
                                disabled={working}
                                onClick={() => void next(true)}
                            >
                                Try a smaller step
                            </Button>
                        )}
                    </div>
                </form>
            )}

            {completed && (
                <div className="flex flex-col gap-3">
                    <div className="flex flex-wrap gap-2">
                        <Button type="button" className={cn("motion-m1", TARGET)} disabled={working} onClick={() => void next()}>
                            {busy === "writing" && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                            {busy === "writing" ? "Writing your next lesson…" : "Next exercise"}
                        </Button>
                        <Link
                            href={`/learning/${goalId}`}
                            className={cn(
                                TARGET,
                                "inline-flex items-center rounded-md px-3 text-sm underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#245B9A]",
                            )}
                        >
                            See progress
                        </Link>
                    </div>
                    {feedbackOn && (
                        <ReplyFeedback
                            subjectKind="lesson"
                            roomy
                            eventId={lesson.lesson_id}
                            answer={lessonAnswer}
                            onAnswered={setLessonAnswer}
                        />
                    )}
                </div>
            )}
        </div>
    );
}

export default LearningSession;
