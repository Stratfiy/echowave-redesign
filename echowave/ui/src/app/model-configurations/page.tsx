/**
 * Models, in two vocabularies.
 *
 * **Simple** asks how the agent should sound and think, and shows one price a
 * minute. **Advanced** names providers and models, because somebody pointing
 * us at an account they pay for has to see them.
 *
 * Split by audience rather than by feature. The buyer this product is aimed at
 * — a clinic owner, a dealership — does not know Sarvam from OpenAI and should
 * not have to learn in order to answer a phone. Splitting the four slots into
 * "the basic four" and "the rest" would have kept that vocabulary problem and
 * only changed how much of it appeared at once.
 *
 * Both write the same thing: a managed slot naming a tier, resolved to a
 * vendor at call time.
 */

"use client";

import { useState } from "react";

import { SimpleModelPicker } from "@/components/agent/SimpleModelPicker";
import ModelConfigurationV2 from "@/components/ModelConfigurationV2";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";
import { cn } from "@/lib/utils";

const TABS = [
    ["simple", "Simple"],
    ["advanced", "Advanced"],
] as const;

type Tab = (typeof TABS)[number][0];

export default function ServiceConfigurationPage() {
    // Simple first, deliberately. The person who needs Advanced knows to look
    // for it; the person who needs Simple does not know that Advanced is the
    // thing confusing them.
    const [tab, setTab] = useState<Tab>("simple");

    return (
        <div className="min-h-screen">
            <div className="container mx-auto px-4 py-8">
                <div className="mx-auto max-w-4xl space-y-6">
                    <div
                        role="tablist"
                        aria-label="How much detail to show"
                        className="inline-flex rounded-lg border border-border p-1"
                    >
                        {TABS.map(([key, label]) => (
                            <button
                                key={key}
                                role="tab"
                                type="button"
                                aria-selected={tab === key}
                                onClick={() => setTab(key)}
                                className={cn(
                                    "rounded-md px-4 py-1.5 text-sm transition-colors",
                                    tab === key
                                        ? "bg-primary text-primary-foreground"
                                        : "text-muted-foreground hover:text-foreground",
                                )}
                            >
                                {label}
                            </button>
                        ))}
                    </div>

                    {tab === "simple" ? (
                        <SimpleModelPicker />
                    ) : (
                        <ModelConfigurationV2
                            docsUrl={SETTINGS_DOCUMENTATION_URLS.modelOverrides}
                        />
                    )}
                </div>
            </div>
        </div>
    );
}
