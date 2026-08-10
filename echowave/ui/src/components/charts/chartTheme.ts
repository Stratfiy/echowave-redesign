/**
 * Chart palette and chrome for every dashboard in the app.
 *
 * The categorical slots are assigned in fixed order and never cycled. Both
 * modes are selected sets validated against this app's real surfaces (light
 * `#ffffff`, dark `#0e1a23` — the shadcn `--card` values), not against a
 * generic default:
 *
 *   light  worst adjacent CVD ΔE 9.1 · normal-vision ΔE 19.6
 *   dark   worst adjacent CVD ΔE 8.4 · normal-vision ΔE 19.3
 *
 * On the light surface aqua, yellow and magenta fall below 3:1 contrast, so the
 * relief rule applies: every chart using them ships a legend and a hover
 * tooltip carrying the values, so identity is never colour alone.
 */

export type Mode = "light" | "dark";

/** Fixed categorical order. Slot N is always the same hue. */
export const SERIES_LIGHT = [
    "#2a78d6", // 1 blue
    "#eb6834", // 2 orange
    "#1baf7a", // 3 aqua
    "#eda100", // 4 yellow
    "#e87ba4", // 5 magenta
    "#008300", // 6 green
    "#4a3aa7", // 7 violet
    "#e34948", // 8 red
] as const;

export const SERIES_DARK = [
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#008300",
    "#9085e9",
    "#e66767",
] as const;

/**
 * Cost components in receipt order: the four pass-through costs, then our
 * platform fee last so the stack reads cost-then-margin bottom-to-top.
 * Colour follows the component, never its rank, so filtering never repaints.
 */
export const COST_COMPONENTS = [
    { key: "stt", label: "STT", slot: 0 },
    { key: "llm", label: "LLM", slot: 1 },
    { key: "tts", label: "TTS", slot: 2 },
    { key: "telephony", label: "Telephony", slot: 3 },
    { key: "platform", label: "Platform fee", slot: 4 },
] as const;

/** Status colours are reserved and never reused as a series colour. */
export const STATUS = {
    good: "#0ca30c",
    warning: "#fab219",
    serious: "#ec835a",
    critical: "#d03b3b",
} as const;

export const CHROME = {
    light: {
        grid: "#e1e0d9",
        axis: "#c3c2b7",
        muted: "#898781",
        surface: "#ffffff",
    },
    dark: {
        grid: "#2c2c2a",
        axis: "#383835",
        muted: "#898781",
        surface: "#0e1a23",
    },
} as const;

export function seriesColor(slot: number, mode: Mode): string {
    const palette = mode === "dark" ? SERIES_DARK : SERIES_LIGHT;
    // Never generate a hue for an overflow slot — wrap to the documented order
    // only as a last resort; charts here never exceed five series.
    return palette[slot % palette.length];
}

/** The latency target the team is optimising towards. */
export const LATENCY_TARGET_MS = 800;
