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
 * Simple saves a managed bundle. Advanced saves the exact stack shown in its
 * editor. Remember the view the customer chose: reopening on Simple after an
 * Advanced save makes the saved stack look as though it was replaced.
 *
 * The title sits on the page rather than inside a tab, because both tabs set
 * the same thing and the Simple tab — the one most people land on — used to
 * open on a bare pair of buttons with nothing saying what the screen was for.
 * It also says out loud that this is the organization's default, since an
 * agent now picks its own bundle while being created and the two answers can
 * differ.
 */

"use client";

import { ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";

import { SimpleModelPicker } from "@/components/agent/SimpleModelPicker";
import ModelConfigurationV2 from "@/components/ModelConfigurationV2";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";
import { UnsavedChangesProvider } from "@/context/UnsavedChangesContext";
import { cn } from "@/lib/utils";

const TABS = [
    ["simple", "Simple"],
    ["advanced", "Advanced"],
] as const;

type Tab = (typeof TABS)[number][0];

const TAB_STORAGE_KEY = "decibyl:model-configuration-view";

export default function ServiceConfigurationPage() {
    const [tab, setTab] = useState<Tab>("simple");

    useEffect(() => {
        if (localStorage.getItem(TAB_STORAGE_KEY) === "advanced") {
            setTab("advanced");
        }
    }, []);

    const selectTab = (next: Tab) => {
        setTab(next);
        localStorage.setItem(TAB_STORAGE_KEY, next);
    };

    // The advanced tab holds an editable model stack. Switching tabs or
    // navigating away with it half-changed used to discard it silently.
    return (
        <UnsavedChangesProvider>
        <div className="min-h-screen">
            <div className="container mx-auto px-4 py-8">
                <div className="mx-auto max-w-6xl space-y-6">
                    <div>
                        <h1 className="text-3xl font-bold">Models</h1>
                        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
                            The default for every agent in this organization. An agent can
                            choose a different bundle while you create it, and that choice
                            wins for that agent.{" "}
                            <a
                                href={SETTINGS_DOCUMENTATION_URLS.modelOverrides}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-0.5 underline"
                            >
                                Learn more <ExternalLink className="h-3 w-3" />
                            </a>
                        </p>
                    </div>

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
                                onClick={() => selectTab(key)}
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

                    <p className="-mt-3 text-sm text-muted-foreground">
                        {tab === "simple"
                            ? "Pick how the agent should sound and think, and see what a minute costs."
                            : "Name the exact provider and model for each part of the call."}
                    </p>

                    {tab === "simple" ? (
                        <SimpleModelPicker />
                    ) : (
                        <ModelConfigurationV2 guardUnsavedChanges />
                    )}
                </div>
            </div>
        </div>
        </UnsavedChangesProvider>
    );
}
