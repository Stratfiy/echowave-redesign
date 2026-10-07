"use client";

import { NotOn } from "@/components/today/NotOn";
import { ReminderEditor } from "@/components/today/ReminderEditor";
import { useFeature } from "@/lib/features";

/** Screen 10: a new reminder, or an event with its reminders. */
export default function NewReminderPage() {
    if (!useFeature("today_reminders")) return <NotOn what="Reminders" />;
    return <ReminderEditor />;
}
