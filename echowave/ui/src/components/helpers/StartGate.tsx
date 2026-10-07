"use client";

/**
 * The old build-an-agent journey at /start, kept for links that still point
 * there. With the describe-it builder on (`describe_builder`), nobody is
 * sent through it: /start opens Chat with "Build something" chosen, and a
 * template link becomes the words for the box. Off, it is the journey as it
 * was.
 */

import { useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { useFeatureState } from "@/lib/helpers";

export function builderPath(search: string): string {
    const params = new URLSearchParams(search);
    const template = params.get("template");
    const next = new URLSearchParams({ helper: "builder" });
    if (template) {
        next.set("say", `Set up the ${template.replace(/[_-]+/g, " ")} agent for me`);
    }
    return `/overview?${next.toString()}`;
}

export function StartGate({ children }: { children: ReactNode }) {
    const router = useRouter();
    const state = useFeatureState("describe_builder");
    useEffect(() => {
        if (state === "on") router.replace(builderPath(window.location.search));
    }, [state, router]);
    if (state !== "off") return <div className="min-h-[50vh]" aria-busy="true" />;
    return <>{children}</>;
}
