"use client";

/** Settings -> Advanced -> Skills (screen 27): the existing skills shelf, in
 *  the Settings shell rather than a second copy of it. Versions, required
 *  tools, the per-agent limit and every permission check stay the shelf's. */
import { MarketplaceScreen } from "@/components/marketplace/MarketplaceScreen";

export default function SettingsSkillsPage() {
    return <MarketplaceScreen kind="skills" />;
}
