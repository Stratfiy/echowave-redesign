"use client";

/**
 * Creating an agent, as three questions rather than one blank box.
 *
 * This screen used to ask for a call type, a use-case label, and a free-text
 * description. That is a reasonable prompt and a poor form: it asks somebody
 * who has never built a voice agent to know, unaided, that the description
 * should mention the persona, the opening line, what to do when the caller is
 * busy, and what the agent must never say. When they do not — and they do not —
 * the generated agent is missing exactly those things, and they find out on a
 * call.
 *
 * So the same information is asked for in the order somebody actually knows
 * it: who the agent is, how the conversation goes, how it ends. Nothing here
 * is required except the objective, because a better form is not a longer one;
 * each additional answer should make the result measurably better rather than
 * being the price of getting anything at all.
 *
 * Two fields are treated differently from the rest, and the difference is the
 * point. The welcome message and the closing line are written into the
 * generated graph verbatim, and the guardrails are hoisted into a global node,
 * by `services/workflow/agent_brief.py`. Everything else is guidance a model
 * turns into prompts. A guardrail asked for in a prompt is one you cannot tell
 * was honoured by looking at the result.
 */

import { ArrowLeft, ArrowRight, Check, Loader2, Play, Sparkles, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import { createWorkflowFromTemplateApiV1WorkflowCreateTemplatePost } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

/**
 * Shown so the operator can read and edit what will apply, and sent only when
 * they have edited it.
 *
 * A mirror of `DEFAULT_GUARDRAILS` in `services/workflow/agent_brief.py`, which
 * stays the single source of behavioural truth: leaving these untouched sends
 * an empty list and the server applies its own. If the two ever drift, the
 * server's win, and the worst case is that this screen displayed a stale
 * sentence rather than that an agent ran without a rule.
 */
const DEFAULT_GUARDRAILS = [
    "Follow the caller's language. If they answer in Hindi, Telugu, Tamil or any other language, switch to it immediately and stay there. Never insist on English.",
    "Ask one question per turn. Never stack two questions in a single turn.",
    "Keep every turn under about three sentences. Callers interrupt long turns and everything after the interruption is lost.",
    "Read back phone numbers digit by digit, and read back dates, times and amounts, before treating them as confirmed.",
    "Never invent a fact, a price, a date or a commitment. If you do not have the information, say so plainly and offer to have a person follow up.",
    "Never claim to be a human being if you are asked directly.",
    "If the caller asks to speak to a person, stop the flow and hand off immediately. Do not attempt to resolve their issue first.",
    "If the caller says they are busy, ask once for a better time, then end the call politely.",
    "If the caller asks not to be contacted again, confirm that you have noted it, apologise once, and end the call. Do not attempt to continue.",
];

const TONES = ["Warm & professional", "Formal", "Casual", "Assertive", "Empathetic"];

/** India first, because that is where the calls are. */
const LANGUAGES = [
    "Hindi",
    "English",
    "Telugu",
    "Tamil",
    "Kannada",
    "Malayalam",
    "Marathi",
    "Bengali",
    "Gujarati",
    "Punjabi",
];

const INDUSTRIES = [
    "Healthcare & clinics",
    "Real estate",
    "Lending & collections",
    "Education & admissions",
    "E-commerce & retail",
    "Hospitality & restaurants",
    "Recruitment",
    "Insurance",
    "Logistics",
    "Other",
];

const STEPS = ["Identity", "Conversation", "Closing"] as const;

/** The bundle balance, so the wizard can say what it buys. */
const BUNDLE_BALANCE_PAISE = 250000;

type Brain = {
    tier: string;
    label: string;
    blurb: string;
    paise_per_minute: number;
};

type VoiceOption = {
    voice_id: string;
    name: string;
    gender: string | null;
    description: string | null;
    is_default: boolean;
    sample_url: string | null;
    sample_url_hi: string | null;
};

type AgentOptions = { brains: Brain[]; voices: VoiceOption[] };

/** "₹5.20" from 520 paise. */
function rupees(paise: number): string {
    return `₹${(paise / 100).toFixed(2)}`;
}

function Chip({
    label,
    selected,
    onClick,
    onRemove,
}: {
    label: string;
    selected?: boolean;
    onClick?: () => void;
    onRemove?: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors",
                selected
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-muted/30 text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
        >
            {selected && !onRemove && <Check className="h-3.5 w-3.5" />}
            {label}
            {onRemove && (
                <X
                    className="h-3.5 w-3.5 opacity-70 hover:opacity-100"
                    onClick={(event) => {
                        event.stopPropagation();
                        onRemove();
                    }}
                />
            )}
        </button>
    );
}

function Field({
    label,
    hint,
    required,
    children,
}: {
    label: string;
    hint?: string;
    required?: boolean;
    children: React.ReactNode;
}) {
    return (
        <div className="space-y-1.5">
            <Label className="text-sm font-medium">
                {label}
                {required && <span className="ml-0.5 text-destructive">*</span>}
            </Label>
            {children}
            {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
        </div>
    );
}

export default function CreateWorkflowPage() {
    const router = useRouter();
    const { user } = useAuth();

    const [step, setStep] = useState(0);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [workflowId, setWorkflowId] = useState<string | null>(null);

    // Step 1 — identity
    const [callType, setCallType] = useState<"inbound" | "outbound">("inbound");
    const [agentName, setAgentName] = useState("");
    const [industry, setIndustry] = useState("");
    const [designation, setDesignation] = useState("");
    const [companyName, setCompanyName] = useState("");
    const [companyDescription, setCompanyDescription] = useState("");
    const [languages, setLanguages] = useState<string[]>(["Hindi", "English"]);
    const [gender, setGender] = useState("Female");
    const [tone, setTone] = useState(TONES[0]);
    const [objective, setObjective] = useState("");

    // What it sounds like and how clever it is, in that order — the voice is
    // the thing a caller notices and the buyer has an opinion about.
    const [options, setOptions] = useState<AgentOptions | null>(null);
    const [voice, setVoice] = useState<string>("");
    const [brain, setBrain] = useState<string>("default");
    // One player, reused. Seven audio elements would let two voices talk over
    // each other, which is a comic way to fail an audition.
    const playerRef = useRef<HTMLAudioElement | null>(null);
    const [playing, setPlaying] = useState<string | null>(null);

    const playSample = (option: VoiceOption) => {
        const url = option.sample_url ?? option.sample_url_hi;
        if (!url) return;
        const player = playerRef.current ?? new Audio();
        playerRef.current = player;
        player.pause();
        player.src = url;
        setPlaying(option.voice_id);
        player.onended = () => setPlaying(null);
        player.onerror = () => setPlaying(null);
        void player.play().catch(() => setPlaying(null));
    };

    // Step 2 — conversation
    const [welcome, setWelcome] = useState("");
    const [guardrails, setGuardrails] = useState(DEFAULT_GUARDRAILS.join("\n"));
    const [guardrailsEdited, setGuardrailsEdited] = useState(false);
    const [flow, setFlow] = useState("");

    // Step 3 — closing
    const [closingLine, setClosingLine] = useState("");
    const [useHangupPrompt, setUseHangupPrompt] = useState(false);
    const [hangupPrompt, setHangupPrompt] = useState("");

    useEffect(() => {
        if (!user) return;
        let cancelled = false;
        void (async () => {
            // Not in the generated SDK yet; the route is newer than the last
            // client generation.
            const response = await client.get({ url: "/api/v1/agent-options" });
            if (cancelled || response.error) return;
            const data = response.data as AgentOptions | undefined;
            if (!data) return;
            setOptions(data);
            setVoice((current) => current || (data.voices[0]?.voice_id ?? ""));
        })();
        return () => {
            cancelled = true;
        };
    }, [user]);

    const chosenBrain = options?.brains.find((b) => b.tier === brain) ?? null;
    // Shown with a "roughly" in front of it. It moves with the rate card and
    // with how much the agent actually says, so it is an estimate in the
    // honest sense rather than an entitlement.
    const approxMinutes =
        chosenBrain && chosenBrain.paise_per_minute > 0
            ? Math.floor(BUNDLE_BALANCE_PAISE / chosenBrain.paise_per_minute)
            : null;

    const guardrailCount = useMemo(
        () => guardrails.split("\n").filter((line) => line.trim()).length,
        [guardrails],
    );

    // Only the objective gates progress. Everything else improves the result
    // without being required to get one.
    const canContinue = step === 0 ? objective.trim().length > 0 : true;

    const create = async () => {
        if (!user) {
            setError("You must be signed in to create an agent.");
            return;
        }
        setIsLoading(true);
        setError(null);

        // The generated SDK still describes the three-field body this screen
        // replaced, so the wizard's fields are not on its type yet. They are on
        // the endpoint — see `CreateWorkflowTemplateRequest` — and the fetch
        // client sends the body as given, so this asserts the shape the server
        // accepts rather than the one the stale type describes. Regenerating
        // the client removes the need for it.
        const body: Record<string, unknown> = {
            call_type: callType,
            use_case: objective.trim().slice(0, 80),
            objective: objective.trim(),
            agent_name: agentName.trim(),
            agent_designation: designation.trim(),
            company_name: companyName.trim(),
            company_description: companyDescription.trim(),
            industry,
            languages,
            gender,
            tone,
            voice,
            llm_tier: brain,
            welcome_message: welcome.trim(),
            conversation_flow: flow.trim(),
            // Empty means "apply the server's own", so an untouched list is
            // never a second copy of the rules that can go stale.
            guardrails: guardrailsEdited
                ? guardrails
                      .split("\n")
                      .map((line) => line.trim())
                      .filter(Boolean)
                : [],
            closing_line: closingLine.trim(),
            hangup_prompt: useHangupPrompt ? hangupPrompt.trim() : "",
        };

        const response = await createWorkflowFromTemplateApiV1WorkflowCreateTemplatePost({
            body: body as Parameters<
                typeof createWorkflowFromTemplateApiV1WorkflowCreateTemplatePost
            >[0]["body"],
        });

        if (response.error) {
            setError(detailFromError(response.error, "Could not create the agent."));
            setIsLoading(false);
            return;
        }
        const id = (response.data as { id?: number } | undefined)?.id;
        if (id) setWorkflowId(String(id));
        setIsLoading(false);
    };

    return (
        <div className="min-h-screen">
            <div className="container mx-auto max-w-3xl px-4 py-8">
                <div className="mb-6 flex items-center gap-2 text-sm font-medium text-primary">
                    <span className="h-2 w-2 rounded-full bg-primary" />
                    NEW AGENT
                </div>

                {/* Stepper */}
                <div className="mb-4 flex flex-wrap items-center gap-3">
                    {STEPS.map((label, index) => (
                        <div key={label} className="flex items-center gap-3">
                            <div className="flex items-center gap-2">
                                <span
                                    className={cn(
                                        "flex h-7 w-7 items-center justify-center rounded-full border text-xs font-semibold",
                                        index < step
                                            ? "border-primary bg-primary text-primary-foreground"
                                            : index === step
                                              ? "border-primary text-primary"
                                              : "border-border text-muted-foreground",
                                    )}
                                >
                                    {index < step ? (
                                        <Check className="h-4 w-4" />
                                    ) : (
                                        index + 1
                                    )}
                                </span>
                                <span
                                    className={cn(
                                        "text-sm",
                                        index === step
                                            ? "font-medium text-foreground"
                                            : "text-muted-foreground",
                                    )}
                                >
                                    {label}
                                </span>
                            </div>
                            {index < STEPS.length - 1 && (
                                <span className="hidden h-px w-10 bg-border sm:block" />
                            )}
                        </div>
                    ))}
                </div>

                <p className="mb-1.5 text-xs text-muted-foreground">Overall progress</p>
                <div className="mb-8 flex gap-1.5">
                    {STEPS.map((label, index) => (
                        <div
                            key={label}
                            className={cn(
                                "h-1.5 flex-1 rounded-full transition-colors",
                                index <= step ? "bg-primary" : "bg-muted",
                            )}
                        />
                    ))}
                </div>

                {step === 0 && (
                    <section className="space-y-6">
                        <header>
                            <h1 className="text-2xl font-semibold tracking-tight">
                                Who is your agent?
                            </h1>
                            <p className="mt-1 text-sm text-muted-foreground">
                                Its name, its voice, and who it speaks for.
                            </p>
                        </header>

                        <Field label="Call type" required>
                            <Select
                                value={callType}
                                onValueChange={(value) =>
                                    setCallType(value as "inbound" | "outbound")
                                }
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="inbound">
                                        Inbound — people call your agent
                                    </SelectItem>
                                    <SelectItem value="outbound">
                                        Outbound — your agent calls people
                                    </SelectItem>
                                </SelectContent>
                            </Select>
                        </Field>

                        <div className="grid gap-4 sm:grid-cols-2">
                            <Field
                                label="Agent name"
                                hint="What it calls itself on the call."
                            >
                                <Input
                                    value={agentName}
                                    onChange={(e) => setAgentName(e.target.value)}
                                    placeholder="Meera"
                                />
                            </Field>
                            <Field label="Industry">
                                <Select value={industry} onValueChange={setIndustry}>
                                    <SelectTrigger>
                                        <SelectValue placeholder="Select an industry" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {INDUSTRIES.map((item) => (
                                            <SelectItem key={item} value={item}>
                                                {item}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </Field>
                            <Field label="Agent designation">
                                <Input
                                    value={designation}
                                    onChange={(e) => setDesignation(e.target.value)}
                                    placeholder="Admissions counsellor"
                                />
                            </Field>
                            <Field label="Company name">
                                <Input
                                    value={companyName}
                                    onChange={(e) => setCompanyName(e.target.value)}
                                    placeholder="Sunrise Academy"
                                />
                            </Field>
                        </div>

                        <Field
                            label="Company description"
                            hint="One line. It is what the agent says when a caller asks who you are."
                        >
                            <Textarea
                                value={companyDescription}
                                onChange={(e) => setCompanyDescription(e.target.value)}
                                placeholder="runs three CBSE schools across Hyderabad."
                                rows={2}
                            />
                        </Field>

                        <Field
                            label="Languages"
                            required
                            hint="The first is the primary. The agent follows whichever the caller uses."
                        >
                            <div className="flex flex-wrap gap-2">
                                {LANGUAGES.map((language) => {
                                    const index = languages.indexOf(language);
                                    return (
                                        <Chip
                                            key={language}
                                            label={
                                                index === 0
                                                    ? `${language} (primary)`
                                                    : language
                                            }
                                            selected={index >= 0}
                                            onClick={() =>
                                                setLanguages((previous) =>
                                                    previous.includes(language)
                                                        ? previous.filter(
                                                              (l) => l !== language,
                                                          )
                                                        : [...previous, language],
                                                )
                                            }
                                        />
                                    );
                                })}
                            </div>
                        </Field>

                        <Field
                            label="Voice"
                            hint="What the caller hears. Changeable later without rebuilding the agent."
                        >
                            {!options ? (
                                <span className="text-sm text-muted-foreground">
                                    Loading voices…
                                </span>
                            ) : (
                                <div className="space-y-3">
                                    {/* Grouped, because seven Indian first names in one
                                        row is a list to read rather than a choice to
                                        make. Gender is the only thing we can say about
                                        these voices without having heard them — it is
                                        the vendor's own metadata, not a guess from the
                                        name — so it is the only thing we sort by. */}
                                    {(
                                        [
                                            ["female", "Female"],
                                            ["male", "Male"],
                                        ] as const
                                    ).map(([key, heading]) => {
                                        const group = options.voices.filter(
                                            (v) => v.gender === key,
                                        );
                                        if (!group.length) return null;
                                        return (
                                            <div key={key}>
                                                <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                                                    {heading}
                                                </p>
                                                <div className="flex flex-wrap gap-2">
                                                    {group.map((option) => (
                                                        <span
                                                            key={option.voice_id}
                                                            className="inline-flex items-center"
                                                        >
                                                            <Chip
                                                                label={
                                                                    option.description
                                                                        ? `${option.name} — ${option.description}`
                                                                        : option.is_default
                                                                          ? `${option.name} · default`
                                                                          : option.name
                                                                }
                                                                selected={
                                                                    voice ===
                                                                    option.voice_id
                                                                }
                                                                onClick={() => {
                                                                    setVoice(
                                                                        option.voice_id,
                                                                    );
                                                                    setGender(heading);
                                                                }}
                                                            />
                                                            {/* Only where there is
                                                                something to play — a
                                                                button that fails on
                                                                click is worse than no
                                                                button. */}
                                                            {(option.sample_url ||
                                                                option.sample_url_hi) && (
                                                                <button
                                                                    type="button"
                                                                    aria-label={`Hear ${option.name}`}
                                                                    onClick={() =>
                                                                        playSample(option)
                                                                    }
                                                                    className="-ml-1 rounded-full p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                                                                >
                                                                    {playing ===
                                                                    option.voice_id ? (
                                                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                                    ) : (
                                                                        <Play className="h-3.5 w-3.5" />
                                                                    )}
                                                                </button>
                                                            )}
                                                        </span>
                                                    ))}
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </Field>

                        <Field
                            label="How clever should it be?"
                            hint="A bigger brain handles a caller who goes off script better, and costs more a minute."
                        >
                            <div className="grid gap-2 sm:grid-cols-3">
                                {(options?.brains ?? []).map((option) => (
                                    <button
                                        key={option.tier}
                                        type="button"
                                        onClick={() => setBrain(option.tier)}
                                        className={cn(
                                            "rounded-lg border p-3 text-left transition-colors",
                                            brain === option.tier
                                                ? "border-primary bg-primary/5"
                                                : "border-border hover:bg-muted/40",
                                        )}
                                    >
                                        <span className="flex items-baseline justify-between gap-2">
                                            <span className="font-medium">
                                                {option.label}
                                            </span>
                                            <span className="text-sm tabular-nums text-muted-foreground">
                                                {rupees(option.paise_per_minute)}/min
                                            </span>
                                        </span>
                                        <span className="mt-1 block text-xs text-muted-foreground">
                                            {option.blurb}
                                        </span>
                                    </button>
                                ))}
                            </div>
                        </Field>

                        {chosenBrain && (
                            <p className="rounded-lg border border-border bg-muted/20 px-4 py-3 text-sm">
                                <strong>
                                    {rupees(chosenBrain.paise_per_minute)} a minute
                                </strong>
                                {approxMinutes !== null && (
                                    <>
                                        {" — a ₹2,500 balance is roughly "}
                                        <strong>{approxMinutes} minutes</strong> of
                                        talking.
                                    </>
                                )}
                                <span className="mt-1 block text-xs text-muted-foreground">
                                    An estimate. A call that says more costs more, and the
                                    figure moves if provider prices do.
                                </span>
                            </p>
                        )}

                        <div className="grid gap-6 sm:grid-cols-2">
                            <Field label="Tone">
                                <div className="flex flex-wrap gap-2">
                                    {TONES.map((option) => (
                                        <Chip
                                            key={option}
                                            label={option}
                                            selected={tone === option}
                                            onClick={() => setTone(option)}
                                        />
                                    ))}
                                </div>
                            </Field>
                        </div>

                        <Field
                            label="What should this call achieve?"
                            required
                            hint="Brief it the way you would brief a new receptionist on their first morning."
                        >
                            <Textarea
                                value={objective}
                                onChange={(e) => setObjective(e.target.value)}
                                placeholder="Find out which course the caller is interested in, whether the student has finished class 10 or 12, and book a campus visit."
                                rows={3}
                            />
                        </Field>
                    </section>
                )}

                {step === 1 && (
                    <section className="space-y-6">
                        <header>
                            <h1 className="text-2xl font-semibold tracking-tight">
                                What&apos;s the conversation?
                            </h1>
                            <p className="mt-1 text-sm text-muted-foreground">
                                The opening line and the flow.
                            </p>
                        </header>

                        <Field
                            label="Welcome message"
                            hint="Spoken word for word, so write it as it should sound. Use {{variable_name}} for anything that changes per call. Leave it empty and the agent opens in its own words."
                        >
                            <Textarea
                                value={welcome}
                                onChange={(e) => setWelcome(e.target.value)}
                                placeholder="नमस्ते, मैं Sunrise Academy से मीरा बोल रही हूँ. क्या अभी बात करने का सही समय है?"
                                rows={3}
                            />
                        </Field>

                        <div className="space-y-1.5">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <Label className="text-sm font-medium">Guardrails</Label>
                                <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2.5 py-1 text-xs text-muted-foreground">
                                    <Check className="h-3 w-3 text-primary" />
                                    {guardrailCount} applied on every turn
                                </span>
                            </div>
                            <Textarea
                                value={guardrails}
                                onChange={(e) => {
                                    setGuardrails(e.target.value);
                                    setGuardrailsEdited(true);
                                }}
                                rows={8}
                                className="text-sm"
                            />
                            <p className="text-xs text-muted-foreground">
                                One rule per line. These are applied to every turn of
                                every call, not written into a prompt and hoped for.
                                Editing this list replaces ours rather than adding to it.
                            </p>
                        </div>

                        <Field
                            label="Conversation flow"
                            hint="Numbered steps. The generated agent gets one node per step, which is what makes a call you can read afterwards."
                        >
                            <Textarea
                                value={flow}
                                onChange={(e) => setFlow(e.target.value)}
                                placeholder={
                                    "1. Confirm you are speaking to the parent or the student.\n2. Ask which course they are interested in.\n3. Ask whether the student has finished class 10 or 12.\n4. Offer two campus visit slots this week.\n5. Read the chosen slot back and confirm."
                                }
                                rows={7}
                            />
                        </Field>
                    </section>
                )}

                {step === 2 && (
                    <section className="space-y-6">
                        <header>
                            <h1 className="text-2xl font-semibold tracking-tight">
                                How does the call end?
                            </h1>
                            <p className="mt-1 text-sm text-muted-foreground">
                                Hangup logic, fallbacks, and goodbyes.
                            </p>
                        </header>

                        <Field
                            label="Closing line"
                            hint="Spoken word for word before the agent hangs up."
                        >
                            <Textarea
                                value={closingLine}
                                onChange={(e) => setClosingLine(e.target.value)}
                                placeholder="आपके समय के लिए धन्यवाद, आपका दिन शुभ हो."
                                rows={2}
                            />
                        </Field>

                        <div className="rounded-lg border border-border bg-muted/20 p-4">
                            <div className="flex items-start justify-between gap-4">
                                <div>
                                    <p className="flex items-center gap-2 text-sm font-medium">
                                        <Sparkles className="h-4 w-4 text-primary" />
                                        Different endings for different outcomes
                                    </p>
                                    <p className="mt-1 text-xs text-muted-foreground">
                                        Say what to do per outcome — interested, busy, not
                                        interested, wrong number — and each becomes its
                                        own ending, so the run record tells you which
                                        happened without anyone reading a transcript.
                                    </p>
                                </div>
                                <Switch
                                    checked={useHangupPrompt}
                                    onCheckedChange={setUseHangupPrompt}
                                />
                            </div>
                            {useHangupPrompt && (
                                <Textarea
                                    value={hangupPrompt}
                                    onChange={(e) => setHangupPrompt(e.target.value)}
                                    rows={6}
                                    className="mt-3 text-sm"
                                    placeholder={
                                        "If they book a visit: read the date and time back, confirm, and end.\nIf they are busy: ask once for a better time, note it, and end.\nIf they are not interested: thank them and end without pitching again.\nIf it is a wrong number: apologise and end immediately."
                                    }
                                />
                            )}
                        </div>
                    </section>
                )}

                {error && (
                    <p
                        role="alert"
                        className="mt-6 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                    >
                        {error}
                    </p>
                )}

                <div className="mt-8 flex items-center justify-between border-t border-border pt-6">
                    <Button
                        variant="ghost"
                        onClick={() => (step === 0 ? router.back() : setStep(step - 1))}
                        disabled={isLoading}
                    >
                        <ArrowLeft className="mr-2 h-4 w-4" />
                        Back
                    </Button>

                    {step < STEPS.length - 1 ? (
                        <Button onClick={() => setStep(step + 1)} disabled={!canContinue}>
                            Continue
                            <ArrowRight className="ml-2 h-4 w-4" />
                        </Button>
                    ) : (
                        <Button onClick={() => void create()} disabled={isLoading}>
                            {isLoading ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <Sparkles className="mr-2 h-4 w-4" />
                            )}
                            Generate agent
                        </Button>
                    )}
                </div>
            </div>

            {isLoading && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
                    <div className="flex flex-col items-center gap-4">
                        <Loader2 className="h-10 w-10 animate-spin text-primary" />
                        <div className="text-center">
                            <p className="font-medium">Building your agent</p>
                            <p className="mt-1 text-sm text-muted-foreground">
                                Turning the brief into a conversation flow. A few seconds.
                            </p>
                        </div>
                    </div>
                </div>
            )}

            <Dialog open={workflowId !== null}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Check className="h-5 w-5 text-primary" />
                            Your agent is ready
                        </DialogTitle>
                        <DialogDescription asChild>
                            <div className="mt-3 space-y-2 text-sm">
                                <p>
                                    A conversation flow has been built from your brief.
                                    Your guardrails are on the Rules node and apply to
                                    every turn.
                                </p>
                                <p>
                                    Talk to it before you change anything — the first call
                                    tells you more than reading the prompts will.
                                </p>
                            </div>
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter className="mt-4">
                        <Button
                            className="w-full"
                            onClick={() =>
                                router.push(`/workflow/${workflowId}?onboarding=web_call`)
                            }
                        >
                            Open and test the agent
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
