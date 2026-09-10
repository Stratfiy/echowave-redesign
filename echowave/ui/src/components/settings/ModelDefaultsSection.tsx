"use client";

/**
 * The workspace's default models, in two vocabularies.
 *
 * This was a page of its own — "Models & voices" in the sidebar — from the
 * time the account had one stack and every agent ran on it. Models now live
 * on the agent, the way Vapi and Bolna put them on the assistant, so what is
 * left here is the fallback: what an agent runs on until it picks its own.
 * A fallback is a setting, not a destination, and it sits with the other
 * settings.
 *
 * **Simple** asks how an agent should sound and think and shows one price a
 * minute. **Advanced** names providers and models, because somebody pointing
 * us at an account they pay for has to see them. The view is remembered:
 * reopening on Simple after an Advanced save makes the saved stack look as
 * though it was replaced.
 */

import { useEffect, useState } from "react";

import { SimpleModelPicker } from "@/components/agent/SimpleModelPicker";
import ModelConfigurationV2 from "@/components/ModelConfigurationV2";
import { cn } from "@/lib/utils";

const TABS = [
    ["simple", "Simple"],
    ["advanced", "Advanced"],
] as const;
type Tab = (typeof TABS)[number][0];
const TAB_STORAGE_KEY = "decibyl:model-configuration-view";

export function ModelDefaultsSection() {
    const [tab, setTab] = useState<Tab>("simple");

    useEffect(() => {
        try {
            if (localStorage.getItem(TAB_STORAGE_KEY) === "advanced") setTab("advanced");
        } catch {
            // Storage blocked: open on Simple, which is the right default anyway.
        }
    }, []);

    const selectTab = (next: Tab) => {
        setTab(next);
        try {
            localStorage.setItem(TAB_STORAGE_KEY, next);
        } catch {
            // Not remembering the view is the only consequence.
        }
    };

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
                <div role="tablist" aria-label="How much detail to show" className="inline-flex rounded-full border border-border p-1">
                    {TABS.map(([key, label]) => (
                        <button
                            key={key}
                            role="tab"
                            type="button"
                            aria-selected={tab === key}
                            onClick={() => selectTab(key)}
                            className={cn(
                                "rounded-full px-4 py-1 text-sm transition-colors",
                                tab === key ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground",
                            )}
                        >
                            {label}
                        </button>
                    ))}
                </div>
                <p className="text-sm text-muted-foreground">
                    {tab === "simple"
                        ? "Pick how an agent should sound and think, and see what a minute costs."
                        : "Name the exact provider and model for each part of the call, or use your own keys."}
                </p>
            </div>
            {tab === "simple" ? <SimpleModelPicker /> : <ModelConfigurationV2 guardUnsavedChanges />}
        </div>
    );
}

export default ModelDefaultsSection;
