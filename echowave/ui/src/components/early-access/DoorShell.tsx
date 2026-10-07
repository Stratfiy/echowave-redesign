/**
 * The door's one column (screens 01 and 02): the logo, a heading, and a
 * form no wider than the design's 440px (520px for onboarding). Plain app
 * background, no carousel, no pricing. 24px side margins on a phone and the
 * safe-area insets on every edge, so nothing sits under a notch or the home
 * indicator.
 */

import type { ReactNode } from "react";

import { BrandLogo } from "@/components/BrandLogo";
import { cn } from "@/lib/utils";

export function DoorShell({
    title,
    lead,
    width = "form",
    children,
}: {
    title: ReactNode;
    lead?: ReactNode;
    /** `form` is 440px (screen 01); `wide` is 520px (screen 02). */
    width?: "form" | "wide";
    children: ReactNode;
}) {
    return (
        <div
            className="flex min-h-dvh w-full flex-col items-center overflow-x-hidden bg-background pb-[max(2.5rem,env(safe-area-inset-bottom))] pl-[max(1.5rem,env(safe-area-inset-left))] pr-[max(1.5rem,env(safe-area-inset-right))] pt-[max(2.5rem,env(safe-area-inset-top))] text-foreground"
        >
            <header className="flex flex-col items-center">
                <BrandLogo className="h-8" />
            </header>
            <main className={cn("mt-8 w-full", width === "wide" ? "max-w-[520px]" : "max-w-[440px]")}>
                <h1 className="text-balance text-2xl font-semibold leading-8 tracking-tight">{title}</h1>
                {lead && <p className="mt-2 text-base leading-[26px] text-muted-foreground">{lead}</p>}
                <div className="mt-6">{children}</div>
            </main>
        </div>
    );
}

export default DoorShell;
