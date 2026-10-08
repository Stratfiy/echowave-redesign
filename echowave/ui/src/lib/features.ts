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
    | "free_mode"
    // Launch stream controls (LAUNCH-PLAN.md, phase 1).
    | "capability_checklist"
    | "operational_quotas"
    | "task_ledger"
    | "personal_space"
    | "member_preferences"
    | "event_catalogue"
    | "reply_feedback"
    // Launch stream `shell` (LAUNCH-PLAN.md, phase 1).
    | "early_access"
    | "first_task_onboarding"
    | "chat_shell"
    | "shell_mobile"
    // Launch stream `agents` (LAUNCH-PLAN.md, phase 2).
    | "launch_helpers"
    | "research_reports"
    | "follow_up_ledger"
    | "trading_summaries"
    | "describe_builder"
    // Launch stream `today` (LAUNCH-PLAN.md, phase 2).
    | "today_list"
    | "approval_dock"
    | "today_reminders"
    | "daily_brief"
    | "end_of_day_note"
    | "routine_start_on"
    // Launch stream `support` (LAUNCH-PLAN.md, phase 2).
    | "support_help"
    | "support_inbox"
    | "support_actions"
    // Stream ops (handoff 11, 14, 15 G-H, 34, 35).
    | "ops_console"
    | "server_analytics"
    | "telemetry_redaction"
    | "session_replay"
    | "laya_guardrails"
    | "laya_rollback"
    | "cost_stop"
    // Decibyl's private browser (stream browser).
    | "decibyl_browser"
    // Launch stream `care` (LAUNCH-PLAN.md, phase 2).
    | "care_simple_mode"
    | "care_medicine_calls"
    | "care_scam_check"
    | "care_tech_help"
    | "care_family_circle"
    // Launch stream `reach` (LAUNCH-PLAN.md, phase 2).
    | "outside_tools"
    | "ordering"
    | "price_compare"
    // Launch stream `learning` (LAUNCH-PLAN.md, phase 2).
    | "learning"
    | "learning_today"
    // Launch stream `meetings` (LAUNCH-PLAN.md, phase 2).
    | "meeting_capture"
    // Launch stream `staff` (LAUNCH-PLAN.md, phase 2; STAFF.md).
    | "staff_console"
    | "staff_roles"
    | "staff_refunds"
    | "staff_evaluations"
    | "staff_incidents"
    // Launch stream `identity` (LAUNCH-PLAN.md, phase 2).
    | "identity_connections"
    | "identity_email"
    | "identity_phone"
    | "identity_notifications"
    | "identity_reconciliation"
    // Launch stream `settings` (LAUNCH-PLAN.md, phase 2; SETTINGS.md).
    | "settings_shell"
    | "memory_manager"
    | "privacy_center"
    | "saved_items"
    | "model_inheritance"
    // Launch stream `voice` (LAUNCH-PLAN.md, phase 2).
    | "decibyl_voice"
    | "voice_latency"
    | "call_for_me"
    | "call_appointment"
    // People: synced contacts with context (PEOPLE.md).
    | "people"
    // Voice isolation: background voices on calls (VOICE.md).
    | "caller_voice_lock"
    | "deepfilternet_filter";

export function useFeature(name: Feature): boolean {
    const { config } = useAppConfig();
    const orgFeatures = useOrgFeatures();
    return Boolean(config?.features?.[name]) || Boolean(orgFeatures?.[name]);
}
