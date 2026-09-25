import { useAppConfig } from "@/context/AppConfigContext";

/**
 * Whether a switched-off feature is on, from the flags the backend reports
 * on /health (api/services/features.py) -- already fetched once at start-up
 * by AppConfigProvider. False until that answer arrives, so nothing about a
 * feature flashes on screen before it is known to exist.
 */
export type Feature = "task_board" | "dialer_import" | "workspace_roles" | "agent_graph_extras" | "shell" | "voice_watch" | "charge_rule";

export function useFeature(name: Feature): boolean {
    const { config } = useAppConfig();
    return Boolean(config?.features?.[name]);
}
