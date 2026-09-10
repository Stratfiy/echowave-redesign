import { redirect } from "next/navigation";

/**
 * Models moved onto the agent. What this page held — the workspace default —
 * is a setting now, under Settings → Model defaults. The route stays so old
 * links, bookmarks and the docs keep landing somewhere useful.
 */
export default function ModelConfigurationsPage() {
    redirect("/settings#model-defaults");
}
