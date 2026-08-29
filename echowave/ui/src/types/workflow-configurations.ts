import type {
    AmbientNoiseConfigurationDefaults,
    OrganizationAiModelConfigurationV2,
    WorkflowConfigurationDefaults as GeneratedWorkflowConfigurationDefaults,
} from "@/client/types.gen";

export type WorkflowConfigurationDefaults = GeneratedWorkflowConfigurationDefaults;

export type AmbientNoiseConfiguration = Omit<
    AmbientNoiseConfigurationDefaults,
    "enabled" | "volume"
> & {
    enabled: boolean;
    volume: number;
    storage_key?: string;
    storage_backend?: string;
    original_filename?: string;
};

export type TurnStopStrategy = NonNullable<GeneratedWorkflowConfigurationDefaults["turn_stop_strategy"]>;
export type TurnStartStrategy = NonNullable<GeneratedWorkflowConfigurationDefaults["turn_start_strategy"]>;
export const DEFAULT_TURN_START_MIN_WORDS = 3;
// Mirrors DEFAULT_USER_SPEECH_TIMEOUT in api/schemas/workflow_configurations.py,
// where the 0.15 floor is also enforced.
export const DEFAULT_USER_SPEECH_TIMEOUT = 0.4;
export const MIN_USER_SPEECH_TIMEOUT = 0.15;
export const MAX_USER_SPEECH_TIMEOUT = 3.0;
export const DEFAULT_PROVISIONAL_VAD_PAUSE_SECS = 1.5;

/**
 * Named response-rate settings, in the spirit of Bolna's Rapid/Normal/Patient.
 *
 * The three numbers below interact, so exposing them as independent sliders
 * means every caller who touches one is running a combination nobody has
 * tested. A preset is a tested tuple with a name, and it collapses that space
 * to three points plus an escape hatch.
 *
 * Deliberately NOT a stored field. The selected preset is derived from the
 * numbers themselves (see `matchLatencyPreset`), so:
 *   - no migration, and no new column to keep in sync with the values;
 *   - a workflow already tuned by hand keeps its numbers and simply reads as
 *     "Custom" — picking a preset is the only thing that overwrites them;
 *   - the pipeline keeps reading the same three fields it always did.
 *
 * `balanced` is exactly the committed defaults, so every existing workflow
 * lands on it unchanged.
 */
export const LATENCY_PRESETS = {
    rapid: {
        label: 'Rapid',
        blurb: 'Short confirmations and IVR-style flows. Cuts in quickly; will occasionally clip someone who pauses mid-sentence.',
        values: { user_speech_timeout: 0.2, smart_turn_stop_secs: 1.0, turn_start_min_words: 2 },
    },
    balanced: {
        label: 'Balanced',
        blurb: 'The default, and what every workflow runs today. Start here and only move if calls tell you to.',
        values: { user_speech_timeout: DEFAULT_USER_SPEECH_TIMEOUT, smart_turn_stop_secs: 2.0, turn_start_min_words: DEFAULT_TURN_START_MIN_WORDS },
    },
    patient: {
        label: 'Patient',
        blurb: 'Callers reading out numbers, thinking mid-sentence, or on a noisy line. Waits longer and interrupts less.',
        values: { user_speech_timeout: 0.8, smart_turn_stop_secs: 3.0, turn_start_min_words: 5 },
    },
} as const;

export type LatencyPreset = keyof typeof LATENCY_PRESETS;
export type LatencyPresetOrCustom = LatencyPreset | 'custom';

/** Which preset these settings correspond to, or 'custom' if none. */
export function matchLatencyPreset(
    config: Pick<
        WorkflowConfigurations,
        'user_speech_timeout' | 'smart_turn_stop_secs' | 'turn_start_min_words'
    >,
): LatencyPresetOrCustom {
    for (const [name, preset] of Object.entries(LATENCY_PRESETS)) {
        const v = preset.values;
        if (
            config.user_speech_timeout === v.user_speech_timeout
            && config.smart_turn_stop_secs === v.smart_turn_stop_secs
            && config.turn_start_min_words === v.turn_start_min_words
        ) {
            return name as LatencyPreset;
        }
    }
    return 'custom';
}

export const TURN_START_STRATEGY_OPTIONS: Array<{
    value: TurnStartStrategy;
    label: string;
    description: string;
}> = [
    {
        value: 'default',
        label: 'Automatic (Recommended)',
        description: 'Recommended. Uses the transcriber\'s own turn signals when it has them, otherwise requires a minimum number of words — so background noise cannot cut the agent off.',
    },
    {
        value: 'min_words',
        label: 'Minimum words',
        description: 'Wait for a minimum number of transcribed words before interrupting bot speech.',
    },
    {
        value: 'provisional_vad',
        label: 'Provisional VAD',
        description: 'Pause bot audio on voice activity, then confirm the interruption with transcription.',
    },
    {
        value: 'vad',
        label: 'Voice activity only',
        description: 'Interrupt on any sound loud enough to read as speech. Fastest to react and the least discriminating — a cough or background talk will stop the agent. Use only if your transcriber emits no interim results.',
    },
];

/** One backup in an ordered chain. Mirrors FallbackServiceConfiguration. */
export interface FallbackService {
    provider: string;
    model?: string;
    voice?: string;
    language?: string;
}

export interface VoicemailDetectionConfiguration {
    enabled: boolean;
    use_workflow_llm: boolean;
    provider?: string;
    model?: string;
    api_key?: string;
    system_prompt?: string;
    long_speech_timeout: number;  // seconds cutoff for long speech detection
}

export const DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION: VoicemailDetectionConfiguration = {
    enabled: false,
    use_workflow_llm: true,
    long_speech_timeout: 8.0,
};

export interface TranscriptConfiguration {
    include_end_timestamps: boolean;
}

export const DEFAULT_TRANSCRIPT_CONFIGURATION: TranscriptConfiguration = {
    include_end_timestamps: false,
};

export interface ModelOverrides {
    llm?: {
        provider?: string;
        model?: string;
        api_key?: string;
        [key: string]: unknown;
    };
    tts?: {
        provider?: string;
        model?: string;
        voice?: string;
        api_key?: string;
        [key: string]: unknown;
    };
    stt?: {
        provider?: string;
        model?: string;
        api_key?: string;
        [key: string]: unknown;
    };
    realtime?: {
        provider?: string;
        model?: string;
        voice?: string;
        api_key?: string;
        [key: string]: unknown;
    };
    is_realtime?: boolean;
}

type WorkflowConfigurationBase = Omit<
    GeneratedWorkflowConfigurationDefaults,
    | "ambient_noise_configuration"
    | "max_call_duration"
    | "max_user_idle_timeout"
    | "smart_turn_stop_secs"
    | "turn_start_strategy"
    | "turn_start_min_words"
    | "provisional_vad_pause_secs"
    | "turn_stop_strategy"
    | "user_speech_timeout"
    | "dictionary"
    | "context_compaction_enabled"
>;

export type WorkflowConfigurations = WorkflowConfigurationBase & {
    ambient_noise_configuration: AmbientNoiseConfiguration;
    max_call_duration: number;  // Maximum call duration in seconds
    max_user_idle_timeout: number;  // Maximum user idle time in seconds
    smart_turn_stop_secs: number;  // Timeout in seconds for incomplete turn detection
    turn_start_strategy: TurnStartStrategy;  // Strategy for detecting start of user turn/interruption
    turn_start_min_words: number;  // Minimum transcribed words required for minimum-word interruptions
    provisional_vad_pause_secs: number;  // Seconds to pause bot output while awaiting transcript confirmation
    turn_stop_strategy: TurnStopStrategy;  // Strategy for detecting end of user turn
    user_speech_timeout: number;  // Silence after VAD stop before the turn ends; "transcription" strategy only
    dictionary?: string;  // Comma-separated words for voice agent to listen for
    fallback_tts?: FallbackService[];  // Ordered voice backups, tried when the one before fails
    fallback_stt?: FallbackService[];  // Ordered transcriber backups
    voicemail_detection?: VoicemailDetectionConfiguration;
    transcript_configuration: TranscriptConfiguration;
    context_compaction_enabled: boolean;  // Summarize context on node transitions to remove stale tool calls
    model_overrides?: ModelOverrides;  // Per-workflow model configuration overrides
    model_configuration_v2_override?: OrganizationAiModelConfigurationV2;  // Full v2 model configuration override
    [key: string]: unknown;  // Allow additional properties for future configurations
};

const FALLBACK_WORKFLOW_CONFIGURATIONS: WorkflowConfigurations = {
    ambient_noise_configuration: {
        enabled: false,
        volume: 0.3
    },
    max_call_duration: 300,
    max_user_idle_timeout: 10,  // 10 seconds
    smart_turn_stop_secs: 2,  // 2 seconds
    turn_start_strategy: 'default',  // Default to platform-chosen user turn start detection
    turn_start_min_words: DEFAULT_TURN_START_MIN_WORDS,
    provisional_vad_pause_secs: DEFAULT_PROVISIONAL_VAD_PAUSE_SECS,
    turn_stop_strategy: 'turn_analyzer',  // Local model ends the turn; see DEFAULT_TURN_STOP_STRATEGY
    user_speech_timeout: DEFAULT_USER_SPEECH_TIMEOUT,
    dictionary: '',
    transcript_configuration: DEFAULT_TRANSCRIPT_CONFIGURATION,
    context_compaction_enabled: false,
};

export function resolveWorkflowConfigurations(
    configurations?: Partial<WorkflowConfigurations> | null,
    defaults?: WorkflowConfigurationDefaults | null,
): WorkflowConfigurations {
    return {
        ...FALLBACK_WORKFLOW_CONFIGURATIONS,
        ...defaults,
        ...configurations,
        ambient_noise_configuration: {
            ...FALLBACK_WORKFLOW_CONFIGURATIONS.ambient_noise_configuration,
            ...defaults?.ambient_noise_configuration,
            ...configurations?.ambient_noise_configuration,
        },
        max_call_duration:
            configurations?.max_call_duration
            ?? defaults?.max_call_duration
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.max_call_duration,
        max_user_idle_timeout:
            configurations?.max_user_idle_timeout
            ?? defaults?.max_user_idle_timeout
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.max_user_idle_timeout,
        smart_turn_stop_secs:
            configurations?.smart_turn_stop_secs
            ?? defaults?.smart_turn_stop_secs
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.smart_turn_stop_secs,
        turn_start_strategy:
            configurations?.turn_start_strategy
            ?? defaults?.turn_start_strategy
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.turn_start_strategy,
        turn_start_min_words:
            configurations?.turn_start_min_words
            ?? defaults?.turn_start_min_words
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.turn_start_min_words,
        provisional_vad_pause_secs:
            configurations?.provisional_vad_pause_secs
            ?? defaults?.provisional_vad_pause_secs
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.provisional_vad_pause_secs,
        turn_stop_strategy:
            configurations?.turn_stop_strategy
            ?? defaults?.turn_stop_strategy
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.turn_stop_strategy,
        user_speech_timeout:
            configurations?.user_speech_timeout
            ?? defaults?.user_speech_timeout
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.user_speech_timeout,
        dictionary:
            configurations?.dictionary
            ?? defaults?.dictionary
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.dictionary,
        context_compaction_enabled:
            configurations?.context_compaction_enabled
            ?? defaults?.context_compaction_enabled
            ?? FALLBACK_WORKFLOW_CONFIGURATIONS.context_compaction_enabled,
        transcript_configuration: {
            ...DEFAULT_TRANSCRIPT_CONFIGURATION,
            ...(defaults?.transcript_configuration as Partial<TranscriptConfiguration> | undefined),
            ...(configurations?.transcript_configuration as Partial<TranscriptConfiguration> | undefined),
        },
    };
}
