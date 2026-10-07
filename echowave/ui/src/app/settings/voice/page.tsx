"use client";

/**
 * Settings, Voice and language (screen 19). Shown in the list while
 * `voice_language_settings` is on; an old link while it is off says so
 * rather than showing controls whose routes are not there.
 */

import { AudioLines } from "lucide-react";

import { EmptyState } from "@/components/shell";
import { VoiceLanguageSettings } from "@/components/voice/VoiceLanguageSettings";
import { useFeature } from "@/lib/features";

export default function VoiceSettingsPage() {
    const on = useFeature("voice_language_settings");
    if (!on) {
        return (
            <div className="mx-auto max-w-[640px] p-4 md:p-6">
                <EmptyState
                    icon={AudioLines}
                    title="Voice settings are not switched on yet"
                    description="You can still type, or use Dictate in Chat."
                />
            </div>
        );
    }
    return <VoiceLanguageSettings />;
}
