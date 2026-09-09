"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { detailFromResult } from "@/lib/apiError";
import logger from "@/lib/logger";
import { cn } from "@/lib/utils";

/**
 * The six ready-made agents, offered where an account has none.
 *
 * The catalogue has existed in the API since before this screen did, and
 * nothing rendered it — a new account met "No active workflows found" and a
 * button to the wizard, which asks eleven questions and then runs a language
 * model to write a flow. That is the right door for a business we have no
 * template for. It is the wrong one for a dental clinic when a dental clinic
 * template already exists, is better than anything written on the spot, and
 * opens in about a second.
 *
 * One click creates the agent and opens it. There is no preview step: the
 * agent it makes is editable, discardable, and faster to read than any summary
 * of it would be.
 *
 * The one question asked before that click is the voice. It is the first thing
 * anybody wants to change about a ready-made agent, and it sits above the grid
 * rather than on each card so that choosing a template stays one click — a
 * decision per card would be six copies of the same question.
 */

/** Male or female, or neither — see VoiceChoice. */
type VoiceGender = "male" | "female";

type TemplateCard = {
    id: string;
    name: string;
    vertical: string;
    direction: string;
    summary: string;
    languages: string[];
};

/**
 * Which voice the agent gets, asked once for the whole grid.
 *
 * Both buttons deselect on a second press, and nothing is selected to begin
 * with. That third state is the important one: it means "use the workspace
 * default", which is what a template has always done, so somebody who does not
 * care about the voice is not made to have an opinion about it.
 */
function VoiceChoice({
    value,
    onChange,
    disabled,
}: {
    value: VoiceGender | null;
    onChange: (next: VoiceGender | null) => void;
    disabled: boolean;
}) {
    const options: { id: VoiceGender; label: string }[] = [
        { id: "female", label: "Female" },
        { id: "male", label: "Male" },
    ];

    return (
        <div className="mb-4 flex flex-wrap items-center gap-2">
            <span id="template-voice-label" className="text-xs text-muted-foreground">
                Voice
            </span>
            <div className="flex gap-1.5" role="group" aria-labelledby="template-voice-label">
                {options.map((option) => {
                    const active = value === option.id;
                    return (
                        <button
                            key={option.id}
                            type="button"
                            disabled={disabled}
                            aria-pressed={active}
                            onClick={() => onChange(active ? null : option.id)}
                            className={cn(
                                "rounded-full border px-3 py-1 text-xs transition-colors disabled:cursor-wait disabled:opacity-60",
                                active
                                    ? "border-primary bg-primary/10 font-medium text-foreground"
                                    : "border-border text-muted-foreground hover:bg-muted/40",
                            )}
                        >
                            {option.label}
                        </button>
                    );
                })}
            </div>
            <span className="text-xs text-muted-foreground">
                {value
                    ? "You can change this on the agent afterwards."
                    : "Leave unset to use your workspace default."}
            </span>
        </div>
    );
}

export function StartFromTemplate({
    /**
     * Set on the create wizard, where this is an alternative to filling in the
     * form rather than a suggestion attached to one. Adds the surround and the
     * "or describe your own" rule.
     *
     * Both live in here rather than in the caller because the decision not to
     * render at all is made in here: a divider drawn by the parent would
     * survive an empty catalogue and separate nothing from the form.
     */
    framed = false,
}: {
    framed?: boolean;
} = {}) {
    const router = useRouter();
    const [templates, setTemplates] = useState<TemplateCard[] | null>(null);
    const [creating, setCreating] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    /**
     * Nothing selected means "leave it alone", and that is the default on
     * purpose: it is what every template did before this existed, and it is
     * the only honest starting state. There is no gender to preselect —
     * Sarvam's own default speaker is female on bulbul:v2 and male on v3, so
     * any preselection here would be a claim about the tier that stops being
     * true when the tier moves.
     */
    const [voiceGender, setVoiceGender] = useState<VoiceGender | null>(null);

    useEffect(() => {
        let cancelled = false;
        void (async () => {
            const response = await client.get({ url: "/api/v1/agent-templates" });
            if (cancelled || response.error) {
                // Silent: templates are an offer, not the page. If they cannot
                // be fetched the create button below still works.
                setTemplates([]);
                return;
            }
            const data = response.data as { templates?: TemplateCard[] } | undefined;
            setTemplates(data?.templates ?? []);
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const start = async (template: TemplateCard) => {
        setCreating(template.id);
        setError(null);
        const response = await client.post({
            url: `/api/v1/agent-templates/${template.id}/create`,
            // A gender, never a speaker name. The backend resolves it to a real
            // voice when the call starts, against whichever vendor the managed
            // tier is on then — see DECIBYL_GENDER_VOICES.
            body: voiceGender ? { voice_gender: voiceGender } : undefined,
        });
        if (response.error) {
            setCreating(null);
            const message = detailFromResult(response, "Could not start from that template.");
            logger.error(`Template create failed: ${message}`);
            setError(message);
            return;
        }
        const created = response.data as { id?: number } | undefined;
        if (created?.id == null) {
            setCreating(null);
            setError("The agent was created but we could not open it. It is in your list.");
            return;
        }
        router.push(`/workflow/${created.id}`);
    };

    if (templates === null || templates.length === 0) return null;

    return (
        <div className={framed ? "space-y-6" : "mt-4"}>
            <div className={framed ? "rounded-lg border bg-muted/20 p-4" : undefined}>
            <h3 className="mb-1 text-sm font-medium">Start from a ready-made agent</h3>
            <p className="mb-3 text-sm text-muted-foreground">
                Each one is a working agent for that business. Open it, change what you
                need, and put it on a number.
            </p>

            {error && (
                <p className="mb-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                    {error}
                </p>
            )}

            <VoiceChoice value={voiceGender} onChange={setVoiceGender} disabled={creating !== null} />

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {templates.map((template) => (
                    <button
                        key={template.id}
                        type="button"
                        disabled={creating !== null}
                        onClick={() => void start(template)}
                        className="rounded-lg border border-border p-4 text-left transition-colors hover:bg-muted/40 disabled:cursor-wait disabled:opacity-60"
                    >
                        <span className="flex items-center justify-between gap-2">
                            <span className="font-medium">{template.name}</span>
                            {creating === template.id && (
                                <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                            )}
                        </span>
                        <span className="mt-1 block text-xs text-muted-foreground">
                            {template.summary}
                        </span>
                        <span className="mt-2 block text-xs text-muted-foreground">
                            {template.direction === "inbound" ? "Answers calls" : "Makes calls"}
                            {template.languages.length > 0 &&
                                ` · ${template.languages.length} languages`}
                        </span>
                    </button>
                ))}
            </div>
            </div>

            {/* Only reached when there are templates to be an alternative to.
                A rule drawn by the caller would survive an empty catalogue and
                separate nothing from the form below it. */}
            {framed && (
                <div className="flex items-center gap-3">
                    <span className="h-px flex-1 bg-border" />
                    <span className="text-xs uppercase tracking-wide text-muted-foreground">
                        or describe your own
                    </span>
                    <span className="h-px flex-1 bg-border" />
                </div>
            )}
        </div>
    );
}
