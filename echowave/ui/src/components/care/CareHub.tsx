"use client";

/**
 * Care (launch stream `care`): for older people and their families.
 *
 * One thing at a time. The hub is a short list of large choices -- only the
 * parts switched on here -- and choosing one shows that part alone, with one
 * way back. The part is in the address (`?part=`), so the phone's own Back
 * button works and a family member can be sent straight to the right place.
 *
 * Voice first: "Talk to Decibyl" leads, and every box a person would type
 * into has "Say it instead".
 */

import { ArrowLeft, HeartHandshake, MessageCircle, Pill, ShieldQuestion, Smartphone, Users } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { ComponentType } from "react";

import { useSimpleMode } from "@/lib/care/simpleMode";
import { type Feature, useFeature } from "@/lib/features";
import { cn } from "@/lib/utils";

import { CirclePanel } from "./CirclePanel";
import { CARE_POSITIONING_LINE, PART_HINTS, PART_TITLES } from "./copy";
import { DueNow } from "./DueNow";
import { FamilyPanel } from "./FamilyPanel";
import { MedicinesPanel } from "./MedicinesPanel";
import { ScamCheckPanel } from "./ScamCheckPanel";
import { SimpleModeSwitch } from "./SimpleModeSwitch";
import { TechHelpPanel } from "./TechHelpPanel";

export type CarePartId = keyof typeof PART_TITLES;

type Part = {
    id: CarePartId;
    flag: Feature;
    icon: ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
    Panel: ComponentType;
};

/** In the order a worried person reaches for them. */
const PARTS: Part[] = [
    { id: "care_scam_check", flag: "care_scam_check", icon: ShieldQuestion, Panel: ScamCheckPanel },
    { id: "care_tech_help", flag: "care_tech_help", icon: Smartphone, Panel: TechHelpPanel },
    { id: "care_medicine_calls", flag: "care_medicine_calls", icon: Pill, Panel: MedicinesPanel },
    { id: "care_family_circle", flag: "care_family_circle", icon: Users, Panel: CirclePanel },
    { id: "family_view", flag: "care_family_circle", icon: HeartHandshake, Panel: FamilyPanel },
];

export function useCareParts(): Part[] {
    const flags: Record<string, boolean> = {
        care_scam_check: useFeature("care_scam_check"),
        care_tech_help: useFeature("care_tech_help"),
        care_medicine_calls: useFeature("care_medicine_calls"),
        care_family_circle: useFeature("care_family_circle"),
    };
    return PARTS.filter((part) => flags[part.flag]);
}

export function CareHub() {
    const parts = useCareParts();
    const simple = useSimpleMode();
    const params = useSearchParams();
    const router = useRouter();
    const pathname = usePathname() ?? "/care";
    const chosen = parts.find((part) => part.id === params?.get("part"));

    if (chosen) {
        const Panel = chosen.Panel;
        return (
            <div className="mx-auto flex w-full max-w-2xl flex-col gap-5 px-4 py-5 md:py-8">
                <Link
                    href={pathname}
                    className="motion-m1 inline-flex min-h-12 items-center gap-2 self-start rounded-lg px-2 text-base font-medium hover:bg-muted/50"
                    data-testid="care-back"
                >
                    <ArrowLeft aria-hidden className="h-5 w-5" />
                    Back to Care
                </Link>
                <h1 className="text-2xl font-semibold">{PART_TITLES[chosen.id]}</h1>
                <Panel />
            </div>
        );
    }

    return (
        <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-5 md:py-8" data-testid="care-hub">
            <header className="flex flex-col gap-1">
                <h1 className="text-3xl font-semibold">Care</h1>
                {CARE_POSITIONING_LINE && <p className="text-lg text-muted-foreground">{CARE_POSITIONING_LINE}</p>}
            </header>
            {parts.some((part) => part.id === "care_medicine_calls") && <DueNow />}
            <nav aria-label="Care" className="flex flex-col gap-3">
                {simple.on && (
                    <Link
                        href="/overview"
                        className="motion-m1 flex min-h-20 items-center gap-4 rounded-2xl bg-foreground px-5 py-4 text-background"
                        data-testid="care-talk"
                    >
                        <MessageCircle aria-hidden className="h-8 w-8 shrink-0" />
                        <span className="flex flex-col">
                            <span className="text-xl font-semibold">Talk to Decibyl</span>
                            <span className="text-base opacity-80">Ask anything, in your own words.</span>
                        </span>
                    </Link>
                )}
                {parts.map((part) => {
                    const Icon = part.icon;
                    return (
                        <button
                            key={part.id}
                            type="button"
                            onClick={() => router.push(`${pathname}?part=${part.id}`)}
                            className={cn(
                                "motion-m1 flex min-h-20 w-full items-center gap-4 rounded-2xl border border-border px-5 py-4 text-left hover:bg-muted/50",
                            )}
                            data-testid={`care-part-${part.id}`}
                        >
                            <Icon aria-hidden className="h-8 w-8 shrink-0" />
                            <span className="flex min-w-0 flex-col">
                                <span className="text-xl font-semibold">{PART_TITLES[part.id]}</span>
                                <span className="text-base text-muted-foreground">{PART_HINTS[part.id]}</span>
                            </span>
                        </button>
                    );
                })}
            </nav>
            <SimpleModeSwitch className="rounded-2xl border border-dashed border-border p-4" />
        </div>
    );
}

export default CareHub;
