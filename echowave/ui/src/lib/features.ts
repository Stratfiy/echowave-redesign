import { useAppConfig } from "@/context/AppConfigContext";
import { useOrgFeatures } from "@/context/OrgConfigContext";

/**
 * Whether a switched-off feature is on, from the flags the backend reports
 * on /health (api/services/features.py) -- already fetched once at start-up
 * by AppConfigProvider -- merged with the signed-in organisation's own map
 * from /api/v1/features (FEATURE_ORG_OVERRIDES, FLAG-1), fetched by
 * OrgConfigProvider. False until an answer arrives, so nothing about a
 * feature flashes on screen before it is known to exist.
 */
export type Feature =
    | "task_board"
    | "dialer_import"
    | "workspace_roles"
    | "agent_graph_extras"
    | "shell"
    | "voice_watch"
    | "charge_rule"
    | "procurement_docs"
    | "decibyl_long_tasks"
    | "decibyl_private_threads"
    | "approvals"
    | "vendor_metering"
    | "managed_realtime_gemini_only"
    | "connections_per_person"
    | "personal_memory"
    | "table_tools"
    | "budget_policies"
    | "plan_ladder"
    | "decibyl_tools"
    | "agent_builder"
    | "managed_telephony"
    // Launch flags, 4 October 2026 (FLAG-1, KAN-275).
    | "invite_only_signup"
    | "trial_plan"
    | "byok_text"
    | "marketplace_publishing"
    | "whatsapp_channel_ui"
    | "voice_number_flow"
    | "approval_scopes"
    | "projects"
    | "agent_faces"
    | "ui_shell_v2"
    | "decibyl_channels"
    | "decibyl_telegram"
    | "decibyl_slack"
    | "decibyl_teams"
    | "studio"
    // The Windows and Mac app (echowave/desktop), and working on a person's
    // own computer from it.
    | "desktop_app"
    | "desktop_computer_use"
    // Free while we are early: no plans, nothing charged (on by default).
    | "free_mode";

export function useFeature(name: Feature): boolean {
    const { config } = useAppConfig();
    const orgFeatures = useOrgFeatures();
    return Boolean(config?.features?.[name]) || Boolean(orgFeatures?.[name]);
}
