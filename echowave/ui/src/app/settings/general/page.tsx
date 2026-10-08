"use client";

import GeneralSettings from "@/components/settings/GeneralSettings";
import { useFeature } from "@/lib/features";

// General on a phone: /settings is the list of sections there, so General
// needs an address of its own. With the Settings shell on, its old links land
// on the workspace's defaults, which is what General held.
export default function GeneralSettingsPage() {
  const shell = useFeature("settings_shell");
  return <GeneralSettings workspaceOnly={shell} />;
}
