import { useAppConfig } from "@/context/AppConfigContext";

/**
 * Whether a switched-off feature is on, from the flags the backend reports
 * on /health (api/services/features.py) -- already fetched once at start-up
 * by AppConfigProvider. False until that answer arrives, so nothing about a
 * feature flashes on screen before it is known to exist.
 */
export type Feature = "task_board" | "dialer_import" | "workspace_roles" | "agent_graph_extras" | "shell" | "voice_watch" | "charge_rule" | "procurement_docs" | "decibyl_long_tasks" | "decibyl_private_threads" | "approvals" | "vendor_metering" | "managed_realtime_gemini_only" | "connections_per_person" | "personal_memory";

export function useFeature(name: Feature): boolean {
    const { config } = useAppConfig();
    return Boolean(config?.features?.[name]);
}
