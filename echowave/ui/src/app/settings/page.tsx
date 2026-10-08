"use client";

import GeneralSettings from "@/components/settings/GeneralSettings";
import { AccountSettings } from "@/components/settings/pages/AccountSettings";
import { useFeature } from "@/lib/features";

/** Settings' first page: Account in the Settings shell (screen 17), General
 *  as it always was while the shell is off. */
export default function SettingsPage() {
  const shell = useFeature("settings_shell");
  return shell ? <AccountSettings /> : <GeneralSettings />;
}
