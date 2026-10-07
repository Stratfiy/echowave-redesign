"use client";

/**
 * The detected timezone, confirmed rather than saved silently (screen 02).
 * A reminder at 9 means 9 where the person is; a guess saved in their name
 * would fire every one of them at the wrong hour.
 */

import { CheckCircle2 } from "lucide-react";
import { useId, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";

export function detectedTimezone(): string {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata";
    } catch {
        return "Asia/Kolkata";
    }
}

export function allTimezones(): string[] {
    try {
        const zones = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone");
        if (zones && zones.length) return zones;
    } catch {
        // Older engines: the detected zone and the common Indian one.
    }
    return ["Asia/Kolkata", "UTC"];
}

/** "GMT+5:30 · 3:40 PM now", so the person checks against their clock. */
export function describeTimezone(zone: string, now: Date = new Date()): string {
    try {
        const parts = new Intl.DateTimeFormat("en-IN", { timeZone: zone, timeZoneName: "shortOffset" }).formatToParts(now);
        const offset = parts.find((part) => part.type === "timeZoneName")?.value ?? "";
        const clock = now.toLocaleTimeString(undefined, { timeZone: zone, hour: "numeric", minute: "2-digit" });
        return `${offset} · ${clock} now`;
    } catch {
        return zone;
    }
}

export function TimezoneConfirm({
    value,
    confirmed,
    onChange,
}: {
    value: string;
    confirmed: boolean;
    onChange: (zone: string, confirmed: boolean) => void;
}) {
    const [changing, setChanging] = useState(false);
    const [filter, setFilter] = useState("");
    const selectId = useId();
    const zones = useMemo(() => allTimezones(), []);
    const shown = useMemo(() => {
        const q = filter.trim().toLowerCase().replace(/\s+/g, "_");
        const list = q ? zones.filter((zone) => zone.toLowerCase().includes(q)) : zones;
        return list.includes(value) ? list : [value, ...list];
    }, [zones, filter, value]);

    return (
        <div className="flex flex-col gap-3" data-testid="timezone-confirm" data-confirmed={confirmed}>
            <p className="text-base">
                <span className="font-medium">{value.replaceAll("_", " ")}</span>
                <span className="ml-2 text-sm text-muted-foreground">{describeTimezone(value)}</span>
            </p>
            {confirmed && !changing ? (
                <p role="status" className="flex flex-wrap items-center gap-2 text-sm text-[#075A39] dark:text-emerald-300">
                    <CheckCircle2 aria-hidden className="h-4 w-4" />
                    Confirmed
                    <Button type="button" variant="ghost" size="sm" className="motion-m1 min-h-11 md:min-h-8" onClick={() => setChanging(true)}>
                        Change
                    </Button>
                </p>
            ) : changing ? (
                <div className="flex flex-col gap-2">
                    <label htmlFor={`${selectId}-filter`} className="text-sm font-medium">
                        Find your timezone
                    </label>
                    <input
                        id={`${selectId}-filter`}
                        type="search"
                        value={filter}
                        placeholder="City, for example Kolkata or Dubai"
                        onChange={(event) => setFilter(event.target.value)}
                        className="min-h-11 rounded-[var(--radius-control)] border border-input bg-background px-3 text-base"
                    />
                    <label htmlFor={selectId} className="sr-only">
                        Timezone
                    </label>
                    <select
                        id={selectId}
                        value={value}
                        onChange={(event) => onChange(event.target.value, false)}
                        className="min-h-11 rounded-[var(--radius-control)] border border-input bg-background px-3 text-base"
                    >
                        {shown.map((zone) => (
                            <option key={zone} value={zone}>
                                {zone.replaceAll("_", " ")}
                            </option>
                        ))}
                    </select>
                    <Button
                        type="button"
                        className="motion-m1 min-h-11 self-start"
                        onClick={() => {
                            onChange(value, true);
                            setChanging(false);
                        }}
                    >
                        Use this timezone
                    </Button>
                </div>
            ) : (
                <div className="flex flex-wrap gap-2">
                    <Button type="button" className="motion-m1 min-h-11" onClick={() => onChange(value, true)}>
                        Yes, that is right
                    </Button>
                    <Button type="button" variant="outline" className="motion-m1 min-h-11" onClick={() => setChanging(true)}>
                        Change
                    </Button>
                </div>
            )}
        </div>
    );
}

export default TimezoneConfirm;
