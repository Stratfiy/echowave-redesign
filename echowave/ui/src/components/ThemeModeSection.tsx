"use client";

/**
 * Settings → Appearance, the way Buzz lays it out: first Light / Dark /
 * System, then the theme -- each tile a small preview of the app in that
 * theme's own colours, light and dark side by side. The default is Buzz's
 * default, neutral black and white; there is no accent colour to pick.
 */

import { Check, Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { applyTheme, readStoredTheme, type Theme, THEMES, themeTokens } from "@/lib/themes";
import { cn } from "@/lib/utils";

export type ThemeMode = "light" | "dark" | "system";

const MODES: { id: ThemeMode; label: string; icon: typeof Sun }[] = [
    { id: "light", label: "Light", icon: Sun },
    { id: "dark", label: "Dark", icon: Moon },
    { id: "system", label: "System", icon: Monitor },
];

/** A thumbnail of the app in one side of a theme: frame, panel, text, button. */
function Preview({ name, gradient }: { name: string; gradient?: boolean }) {
    const t = themeTokens(name, gradient);
    const frame = gradient ? `linear-gradient(to bottom, ${t["--shell-gradient-top"]}, ${t["--shell-gradient-bottom"]})` : t["--shell-gradient-top"];
    return (
        <span className="flex h-full w-full overflow-hidden" style={{ background: frame }} aria-hidden>
            <span className="flex w-1/4 flex-col gap-1 p-1.5">
                <span className="h-1 w-3/4 rounded-full" style={{ background: t["--sidebar-foreground"], opacity: 0.6 }} />
                <span className="h-1 w-1/2 rounded-full" style={{ background: t["--sidebar-foreground"], opacity: 0.4 }} />
            </span>
            <span className="m-1.5 ml-0 flex flex-1 flex-col gap-1 rounded-md p-1.5" style={{ background: t["--background"] }}>
                <span className="h-1.5 w-2/3 rounded-full" style={{ background: t["--foreground"] }} />
                <span className="h-1 w-full rounded-full" style={{ background: t["--border"] }} />
                <span className="h-1 w-5/6 rounded-full" style={{ background: t["--border"] }} />
                <span className="mt-auto h-2.5 w-1/3 self-end rounded" style={{ background: t["--primary"] }} />
            </span>
        </span>
    );
}

function Tile({ selected, onClick, label, children }: { selected: boolean; onClick: () => void; label: React.ReactNode; children: React.ReactNode }) {
    return (
        <button
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={onClick}
            className={cn(
                "flex flex-col gap-2 rounded-lg border p-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                selected ? "border-foreground ring-1 ring-foreground" : "border-border hover:bg-muted/50",
            )}
        >
            <span className="relative block h-16 overflow-hidden rounded-md border border-border">{children}</span>
            <span className="flex items-center gap-1.5 font-medium">
                {label}
                {selected && <Check className="ml-auto h-3.5 w-3.5" aria-hidden />}
            </span>
        </button>
    );
}

export function ThemeModeSection() {
    const { theme: mode, setTheme: setMode } = useTheme();
    // Both choices are known only after mount (next-themes, localStorage);
    // before that nothing is marked rather than the wrong thing.
    const [mounted, setMounted] = useState(false);
    const [theme, setTheme] = useState<Theme | null>(null);
    useEffect(() => {
        setMounted(true);
        setTheme(readStoredTheme());
    }, []);
    const currentMode = mounted ? ((mode as ThemeMode | undefined) ?? "light") : null;
    const dark = currentMode === "dark";

    const choose = (next: Theme) => {
        setTheme(next);
        applyTheme(next);
    };

    return (
        <div className="space-y-5">
            <div role="radiogroup" aria-label="Mode" className="grid grid-cols-3 gap-3">
                {MODES.map(({ id, label, icon: Icon }) => (
                    <Tile
                        key={id}
                        selected={currentMode === id}
                        onClick={() => setMode(id)}
                        label={
                            <>
                                <Icon className="h-3.5 w-3.5" aria-hidden />
                                {label}
                            </>
                        }
                    >
                        {id === "system" ? (
                            <span className="flex h-full">
                                <span className="w-1/2 overflow-hidden">
                                    <Preview name={(theme ?? THEMES[0]).light} gradient={(theme ?? THEMES[0]).gradient} />
                                </span>
                                <span className="w-1/2 overflow-hidden">
                                    <Preview name={(theme ?? THEMES[0]).dark} gradient={(theme ?? THEMES[0]).gradient} />
                                </span>
                            </span>
                        ) : (
                            <Preview name={id === "dark" ? (theme ?? THEMES[0]).dark : (theme ?? THEMES[0]).light} gradient={(theme ?? THEMES[0]).gradient} />
                        )}
                    </Tile>
                ))}
            </div>

            <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Theme</p>
                <div role="radiogroup" aria-label="Theme" className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                    {THEMES.map((option) => (
                        <Tile key={option.id} selected={theme?.id === option.id} onClick={() => choose(option)} label={option.label}>
                            <Preview name={dark ? option.dark : option.light} gradient={option.gradient} />
                        </Tile>
                    ))}
                </div>
            </div>
        </div>
    );
}
