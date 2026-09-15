"use client";

/**
 * The app catalogue moved into the Marketplace, where the rest of the
 * shopping is: Tools, Bots, Integrations, one strip. This screen was the
 * same catalogue under a second name, which is how somebody ends up
 * connecting Gmail twice looking for the list they saw yesterday.
 *
 * The route stays so old links and bookmarks do not 404.
 */

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import SpinLoader from "@/components/SpinLoader";

export default function IntegrationsAppsPage() {
    const router = useRouter();
    useEffect(() => {
        router.replace("/marketplace/integrations");
    }, [router]);
    return <SpinLoader />;
}
