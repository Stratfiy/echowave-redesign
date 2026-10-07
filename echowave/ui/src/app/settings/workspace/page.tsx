"use client";

import GeneralSettings from "@/components/settings/GeneralSettings";

/** The workspace's own defaults, under its name in the Settings shell. */
export default function WorkspaceDefaultsPage() {
  return <GeneralSettings workspaceOnly />;
}
