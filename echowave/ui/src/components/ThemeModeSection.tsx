"use client";

/**
 * Light, dark, or follow the system -- the choice Buzz puts at the top of its
 * Appearance settings, as three small previews of the app rather than a
 * toggle whose icon has to be decoded.
 *
 * The palettes are Catppuccin Latte and Macchiato (globals.css), the pair
 * Buzz ships. Saved on this device by next-themes under its own key, so an
 * old "theme" value from the retired sidebar toggle is never replayed.
 */

import { Check, Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

export type ThemeMode = "light" | "dark" | "system";

const MODES: { id: ThemeMode; label: string; icon: typeof Sun }[] = [
    { id: "light", label: "Light", icon: Sun },
    { id: "dark", label: "Dark", icon: Moon },
    { id: "system", label: "System", icon: Monitor },
];

/** A thumbnail of the app: a frame, a panel, two lines and a button. */
function Preview({ dark }: { dark: boolean }) {
    const frame = dark ? "#1e2030" : "#e6e6b6";
    const panel = dark ? "#24273a" : "#eff1f5";
    const line = dark ? "#494d64" : "#ccd0da";
    const ink = dark ? "#cad3f5" : "#4c4f69";
    return (
        <span className="flex h-full w-full overflow-hidden" style={{ background: frame }} aria-hidden>
            <span className="w-1/4" />
            <span className="m-1.5 ml-0 flex flex-1 flex-col gap-1 rounded-md p-1.5" style={{ background: panel }}>
                <span className="h-1.5 w-2/3 rounded-full" style={{ background: ink }} />
                <span className="h-1 w-full rounded-full" style={{ background: line }} />
                <span className="h-1 w-5/6 rounded-full" style={{ background: line }} />
                <span className="mt-auto h-2.5 w-1/3 self-end rounded" style={{ background: ink }} />
            </span>
        </span>
    );
}

export function ThemeModeSection() {
    const { theme, setTheme } = useTheme();
    // next-themes knows the stored choice only after mount; before that no
    // option is marked, rather than the wrong one.
    const [mounted, setMounted] = useState(false);
    useEffect(() => setMounted(true), []);
    const current = mounted ? ((theme as ThemeMode | undefined) ?? "light") : null;

    return (
        <div role="radiogroup" aria-label="Appearance" className="grid grid-cols-3 gap-3">
            {MODES.map(({ id, label, icon: Icon }) => {
                const selected = current === id;
                return (
                    <button
                        key={id}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        onClick={() => setTheme(id)}
                        className={cn(
                            "group flex flex-col gap-2 rounded-lg border p-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            selected ? "border-[var(--accent-brand)] ring-1 ring-[var(--accent-brand)]" : "border-border hover:bg-muted/50",
                        )}
                    >
                        <span className="relative block h-16 overflow-hidden rounded-md border border-border">
                            {id === "system" ? (
                                <span className="flex h-full">
                                    <span className="w-1/2 overflow-hidden">
                                        <Preview dark={false} />
                                    </span>
                                    <span className="w-1/2 overflow-hidden">
                                        <Preview dark />
                                    </span>
                                </span>
                            ) : (
                                <Preview dark={id === "dark"} />
                            )}
                        </span>
                        <span className="flex items-center gap-1.5 font-medium">
                            <Icon className="h-3.5 w-3.5" aria-hidden />
                            {label}
                            {selected && <Check className="ml-auto h-3.5 w-3.5 text-[var(--brand-blue)]" aria-hidden />}
                        </span>
                    </button>
                );
            })}
        </div>
    );
}
