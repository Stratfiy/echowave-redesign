"use client";

/**
 * Settings -> Personal -> Personalization (screen 18; handoff 24).
 *
 * Language and answer style first, instructions next, memory last. Every
 * field has one short helper line; related changes save together with Save.
 * Instructions are the person's preferences -- saving them grants no new
 * permission, and the page says so. Memory is opt-in and lives in the memory
 * manager; a temporary conversation is a clear new-session action.
 */

import Link from "next/link";
import { useState } from "react";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { SettingsSection } from "@/components/shell/SettingsSection";
import { Button } from "@/components/ui/button";
import { UnsavedChangesProvider } from "@/context/UnsavedChangesContext";
import { useFeature } from "@/lib/features";
import { cn } from "@/lib/utils";

import { LanguagePicker } from "../LanguagePicker";
import { Field, SettingsFormFrame } from "../SettingsForm";
import { TemporaryConversationButton } from "../TemporaryConversationButton";
import { type ProfileField, useProfileForm } from "../useProfileForm";

const FIELDS: readonly ProfileField[] = ["language", "explanation_language", "response_length", "custom_instructions"];
const LABELS: Partial<Record<ProfileField, string>> = {
    language: "Your language",
    explanation_language: "Explanation language",
    response_length: "Answer length",
    custom_instructions: "Your instructions",
};

const LENGTHS = [
    { value: "short", label: "Short", help: "A few sentences unless you ask for more." },
    { value: "balanced", label: "Balanced", help: "As long as the question needs." },
    { value: "detailed", label: "Detailed", help: "Fuller answers with the reasoning and steps." },
] as const;

/** Shown only on request, and made up on this page -- no model is asked. */
const EXAMPLE: Record<string, string> = {
    short: "Your GST return is due on the 20th. ₹12,400 is payable.",
    balanced:
        "Your GST return (GSTR-3B) is due on the 20th. Based on last month's invoices, ₹12,400 is payable. I can draft the payment for your OK.",
    detailed:
        "Your GSTR-3B for this month is due on the 20th. From the 14 invoices recorded, output tax is ₹18,900 and input credit ₹6,500, so ₹12,400 is payable. I can draft the payment for your OK, or remind you on the 18th.",
};

function PersonalizationForm() {
    const form = useProfileForm("settings-personalization", FIELDS);
    const memoryManager = useFeature("memory_manager");
    const [example, setExample] = useState(false);
    const languages = form.stored?.languages ?? [];
    const max = form.stored?.max_instructions ?? 1500;
    const instructions = (form.draft.custom_instructions as string | null) ?? "";
    const length = (form.draft.response_length as string | null) ?? "balanced";
    const over = instructions.length > max;

    return (
        <SettingsFormFrame form={form} labels={LABELS}>
            <SettingsSection id="language" title="Language and answer style" description="Applies from your next message." scope="Just you">
                <div className="flex flex-col gap-6">
                    <Field id="reply-language" label="Your language" help="Decibyl replies, and speaks, in it unless you write in another.">
                        <LanguagePicker
                            id="reply-language"
                            languages={languages}
                            value={(form.draft.language as string | null) ?? null}
                            onChange={(tag) => form.set("language", tag)}
                            allowNone
                            noneLabel="Match how I write"
                        />
                    </Field>
                    <Field id="explanation-language" label="Explanation language (optional)" help="A second language for explaining something difficult.">
                        <LanguagePicker
                            id="explanation-language"
                            languages={languages}
                            value={(form.draft.explanation_language as string | null) ?? null}
                            onChange={(tag) => form.set("explanation_language", tag)}
                            allowNone
                            noneLabel="None"
                        />
                    </Field>
                    <fieldset id="length" className="scroll-mt-20">
                        <legend className="text-sm font-medium">Answer length</legend>
                        <p className="mt-0.5 text-xs text-muted-foreground">How much Decibyl says by default.</p>
                        <div role="radiogroup" aria-label="Answer length" className="mt-2 grid gap-2 sm:grid-cols-3">
                            {LENGTHS.map((option) => (
                                <button
                                    key={option.value}
                                    type="button"
                                    role="radio"
                                    aria-checked={length === option.value}
                                    onClick={() => form.set("response_length", option.value)}
                                    className={cn(
                                        "motion-m1 min-h-11 rounded-[8px] border px-3 py-2 text-left text-sm",
                                        length === option.value ? "border-foreground bg-accent" : "border-border",
                                    )}
                                >
                                    <span className="block font-medium">{option.label}</span>
                                    <span className="block text-xs text-muted-foreground">{option.help}</span>
                                </button>
                            ))}
                        </div>
                        <div className="mt-2">
                            <Button type="button" variant="ghost" size="sm" className="min-h-11 md:min-h-8" onClick={() => setExample((v) => !v)} aria-expanded={example}>
                                {example ? "Hide the example" : "Show an example"}
                            </Button>
                            {example && (
                                <p className="motion-m2 mt-1 rounded-[8px] bg-muted/50 p-3 text-sm" data-testid="length-example">
                                    {EXAMPLE[length] ?? EXAMPLE.balanced}
                                    <span className="mt-1 block text-xs text-muted-foreground">An illustration written for this page, not a real answer.</span>
                                </p>
                            )}
                        </div>
                    </fieldset>
                </div>
            </SettingsSection>

            <SettingsSection id="instructions" title="Your instructions" description="Anything Decibyl should keep in mind with you." scope="Just you">
                <Field
                    id="instructions-field"
                    label="Instructions"
                    help="Preferences only: they never let Decibyl send, pay, book or delete without asking you."
                    error={over ? `Up to ${max} characters; this is ${instructions.length}. Nothing is cut for you.` : null}
                >
                    <textarea
                        id="instructions-field-input"
                        className="min-h-32 w-full resize-y rounded-[8px] border border-[#7B8491]/60 bg-background p-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring md:text-sm"
                        value={instructions}
                        onChange={(event) => form.set("custom_instructions", event.target.value)}
                        aria-invalid={over || undefined}
                        placeholder="For example: I run a clinic in Pune. Keep money in rupees. Mention my team by first name."
                    />
                    <p className={cn("mt-1 text-right text-xs", over ? "text-destructive" : "text-muted-foreground")} aria-live="polite">
                        {instructions.length} / {max}
                    </p>
                </Field>
            </SettingsSection>

            <SettingsSection id="memory" title="Memory" description="Decibyl remembers things from your conversations only if you turn it on." scope="Just you">
                {memoryManager ? (
                    <div className="flex flex-col gap-3">
                        <p className="text-sm">
                            {form.stored?.memory_enabled ? "Memory is on." : "Memory is off."}{" "}
                            <Link href="/settings/memory" className="underline underline-offset-2">
                                Manage memories
                            </Link>
                        </p>
                        <div id="temporary">
                            <TemporaryConversationButton />
                        </div>
                    </div>
                ) : (
                    <p className="text-sm text-muted-foreground">Managing memory from Settings is not switched on here yet.</p>
                )}
            </SettingsSection>
        </SettingsFormFrame>
    );
}

export function PersonalizationSettings() {
    return (
        <UnsavedChangesProvider>
            <PageHeader title="Personalization" description="How Decibyl talks with you. Only yours." />
            <PageBody className="max-w-[640px]">
                <PersonalizationForm />
            </PageBody>
        </UnsavedChangesProvider>
    );
}

export default PersonalizationSettings;
