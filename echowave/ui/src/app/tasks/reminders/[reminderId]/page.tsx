"use client";

import { useParams } from "next/navigation";

import { NotOn } from "@/components/today/NotOn";
import { ReminderEditor } from "@/components/today/ReminderEditor";
import { useFeature } from "@/lib/features";

/** Screen 10: one reminder, to change, pause, test or cancel. */
export default function ReminderPage() {
    const { reminderId } = useParams<{ reminderId: string }>();
    if (!useFeature("today_reminders")) return <NotOn what="Reminders" />;
    return <ReminderEditor reminderId={Number(reminderId)} />;
}
