"use client";

/**
 * When a routine runs: how often, against what, and at what time.
 *
 * The same four controls wherever a schedule is set -- the bot's own panel,
 * where a routine is created, and the Tasks board, where somebody who can
 * see every schedule at once wants to move one. Shared rather than copied,
 * because two spellings of "Monday to Friday" is a screen that disagrees
 * with the screen next to it about what a routine does.
 *
 * ``idPrefix`` exists because two of these can be open on one page: the
 * labels have to point at their own inputs or clicking one focuses the
 * other.
 */

import type { Anchor, Cadence } from "@/client/types.gen";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ANCHORS, CADENCES, DAYS } from "@/lib/schedule";

export type ScheduleValue = {
    cadence: Cadence;
    anchor: Anchor;
    /** "HH:MM", as a time input gives it. */
    time: string;
    weekday: number;
};

export function ScheduleFields({
    value,
    onChange,
    idPrefix = "schedule",
}: {
    value: ScheduleValue;
    onChange: (next: ScheduleValue) => void;
    idPrefix?: string;
}) {
    const set = (patch: Partial<ScheduleValue>) => onChange({ ...value, ...patch });

    return (
        <div className="grid gap-3 sm:grid-cols-3">
            <div>
                <Label htmlFor={`${idPrefix}-cadence`}>How often</Label>
                <select
                    id={`${idPrefix}-cadence`}
                    className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                    value={value.cadence}
                    onChange={(e) => set({ cadence: e.target.value as Cadence })}
                >
                    {CADENCES.map((c) => (
                        <option key={c.value} value={c.value}>
                            {c.label}
                        </option>
                    ))}
                </select>
            </div>
            <div>
                <Label htmlFor={`${idPrefix}-anchor`}>When</Label>
                <select
                    id={`${idPrefix}-anchor`}
                    className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                    value={value.anchor}
                    onChange={(e) => set({ anchor: e.target.value as Anchor })}
                >
                    {ANCHORS.map((a) => (
                        <option key={a.value} value={a.value}>
                            {a.label}
                        </option>
                    ))}
                </select>
            </div>
            {value.anchor === "clock" ? (
                <div>
                    <Label htmlFor={`${idPrefix}-time`}>Time</Label>
                    <Input
                        id={`${idPrefix}-time`}
                        type="time"
                        className="mt-1"
                        value={value.time}
                        onChange={(e) => set({ time: e.target.value })}
                    />
                </div>
            ) : null}
            {value.cadence === "weekly" ? (
                <div>
                    <Label htmlFor={`${idPrefix}-weekday`}>Day</Label>
                    <select
                        id={`${idPrefix}-weekday`}
                        className="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                        value={value.weekday}
                        onChange={(e) =>
                            set({ weekday: Number.parseInt(e.target.value, 10) })
                        }
                    >
                        {DAYS.map((d, i) => (
                            <option key={d} value={i}>
                                {d}
                            </option>
                        ))}
                    </select>
                </div>
            ) : null}
        </div>
    );
}
