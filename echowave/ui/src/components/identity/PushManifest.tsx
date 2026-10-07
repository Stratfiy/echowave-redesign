"use client";

/**
 * The web app manifest, linked only while notifications are switched on
 * (launch stream identity). iPhones deliver web push only to a site added to
 * the Home Screen, which needs a manifest; nobody else gets one, so turning
 * the switch off restores today's page exactly.
 */

import { useEffect } from "react";

import { useFeature } from "@/lib/features";

export function PushManifest() {
    const on = useFeature("identity_notifications");
    useEffect(() => {
        if (!on || document.querySelector('link[rel="manifest"]')) return;
        const link = document.createElement("link");
        link.rel = "manifest";
        link.href = "/manifest.webmanifest";
        link.dataset.identity = "push";
        document.head.appendChild(link);
        return () => link.remove();
    }, [on]);
    return null;
}

export default PushManifest;
