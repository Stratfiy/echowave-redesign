"use client";

/**
 * Studio: agents and a website for them, built from one chat.
 *
 * Behind the `studio` flag (api/services/features.py). While it is off the
 * page says so rather than rendering a chat whose every request is a 404.
 */

import { PageHeader } from "@/components/layout/PageHeader";
import SpinLoader from "@/components/SpinLoader";
import { StudioWorkspace } from "@/components/studio/StudioWorkspace";
import { useAppConfig } from "@/context/AppConfigContext";
import { useFeature } from "@/lib/features";

export default function StudioPage() {
    const { config } = useAppConfig();
    const on = useFeature("studio");
    return (
        <div className="flex min-h-0 flex-1 flex-col">
            <PageHeader
                title="Studio"
                description="Agents and a website for them, from one chat."
            />
            <div className="w-full px-4 py-4 sm:px-6">
                {on ? (
                    <StudioWorkspace />
                ) : !config ? (
                    // Flags arrive with the app config; until then nothing
                    // is known, and "not switched on" would be a guess.
                    <SpinLoader />
                ) : (
                    <p className="text-sm text-muted-foreground">
                        Studio is not switched on for this workspace.
                    </p>
                )}
            </div>
        </div>
    );
}
