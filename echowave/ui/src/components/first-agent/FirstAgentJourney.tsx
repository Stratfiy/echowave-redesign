"use client";

/**
 * The first agent, in three steps: pick a template, name it, hear it.
 *
 * A new account used to land on a wizard that asks eleven questions and then
 * runs a language model to write a flow. That is the right door for a business
 * we have no template for and the wrong first screen for everybody: the person
 * who has just signed up has not decided to build anything yet, they want to
 * hear whether this works. Gnani opens with a template grid and a name field;
 * Vapi with a template grid and a "talk to it" button. This is that shape.
 *
 * Every click reports to PostHog, one event per decision, so the funnel shows
 * where people stop rather than only that they did. Property names are the
 * ones the backend's `workflow_created` already uses where the two overlap.
 *
 * State survives a navigation away — verifying a phone number happens on
 * another screen — through sessionStorage, and is dropped once the flow is
 * finished so a second visit starts clean.
 */

import {
    ArrowLeft,
    ArrowRight,
    Check,
    Headphones,
    Loader2,
    Mic,
    Phone,
    PhoneCall,
    Sparkles,
    UserRound,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import posthog from "posthog-js";
import { useCallback, useEffect, useMemo, useState } from "react";

import { EmbeddedVoiceTester } from "@/app/workflow/[workflowId]/components/workflow-tester/EmbeddedVoiceTester";
import { ManualTextChatPanel } from "@/app/workflow/[workflowId]/components/workflow-tester/ManualTextChatPanel";
import { client } from "@/client/client.gen";
import {
    createWorkflowRunApiV1WorkflowWorkflowIdRunsPost,
    initiateCallApiV1TelephonyInitiateCallPost,
    listNumbersApiV1VerifiedNumbersGet,
} from "@/client/sdk.gen";
import type { VerifiedNumber } from "@/client/types.gen";
import { PostCallSummary } from "@/components/agent/PostCallSummary";
import { BrandLogo } from "@/components/BrandLogo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { PostHogEvent } from "@/constants/posthog-events";
import { SETUP_CALL_URL } from "@/constants/setupCall";
import { WORKFLOW_RUN_MODES } from "@/constants/workflowRunModes";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { announceBalanceChanged } from "@/lib/billing/balanceEvents";
import logger from "@/lib/logger";
import { cn, getRandomId } from "@/lib/utils";

type Step = "pick" | "name" | "hear" | "ready";
type VoiceGender = "male" | "female";
type TestMode = "browser" | "phone";

type Template = {
    id: string;
    name: string;
    vertical: string;
    direction: "inbound" | "outbound" | "message" | "scheduled";
    summary: string;
    languages: string[];
    variables?: { name: string; asks_for: string }[];
    greeting?: string | null;
    suggested_voices?: SuggestedVoice[];
};

type SuggestedVoice = {
    provider: string;
    voice_id: string;
    name: string;
    gender: string;
    language: string;
    blurb?: string;
    sample_url?: string | null;
};

const LANGUAGE_NAMES: Record<string, string> = { en: "English", hi: "Hindi", ta: "Tamil", kn: "Kannada", te: "Telugu" };

/**
 * "Hear it": one chip per suggested voice, a man and a woman in more than
 * one language, with a clip when one has been recorded. A card that says
 * "6 languages" is a claim; a play button is proof.
 */
function VoiceChips({
    voices,
    selected,
    onSelect,
}: {
    voices: SuggestedVoice[];
    selected?: string | null;
    onSelect?: (voice: SuggestedVoice) => void;
}) {
    const [playing, setPlaying] = useState<string | null>(null);
    const play = (voice: SuggestedVoice) => {
        if (!voice.sample_url) return;
        const key = `${voice.voice_id}-${voice.language}`;
        const audio = new Audio(voice.sample_url);
        setPlaying(key);
        audio.onended = () => setPlaying(null);
        void audio.play().catch(() => setPlaying(null));
    };
    if (voices.length === 0) return null;
    return (
        <span className="mt-2 flex flex-wrap gap-1.5" data-testid="voice-chips">
            {voices.map((voice) => {
                const key = `${voice.voice_id}-${voice.language}`;
                return (
                    <button
                        key={key}
                        type="button"
                        onClick={(e) => {
                            e.stopPropagation();
                            onSelect?.(voice);
                            play(voice);
                        }}
                        aria-pressed={selected === voice.voice_id}
                        title={voice.blurb || voice.name}
                        className={cn(
                            "rounded-full border px-2 py-0.5 text-[11px] hover:bg-muted",
                            selected === voice.voice_id
                                ? "border-[var(--accent-brand)] bg-[var(--accent-brand-soft)] font-medium"
                                : "border-border",
                            playing === key && "text-[var(--accent-brand)]",
                        )}
                    >
                        {voice.gender === "female" ? "♀" : "♂"} {voice.name} · {LANGUAGE_NAMES[voice.language] ?? voice.language}
                        {voice.sample_url ? " ▶" : ""}
                    </button>
                );
            })}
        </span>
    );
}

/** Where the flow was, so a detour to verify a number comes back here. */
type Saved = {
    step: Step;
    templateId: string | null;
    voice: VoiceGender | null;
    agentName: string;
    workflowId: number | null;
    runId: number | null;
};

const STORAGE_KEY = "decibyl.firstAgent";
/** Voice bots ring a phone; the other two kinds are tested by typing. */
function isVoice(template: Template | null | undefined): boolean {
    return !template || template.direction === "inbound" || template.direction === "outbound";
}

function whatItDoes(template: Template): string {
    switch (template.direction) {
        case "inbound":
            return "Answers calls";
        case "outbound":
            return "Makes calls";
        case "message":
            return "Replies to messages";
        default:
            return "Runs on a schedule";
    }
}

/** How many bots the door shows. The rest are a link away. */
const FEATURED = 5;

/** What the composer opens with when somebody would rather describe the
 *  bot than pick one. Decibyl builds it from the sentence. */
export const DESCRIBE_PROMPT = "Build me a bot that ";

function steps(voice: boolean): { id: Step; title: string; hint: string }[] {
    return [
        { id: "pick", title: "Pick a bot", hint: "Each one already does the job" },
        { id: "name", title: "Name it", hint: "Name, business, opening line" },
        voice
            ? { id: "hear", title: "Hear it", hint: "A real call, in the browser or to your phone" }
            : { id: "hear", title: "Test it", hint: "Type to it, the way a customer would" },
    ];
}

function readSaved(): Saved | null {
    try {
        const raw = sessionStorage.getItem(STORAGE_KEY);
        return raw ? (JSON.parse(raw) as Saved) : null;
    } catch {
        return null;
    }
}

function writeSaved(saved: Saved | null) {
    try {
        if (saved) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
        else sessionStorage.removeItem(STORAGE_KEY);
    } catch {
        // Private mode or blocked storage: the flow still works, it just does
        // not survive a detour.
    }
}

/** The short label the industry column shows: the part before the dash. */
function industryOf(template: Template): string {
    return template.vertical.split("—")[0].trim();
}

export function FirstAgentJourney() {
    const router = useRouter();
    const { user, loading: authLoading, getAccessToken } = useAuth();
    const firstName = user?.displayName?.split(" ")[0];

    const [step, setStep] = useState<Step>("pick");
    const [templates, setTemplates] = useState<Template[] | null>(null);
    const [templateId, setTemplateId] = useState<string | null>(null);
    const [voice, setVoice] = useState<VoiceGender | null>(null);
    // The exact suggested voice pressed on the card, so the first agent
    // answers in the voice that was just heard rather than a gender's default.
    const [voiceId, setVoiceId] = useState<string | null>(null);
    const [agentName, setAgentName] = useState("");
    const [answers, setAnswers] = useState<Record<string, string>>({});
    const [greeting, setGreeting] = useState("");
    const [greetingTouched, setGreetingTouched] = useState(false);
    const [workflowId, setWorkflowId] = useState<number | null>(null);
    const [runId, setRunId] = useState<number | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [restored, setRestored] = useState(false);

    const template = useMemo(
        () => templates?.find((t) => t.id === templateId) ?? null,
        [templates, templateId],
    );

    // Templates, then whatever a previous visit left behind. Waits for auth:
    // the bearer interceptor is only registered once it has loaded, and a
    // request before that fails silently.
    const userId = user?.id;
    useEffect(() => {
        if (authLoading || !userId) return;
        let cancelled = false;
        void (async () => {
            const response = await client.get({ url: "/api/v1/agent-templates" });
            if (cancelled) return;
            const data = response.error
                ? []
                : ((response.data as { templates?: Template[] } | undefined)?.templates ?? []);
            setTemplates(data);
            const saved = readSaved();
            // A marketplace card arrives with its template in the URL. Read
            // off the window rather than useSearchParams, which would need
            // a Suspense boundary around a page that otherwise renders at
            // once. A resumed agent still wins: it has a workflow already.
            const wanted = new URLSearchParams(window.location.search).get("template");
            if (!saved?.workflowId && wanted && data.some((t) => t.id === wanted)) {
                setTemplateId(wanted);
                setStep("name");
            }
            if (saved?.workflowId) {
                setTemplateId(saved.templateId);
                setVoice(saved.voice);
                setAgentName(saved.agentName);
                setWorkflowId(saved.workflowId);
                setRunId(saved.runId);
                setStep(saved.step === "pick" || saved.step === "name" ? "hear" : saved.step);
            }
            setRestored(true);
            posthog.capture(PostHogEvent.FIRST_AGENT_STARTED, {
                templates_offered: data.length,
                resumed: Boolean(saved?.workflowId),
            });
        })();
        return () => {
            cancelled = true;
        };
    }, [authLoading, userId]);

    useEffect(() => {
        if (!restored) return;
        if (step === "ready") return;
        writeSaved({ step, templateId, voice, agentName, workflowId, runId });
    }, [restored, step, templateId, voice, agentName, workflowId, runId]);

    // The template's own first words, until the person edits them.
    useEffect(() => {
        if (!greetingTouched && template?.greeting) setGreeting(template.greeting);
    }, [template, greetingTouched]);

    const pick = (next: Template) => {
        setTemplateId(next.id);
        setGreetingTouched(false);
        setAnswers({});
        posthog.capture(PostHogEvent.FIRST_AGENT_TEMPLATE_PICKED, {
            template_id: next.id,
            vertical: next.vertical,
            direction: next.direction,
        });
    };

    const pickVoice = (next: VoiceGender | null) => {
        setVoice(next);
        setVoiceId(null);
        posthog.capture(PostHogEvent.FIRST_AGENT_VOICE_PICKED, { voice_gender: next });
    };

    const pickSuggestedVoice = (suggested: SuggestedVoice) => {
        setVoice(suggested.gender === "male" ? "male" : "female");
        setVoiceId(suggested.voice_id);
        posthog.capture(PostHogEvent.FIRST_AGENT_VOICE_PICKED, {
            voice_gender: suggested.gender,
            voice_id: suggested.voice_id,
        });
    };

    const back = (from: Step, to: Step) => {
        posthog.capture(PostHogEvent.FIRST_AGENT_STEP_BACK, { from, to });
        setError(null);
        setStep(to);
    };

    // "Describe your bot": Decibyl's thread, with the sentence started.
    const describe = () => {
        posthog.capture(PostHogEvent.FIRST_AGENT_SCRATCH_CHOSEN, {
            templates_offered: templates?.length ?? 0,
        });
        writeSaved(null);
        router.push(`/overview?say=${encodeURIComponent(DESCRIBE_PROMPT)}`);
    };

    const create = async () => {
        if (!template) return;
        setBusy(true);
        setError(null);
        const variables = Object.fromEntries(
            Object.entries(answers).filter(([, value]) => value.trim()),
        );
        const response = await client.post({
            url: `/api/v1/agent-templates/${template.id}/create`,
            body: {
                voice_gender: voice ?? undefined,
                voice_id: voiceId ?? undefined,
                agent_name: agentName.trim() || undefined,
                variables,
                greeting: greetingTouched ? greeting.trim() : undefined,
                source: "first_agent",
            },
        });
        setBusy(false);
        if (response.error) {
            const message = detailFromResult(response, "Could not create the agent.");
            logger.error(`First agent create failed: ${message}`);
            posthog.capture(PostHogEvent.FIRST_AGENT_CREATE_FAILED, {
                template_id: template.id,
                reason: message,
            });
            setError(message);
            return;
        }
        const created = response.data as { id?: number } | undefined;
        if (created?.id == null) {
            setError("The agent was created but could not be opened. It is in your list.");
            return;
        }
        setWorkflowId(created.id);
        posthog.capture(PostHogEvent.FIRST_AGENT_CREATED, {
            workflow_id: created.id,
            template_id: template.id,
            voice_gender: voice,
            renamed: Boolean(agentName.trim()),
            variables_answered: Object.keys(variables).length,
            greeting_changed: greetingTouched,
        });
        setStep("hear");
    };

    const finish = (next: "number" | "agent" | "recording") => {
        posthog.capture(PostHogEvent.FIRST_AGENT_FINISHED, {
            workflow_id: workflowId,
            next,
        });
        writeSaved(null);
    };

    const displayName = agentName.trim() || template?.name || "Your agent";

    return (
        <div className="min-h-screen px-4 py-6 sm:px-8">
            <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[280px_1fr]">
                <JourneyRail
                    step={step}
                    voice={isVoice(template)}
                    templateName={template?.name}
                    agentSummary={
                        step === "hear" || step === "ready"
                            ? `${displayName}${template ? ` · ${template.languages.slice(0, 2).join(", ")}` : ""}`
                            : undefined
                    }
                    firstName={firstName}
                />

                <section className="rounded-[var(--radius-large)] border border-border bg-card p-6 sm:p-8">
                    {step === "pick" && (
                        <PickStep
                            templates={templates}
                            selected={templateId}
                            voice={voice}
                            voiceId={voiceId}
                            onPick={pick}
                            onVoice={pickVoice}
                            onSuggestedVoice={pickSuggestedVoice}
                            onContinue={() => setStep("name")}
                            onDescribe={describe}
                        />
                    )}
                    {step === "name" && template && (
                        <NameStep
                            template={template}
                            agentName={agentName}
                            answers={answers}
                            greeting={greeting}
                            busy={busy}
                            error={error}
                            onName={setAgentName}
                            onAnswer={(key, value) => setAnswers((prev) => ({ ...prev, [key]: value }))}
                            onGreeting={(value) => {
                                setGreetingTouched(true);
                                setGreeting(value);
                            }}
                            onBack={() => back("name", "pick")}
                            onContinue={() => void create()}
                        />
                    )}
                    {step === "hear" && workflowId !== null && (
                        <HearStep
                            workflowId={workflowId}
                            voice={isVoice(template)}
                            displayName={displayName}
                            getAccessToken={getAccessToken}
                            onRun={setRunId}
                            onBack={() => back("hear", "name")}
                            onDone={() => setStep("ready")}
                        />
                    )}
                    {step === "ready" && workflowId !== null && (
                        <ReadyStep
                            workflowId={workflowId}
                            voice={isVoice(template)}
                            runId={runId}
                            displayName={displayName}
                            onFinish={finish}
                        />
                    )}
                </section>
            </div>
        </div>
    );
}

// ---------------------------------------------------------------------------
// The rail: logo, the three steps and what was chosen at each, a way to a person.
// ---------------------------------------------------------------------------

function JourneyRail({
    step,
    voice,
    templateName,
    agentSummary,
    firstName,
}: {
    step: Step;
    voice: boolean;
    templateName?: string;
    agentSummary?: string;
    firstName?: string;
}) {
    const STEPS = steps(voice);
    const index = step === "ready" ? STEPS.length : STEPS.findIndex((s) => s.id === step);
    const chosen: Partial<Record<Step, string | undefined>> = {
        pick: templateName,
        name: agentSummary,
    };

    return (
        <aside className="flex flex-col rounded-[var(--radius-large)] border border-border bg-card p-5 lg:min-h-[calc(100vh-3rem)]">
            <Link href="/" className="inline-flex items-center" aria-label="Decibyl home">
                <BrandLogo className="h-6" />
            </Link>
            <h1 className="mt-8 text-xl font-semibold leading-snug">
                {step === "ready"
                    ? "Your first bot is live."
                    : firstName
                      ? `Hi ${firstName}, let's build your first bot.`
                      : "Let's build your first bot."}
            </h1>

            <ol className="mt-8 space-y-5" aria-label="Steps">
                {STEPS.map((item, i) => {
                    const done = i < index;
                    const current = i === index;
                    return (
                        <li key={item.id} className="flex gap-3">
                            <span
                                className={cn(
                                    "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                                    done && "border-transparent bg-primary text-primary-foreground",
                                    current && "border-[var(--accent-brand)] text-[var(--accent-brand)]",
                                    !done && !current && "border-border text-muted-foreground",
                                )}
                                aria-hidden
                            >
                                {done ? <Check className="h-3.5 w-3.5" /> : i + 1}
                            </span>
                            <div className="min-w-0">
                                <p className={cn("text-sm font-medium", !done && !current && "text-muted-foreground")}>
                                    {item.title}
                                </p>
                                <p className="truncate text-xs text-muted-foreground">
                                    {(done && chosen[item.id]) || item.hint}
                                </p>
                            </div>
                        </li>
                    );
                })}
            </ol>

            <div className="mt-auto pt-8">
                <p className="text-xs text-muted-foreground">Having trouble?</p>
                <a
                    href={SETUP_CALL_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={() => posthog.capture(PostHogEvent.FIRST_AGENT_HELP_CLICKED, { step })}
                    className="mt-1 inline-flex items-center gap-1.5 text-sm font-medium underline-offset-4 hover:underline"
                >
                    <UserRound className="h-4 w-4" />
                    Talk to us
                </a>
            </div>
        </aside>
    );
}

// ---------------------------------------------------------------------------
// Step 1 — pick a template
// ---------------------------------------------------------------------------

/** A colour for a teammate's tile, steady per name. Six from the accent's
 *  family so a row of cards reads as a team rather than a rainbow. */
const TILE_COLOURS = [
    "bg-rose-100 text-rose-900",
    "bg-amber-100 text-amber-900",
    "bg-emerald-100 text-emerald-900",
    "bg-sky-100 text-sky-900",
    "bg-violet-100 text-violet-900",
    "bg-teal-100 text-teal-900",
];

function tileColour(name: string): string {
    let hash = 0;
    for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
    return TILE_COLOURS[hash % TILE_COLOURS.length];
}

function initials(name: string): string {
    const words = name.split(/\s+/).filter(Boolean);
    return (words[0]?.[0] ?? "") + (words[1]?.[0] ?? "");
}

/**
 * "Meet your team": the templates as teammates you hire, the way Wingman
 * introduces its people -- a face, a name, what they do, and Hire. A card
 * is one working agent for that job; the whole shelf is a link away.
 */
function PickStep({
    templates,
    selected,
    voice,
    voiceId,
    onPick,
    onVoice,
    onSuggestedVoice,
    onContinue,
    onDescribe,
}: {
    templates: Template[] | null;
    selected: string | null;
    voice: VoiceGender | null;
    voiceId: string | null;
    onPick: (template: Template) => void;
    onVoice: (voice: VoiceGender | null) => void;
    onSuggestedVoice: (voice: SuggestedVoice) => void;
    onContinue: () => void;
    onDescribe: () => void;
}) {
    const chosen = templates?.find((t) => t.id === selected);
    const featured = templates?.slice(0, FEATURED) ?? null;
    const hire = (t: Template) => {
        onPick(t);
        onContinue();
    };

    return (
        <div className="space-y-6">
            <header className="flex flex-wrap items-end justify-between gap-3">
                <div>
                    <h2 className="text-2xl font-semibold tracking-tight">Pick a bot</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                        Each one already does the job. Add one to your team, name it, and try it in a minute.
                    </p>
                </div>
                <Link
                    href="/marketplace"
                    className="inline-flex items-center gap-1 text-sm font-medium text-[var(--accent-brand)] underline-offset-4 hover:underline"
                >
                    More bots
                    <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </Link>
            </header>

            {featured === null ? (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-busy>
                    {[0, 1, 2, 3, 4, 5].map((i) => (
                        <div key={i} className="h-52 animate-pulse rounded-2xl bg-muted" />
                    ))}
                </div>
            ) : featured.length === 0 ? (
                <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
                    No bots are available on this deployment. Describe yours instead.
                </div>
            ) : (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" role="radiogroup" aria-label="Bots">
                    {featured.map((t) => {
                        const active = t.id === selected;
                        return (
                            <div
                                key={t.id}
                                role="radio"
                                aria-checked={active}
                                aria-label={t.name}
                                tabIndex={0}
                                onClick={() => onPick(t)}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                        e.preventDefault();
                                        onPick(t);
                                    }
                                }}
                                className={cn(
                                    "flex cursor-pointer flex-col rounded-2xl border p-5 text-left shadow-[var(--shadow-card)] transition-colors",
                                    active
                                        ? "border-[var(--accent-brand)] bg-[var(--accent-brand-soft)] ring-1 ring-[var(--accent-brand)]"
                                        : "border-border bg-card hover:bg-muted/40",
                                )}
                            >
                                <span className="flex items-center gap-3">
                                    <span
                                        aria-hidden="true"
                                        className={cn(
                                            "flex h-12 w-12 shrink-0 items-center justify-center rounded-full text-base font-semibold",
                                            tileColour(t.name),
                                        )}
                                    >
                                        {initials(t.name)}
                                    </span>
                                    <span className="min-w-0">
                                        <span className="block font-semibold leading-tight">{t.name}</span>
                                        <span className="block text-xs text-muted-foreground">
                                            {industryOf(t)} · {whatItDoes(t)}
                                        </span>
                                    </span>
                                </span>
                                <span className="mt-3 block text-sm text-muted-foreground">{t.summary}</span>
                                {t.languages.length > 0 && (
                                    <span className="mt-2 block text-xs text-muted-foreground">
                                        Speaks {t.languages.map((l) => LANGUAGE_NAMES[l] ?? l).join(", ")}
                                    </span>
                                )}
                                <VoiceChips
                                    voices={t.suggested_voices ?? []}
                                    selected={selected === t.id ? voiceId : null}
                                    onSelect={(v) => {
                                        if (selected !== t.id) onPick(t);
                                        onSuggestedVoice(v);
                                    }}
                                />
                                <span className="mt-4 flex items-center justify-end">
                                    <Button
                                        type="button"
                                        size="sm"
                                        variant={active ? "default" : "outline"}
                                        aria-label={`Add ${t.name} to team`}
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            hire(t);
                                        }}
                                    >
                                        Add to team
                                        <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                                    </Button>
                                </span>
                            </div>
                        );
                    })}
                    {/* The sixth card is the one that is not on the shelf: say
                        what you need and Decibyl builds it. */}
                    <button
                        type="button"
                        onClick={onDescribe}
                        className="flex flex-col items-start justify-between rounded-2xl border border-dashed border-border p-5 text-left transition-colors hover:bg-muted/40"
                    >
                        <span>
                            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
                                <Sparkles className="h-5 w-5" aria-hidden="true" />
                            </span>
                            <span className="mt-3 block font-semibold">Describe your bot</span>
                            <span className="mt-1 block text-sm text-muted-foreground">
                                Not on the shelf? Tell Decibyl what it should do, in your words, and it builds one.
                            </span>
                        </span>
                        <span className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-[var(--accent-brand)]">
                            Chat to Decibyl
                            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                        </span>
                    </button>
                </div>
            )}

            <div className="flex flex-wrap items-center gap-2">
                <span id="first-agent-voice" className="text-sm text-muted-foreground">
                    Voice
                </span>
                <div className="flex gap-1.5" role="group" aria-labelledby="first-agent-voice">
                    {(["female", "male"] as VoiceGender[]).map((option) => {
                        const active = voice === option;
                        return (
                            <button
                                key={option}
                                type="button"
                                aria-pressed={active}
                                onClick={() => onVoice(active ? null : option)}
                                className={cn(
                                    "rounded-full border px-3 py-1 text-sm capitalize transition-colors",
                                    active
                                        ? "border-[var(--accent-brand)] bg-[var(--accent-brand-soft)] font-medium"
                                        : "border-border text-muted-foreground hover:bg-muted/40",
                                )}
                            >
                                {option}
                            </button>
                        );
                    })}
                </div>
                <span className="text-xs text-muted-foreground">
                    {voiceId
                        ? "Your bot will answer in the voice you just heard."
                        : voice
                          ? "Change it on the agent whenever you like."
                          : "Press a voice on a card to hear it and choose it."}
                </span>
            </div>

            <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5">
                <p className="text-sm text-muted-foreground">
                    {chosen ? (
                        <>
                            Selected: <span className="font-medium text-foreground">{chosen.name}</span>
                        </>
                    ) : (
                        <>
                            Pick one to continue, or{" "}
                            <Link href="/marketplace" className="underline underline-offset-4 hover:text-foreground">
                                see every bot
                            </Link>
                            .
                        </>
                    )}
                </p>
                <div className="flex items-center gap-3">
                    <Button onClick={onContinue} disabled={!chosen}>
                        Continue
                        <ArrowRight className="h-4 w-4" />
                    </Button>
                </div>
            </footer>
        </div>
    );
}

// ---------------------------------------------------------------------------
// Step 2 — name it
// ---------------------------------------------------------------------------

function NameStep({
    template,
    agentName,
    answers,
    greeting,
    busy,
    error,
    onName,
    onAnswer,
    onGreeting,
    onBack,
    onContinue,
}: {
    template: Template;
    agentName: string;
    answers: Record<string, string>;
    greeting: string;
    busy: boolean;
    error: string | null;
    onName: (value: string) => void;
    onAnswer: (key: string, value: string) => void;
    onGreeting: (value: string) => void;
    onBack: () => void;
    onContinue: () => void;
}) {
    const variables = template.variables ?? [];

    return (
        <form
            className="space-y-6"
            onSubmit={(event) => {
                event.preventDefault();
                if (!busy) onContinue();
            }}
        >
            <header>
                <h2 className="text-2xl font-semibold tracking-tight">Name it</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    {template.name}, adjusted for you. Everything here can be changed later.
                </p>
            </header>

            <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                    <Label htmlFor="first-agent-name">Agent name</Label>
                    <Input
                        id="first-agent-name"
                        value={agentName}
                        onChange={(e) => onName(e.target.value)}
                        placeholder={template.name}
                        maxLength={120}
                    />
                    <p className="text-xs text-muted-foreground">How it appears in your list.</p>
                </div>
                {variables.map((variable) => (
                    <div key={variable.name} className="space-y-2">
                        <Label htmlFor={`first-agent-${variable.name}`}>{variable.asks_for}</Label>
                        <Input
                            id={`first-agent-${variable.name}`}
                            value={answers[variable.name] ?? ""}
                            onChange={(e) => onAnswer(variable.name, e.target.value)}
                        />
                    </div>
                ))}
            </div>

            {template.languages.length > 0 && (
                <div>
                    <p className="text-sm font-medium">Languages it follows</p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                        {template.languages.map((language) => (
                            <span
                                key={language}
                                className="rounded-full border border-border px-2.5 py-0.5 text-xs"
                            >
                                {language}
                            </span>
                        ))}
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                        It answers in whichever of these the caller speaks, and switches mid-call.
                    </p>
                </div>
            )}

            <div className="space-y-2">
                <Label htmlFor="first-agent-greeting">Opening line</Label>
                <Textarea
                    id="first-agent-greeting"
                    value={greeting}
                    onChange={(e) => onGreeting(e.target.value)}
                    rows={3}
                    maxLength={600}
                />
                <p className="text-xs text-muted-foreground">
                    Voice, model and transcription use Decibyl&apos;s managed defaults. Change them
                    inside the agent whenever you like.
                </p>
            </div>

            {error && (
                <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                    {error}
                </p>
            )}

            <footer className="flex items-center justify-between border-t border-border pt-5">
                <Button type="button" variant="ghost" onClick={onBack} disabled={busy}>
                    <ArrowLeft className="h-4 w-4" />
                    Back
                </Button>
                <Button type="submit" disabled={busy}>
                    {busy ? (
                        <>
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Creating…
                        </>
                    ) : (
                        <>
                            Continue
                            <ArrowRight className="h-4 w-4" />
                        </>
                    )}
                </Button>
            </footer>
        </form>
    );
}

// ---------------------------------------------------------------------------
// Step 3 — hear it
// ---------------------------------------------------------------------------

function HearStep({
    workflowId,
    voice,
    displayName,
    getAccessToken,
    onRun,
    onBack,
    onDone,
}: {
    workflowId: number;
    /** A voice bot is heard on a call; any other kind is tested by typing. */
    voice: boolean;
    displayName: string;
    getAccessToken: () => Promise<string>;
    onRun: (runId: number) => void;
    // "Now hear it" was the one step with no way back: a wrong name or the
    // wrong template meant starting the whole journey over.
    onBack: () => void;
    onDone: () => void;
}) {
    const { user, loading: authLoading } = useAuth();
    const userId = user?.id;
    const [mode, setMode] = useState<TestMode>("browser");
    const [numbers, setNumbers] = useState<VerifiedNumber[] | null>(null);
    const [phone, setPhone] = useState<string | null>(null);
    const [accessToken, setAccessToken] = useState<string | null>(null);
    const [browserRunId, setBrowserRunId] = useState<number | null>(null);
    const [ringing, setRinging] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [startedAt, setStartedAt] = useState<number | null>(null);

    useEffect(() => {
        if (authLoading || !userId) return;
        let cancelled = false;
        void (async () => {
            const response = await listNumbersApiV1VerifiedNumbersGet();
            if (cancelled) return;
            const verified = (response.data ?? []).filter((n) => n.status === "verified");
            setNumbers(verified);
            setPhone(verified[0]?.phone_number ?? null);
        })();
        return () => {
            cancelled = true;
        };
    }, [authLoading, userId]);

    const startBrowser = async () => {
        setBusy(true);
        setError(null);
        try {
            const token = await getAccessToken();
            const response = await createWorkflowRunApiV1WorkflowWorkflowIdRunsPost({
                path: { workflow_id: workflowId },
                body: { mode: WORKFLOW_RUN_MODES.SMALL_WEBRTC, name: `WR-${getRandomId()}` },
            });
            if (response.error || !response.data?.id) {
                throw new Error(detailFromResult(response, "Could not start the test call."));
            }
            setAccessToken(token);
            setBrowserRunId(response.data.id);
            setStartedAt(Date.now());
            onRun(response.data.id);
            posthog.capture(PostHogEvent.WEB_CALL_INITIATED, {
                workflow_id: workflowId,
                workflow_run_id: response.data.id,
                source: "first_agent",
            });
            posthog.capture(PostHogEvent.FIRST_AGENT_TEST_STARTED, {
                workflow_id: workflowId,
                workflow_run_id: response.data.id,
                mode: "browser",
            });
        } catch (err) {
            const message = err instanceof Error ? err.message : "Could not start the test call.";
            posthog.capture(PostHogEvent.FIRST_AGENT_TEST_FAILED, {
                workflow_id: workflowId,
                mode: "browser",
                reason: message,
            });
            setError(message);
        } finally {
            setBusy(false);
        }
    };

    const callPhone = async () => {
        if (!phone) return;
        setBusy(true);
        setError(null);
        const response = await initiateCallApiV1TelephonyInitiateCallPost({
            body: { workflow_id: workflowId, phone_number: phone },
        });
        setBusy(false);
        if (response.error) {
            const message = detailFromResult(response, "Could not place the call.");
            posthog.capture(PostHogEvent.FIRST_AGENT_TEST_FAILED, {
                workflow_id: workflowId,
                mode: "phone",
                reason: message,
            });
            setError(message);
            return;
        }
        const run = (response.data as { workflow_run_id?: number } | undefined)?.workflow_run_id;
        if (run) onRun(run);
        setRinging(true);
        setStartedAt(Date.now());
        posthog.capture(PostHogEvent.FIRST_AGENT_TEST_STARTED, {
            workflow_id: workflowId,
            workflow_run_id: run ?? null,
            mode: "phone",
        });
    };

    const complete = useCallback(
        (how: TestMode) => {
            posthog.capture(PostHogEvent.FIRST_AGENT_TEST_COMPLETED, {
                workflow_id: workflowId,
                mode: how,
                seconds: startedAt ? Math.round((Date.now() - startedAt) / 1000) : null,
            });
            onDone();
        },
        [workflowId, startedAt, onDone],
    );

    const onBrowserCompleted = useCallback(() => {
        announceBalanceChanged();
        complete("browser");
    }, [complete]);

    if (browserRunId !== null && accessToken) {
        return (
            <div className="flex min-h-[520px] flex-col gap-4">
                <header>
                    <h2 className="text-2xl font-semibold tracking-tight">{displayName} is on the line.</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                        Speak naturally. End the call when you have heard enough.
                    </p>
                </header>
                <EmbeddedVoiceTester
                    workflowId={workflowId}
                    workflowRunId={browserRunId}
                    accessToken={accessToken}
                    onReset={() => setBrowserRunId(null)}
                    onCompleted={onBrowserCompleted}
                />
            </div>
        );
    }

    if (!voice) {
        return (
            <div className="space-y-6">
                <header>
                    <h2 className="text-2xl font-semibold tracking-tight">Now test it.</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                        {displayName} is ready. Write to it the way a customer would; the test is free.
                    </p>
                </header>
                <div className="rounded-xl border border-border p-4">
                    <ManualTextChatPanel workflowId={workflowId} ready disabled={false} disabledReason={null} />
                </div>
                <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5">
                    <Button variant="ghost" onClick={onBack}>
                        <ArrowLeft className="h-4 w-4" />
                        Back
                    </Button>
                    <Button onClick={onDone}>
                        Done
                        <ArrowRight className="h-4 w-4" />
                    </Button>
                </footer>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <header>
                <h2 className="text-2xl font-semibold tracking-tight">Now hear it.</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    {displayName} is ready. This test call is free.
                </p>
            </header>

            <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="How to test">
                <ModeCard
                    active={mode === "browser"}
                    onClick={() => setMode("browser")}
                    icon={<Mic className="h-5 w-5" />}
                    title="Talk in the browser"
                    body="Uses your microphone. No phone needed."
                />
                <ModeCard
                    active={mode === "phone"}
                    onClick={() => setMode("phone")}
                    icon={<Phone className="h-5 w-5" />}
                    title="Call my phone"
                    body={
                        numbers === null
                            ? "Checking your verified numbers…"
                            : numbers.length === 0
                              ? "Verify your number first — takes a minute."
                              : `We ring ${numbers.length === 1 ? "your verified number" : "a number you have verified"}.`
                    }
                />
            </div>

            {mode === "phone" && numbers !== null && (
                <div className="rounded-xl border border-border p-4">
                    {numbers.length === 0 ? (
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <p className="text-sm text-muted-foreground">
                                Test calls go only to numbers you have verified.
                            </p>
                            <Button asChild variant="outline">
                                <Link
                                    href="/verified-numbers?next=/start"
                                    onClick={() =>
                                        posthog.capture(PostHogEvent.FIRST_AGENT_VERIFY_NUMBER_CLICKED, {
                                            workflow_id: workflowId,
                                        })
                                    }
                                >
                                    Verify my number
                                </Link>
                            </Button>
                        </div>
                    ) : ringing ? (
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="flex items-center gap-3">
                                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-[var(--accent-brand-soft)] text-[var(--accent-brand)]">
                                    <PhoneCall className="h-4 w-4 animate-pulse" />
                                </span>
                                <div>
                                    <p className="text-sm font-medium">Ringing {phone}</p>
                                    <p className="text-xs text-muted-foreground">Pick up and talk to {displayName}.</p>
                                </div>
                            </div>
                            <Button onClick={() => complete("phone")}>
                                I took the call
                                <ArrowRight className="h-4 w-4" />
                            </Button>
                        </div>
                    ) : (
                        <div className="flex flex-wrap items-end gap-3">
                            <div className="min-w-[220px] flex-1 space-y-2">
                                <Label htmlFor="first-agent-phone">Which number</Label>
                                <select
                                    id="first-agent-phone"
                                    value={phone ?? ""}
                                    onChange={(e) => setPhone(e.target.value)}
                                    className="h-9 w-full rounded-[var(--radius-control)] border border-input bg-card px-3 text-sm"
                                >
                                    {numbers.map((n) => (
                                        <option key={n.phone_number} value={n.phone_number}>
                                            {n.phone_number}
                                            {n.label ? ` · ${n.label}` : ""}
                                        </option>
                                    ))}
                                </select>
                            </div>
                            <Button onClick={() => void callPhone()} disabled={busy || !phone}>
                                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Phone className="h-4 w-4" />}
                                Call me
                            </Button>
                        </div>
                    )}
                </div>
            )}

            {error && (
                <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                    {error}
                </p>
            )}

            {mode === "phone" && (
                <footer className="flex items-center justify-start border-t border-border pt-5">
                    <Button type="button" variant="ghost" onClick={onBack} disabled={busy}>
                        <ArrowLeft className="h-4 w-4" />
                        Back
                    </Button>
                </footer>
            )}
            {mode === "browser" && (
                <footer className="flex items-center justify-between border-t border-border pt-5">
                    <Button type="button" variant="ghost" onClick={onBack} disabled={busy}>
                        <ArrowLeft className="h-4 w-4" />
                        Back
                    </Button>
                    <Button onClick={() => void startBrowser()} disabled={busy}>
                        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Headphones className="h-4 w-4" />}
                        Start test call
                    </Button>
                </footer>
            )}
        </div>
    );
}

function ModeCard({
    active,
    onClick,
    icon,
    title,
    body,
}: {
    active: boolean;
    onClick: () => void;
    icon: React.ReactNode;
    title: string;
    body: string;
}) {
    return (
        <button
            type="button"
            role="radio"
            aria-checked={active}
            onClick={onClick}
            className={cn(
                "flex gap-3 rounded-xl border p-4 text-left transition-colors",
                active
                    ? "border-[var(--accent-brand)] bg-[var(--accent-brand-soft)] ring-1 ring-[var(--accent-brand)]"
                    : "border-border hover:bg-muted/40",
            )}
        >
            <span className="mt-0.5 text-[var(--accent-brand)]">{icon}</span>
            <span>
                <span className="block font-medium">{title}</span>
                <span className="mt-0.5 block text-sm text-muted-foreground">{body}</span>
            </span>
        </button>
    );
}

// ---------------------------------------------------------------------------
// Step 4 — done
// ---------------------------------------------------------------------------

function ReadyStep({
    workflowId,
    voice,
    runId,
    displayName,
    onFinish,
}: {
    workflowId: number;
    voice: boolean;
    runId: number | null;
    displayName: string;
    onFinish: (next: "number" | "agent" | "recording") => void;
}) {
    return (
        <div className="space-y-6">
            <header>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-[var(--accent-brand-soft)] px-2.5 py-1 text-xs font-medium text-[var(--accent-brand)]">
                    <Sparkles className="h-3.5 w-3.5" />
                    {voice ? "First call done" : "First test done"}
                </span>
                <h2 className="mt-3 text-2xl font-semibold tracking-tight">
                    {voice ? `${displayName} took its first call.` : `${displayName} is on the team.`}
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    Paid from your credits — the first ones came free with the account.
                    {runId !== null && " The recording and transcript are saved with the call."}
                </p>
            </header>
            {runId !== null && <PostCallSummary workflowId={workflowId} runId={runId} />}

            <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-xl border border-border p-4">
                    <p className="font-medium">Customers can reach {displayName} once it has a number.</p>
                    <p className="mt-1 text-sm text-muted-foreground">
                        Get a number in a minute, or connect one you already own.
                    </p>
                    <Button asChild className="mt-4">
                        <Link href="/numbers" onClick={() => onFinish("number")}>
                            Get a phone number
                            <ArrowRight className="h-4 w-4" />
                        </Link>
                    </Button>
                </div>
                <div className="rounded-xl border border-border p-4">
                    <p className="font-medium">Change what it says, or how it sounds.</p>
                    <p className="mt-1 text-sm text-muted-foreground">
                        Prompts, voice, model and tools are all on the agent.
                    </p>
                    <div className="mt-4 flex flex-wrap gap-2">
                        <Button asChild variant="outline">
                            <Link href={`/workflow/${workflowId}`} onClick={() => onFinish("agent")}>
                                Open {displayName}
                            </Link>
                        </Button>
                        {runId !== null && (
                            <Button asChild variant="ghost">
                                <Link
                                    href={`/workflow/${workflowId}/run/${runId}`}
                                    onClick={() => onFinish("recording")}
                                >
                                    Recording &amp; transcript
                                </Link>
                            </Button>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}

export default FirstAgentJourney;
