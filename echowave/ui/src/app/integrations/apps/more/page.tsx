"use client";

/**
 * The long tail is a chip on the Integrations shelf now ("Other"), and
 * search covers it. The route stays so old links do not 404.
 */

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import SpinLoader from "@/components/SpinLoader";

export default function IntegrationsAppsMorePage() {
    const router = useRouter();
    useEffect(() => {
        router.replace("/marketplace/integrations");
    }, [router]);
    return <SpinLoader />;
}
