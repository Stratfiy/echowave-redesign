/**
 * The app's themes, the way Buzz does them.
 *
 * Buzz (block/buzz, Apache-2.0) derives every surface colour of its UI from
 * three colours of a syntax theme -- background, text, comment -- so a theme
 * is three hex values and one function, not a hand-kept palette of sixty
 * tokens that drift apart. `themeTokens` is a port of Buzz's
 * `createThemeVars` and its chrome-colour maths (desktop/src/shared/theme/
 * adaptive-theme.ts at ec7ea38), emitting this app's own token names.
 *
 * The default is Buzz's own default: GitHub Light and GitHub Dark with a
 * neutral accent -- ink in light, white in dark -- and the one branded touch
 * Buzz allows itself, a soft gradient across the sidebar. No purple, and no
 * accent colour to choose while it is on. The other themes are the paired
 * light/dark themes Buzz's picker offers, colours read from the same Shiki
 * theme files it reads.
 *
 * The default's values also stand in globals.css (so the first paint needs no
 * script); `themes.test.ts` fails if the two disagree.
 */

export type ThemeColors = { bg: string; fg: string; comment: string; added: string | null; deleted: string | null };

/** Background, text, comment, and the git colours where the theme has them. */
export const THEME_COLORS: Record<string, ThemeColors> = {
    "catppuccin-latte": { bg: "#eff1f5", fg: "#4c4f69", comment: "#7c7f93", added: "#40a02b", deleted: "#d20f39" },
    "catppuccin-mocha": { bg: "#1e1e2e", fg: "#cdd6f4", comment: "#9399b2", added: "#a6e3a1", deleted: "#f38ba8" },
    "everforest-light": { bg: "#fdf6e3", fg: "#5c6a72", comment: "#939f91", added: "#8da101", deleted: "#f85552" },
    "everforest-dark": { bg: "#2d353b", fg: "#d3c6aa", comment: "#859289", added: "#a7c080", deleted: "#e67e80" },
    "github-light": { bg: "#ffffff", fg: "#24292e", comment: "#6a737d", added: "#28a745", deleted: "#d73a49" },
    "github-dark": { bg: "#24292e", fg: "#e1e4e8", comment: "#6a737d", added: "#34d058", deleted: "#ea4a5a" },
    "github-light-high-contrast": { bg: "#ffffff", fg: "#0e1116", comment: "#66707b", added: "#055d20", deleted: "#a0111f" },
    "github-dark-high-contrast": { bg: "#0a0c10", fg: "#f0f3f6", comment: "#bdc4cc", added: "#26cd4d", deleted: "#ff6a69" },
    "gruvbox-light-medium": { bg: "#fbf1c7", fg: "#3c3836", comment: "#928374", added: "#3c3836", deleted: "#cc241d" },
    "gruvbox-dark-medium": { bg: "#282828", fg: "#ebdbb2", comment: "#928374", added: "#ebdbb2", deleted: "#cc241d" },
    "kanagawa-lotus": { bg: "#f2ecbc", fg: "#545464", comment: "#716e61", added: null, deleted: null },
    "kanagawa-wave": { bg: "#1f1f28", fg: "#dcd7ba", comment: "#727169", added: null, deleted: null },
    "min-light": { bg: "#ffffff", fg: "#212121", comment: "#c2c3c5", added: null, deleted: null },
    "min-dark": { bg: "#1f1f1f", fg: "#d4d4d4", comment: "#6b737c", added: null, deleted: null },
    "one-light": { bg: "#fafafa", fg: "#383a42", comment: "#a0a1a7", added: null, deleted: null },
    "one-dark-pro": { bg: "#282c34", fg: "#abb2bf", comment: "#5c6370", added: null, deleted: null },
    "rose-pine-dawn": { bg: "#faf4ed", fg: "#575279", comment: "#9893a5", added: "#56949f", deleted: "#797593" },
    "rose-pine": { bg: "#191724", fg: "#e0def4", comment: "#6e6a86", added: "#9ccfd8", deleted: "#908caa" },
    "slack-ochin": { bg: "#ffffff", fg: "#000000", comment: "#357b42", added: "#ecb22e", deleted: "#ffffff" },
    "slack-dark": { bg: "#222222", fg: "#e6e6e6", comment: "#6a9955", added: "#ecb22e", deleted: "#ffffff" },
    "solarized-light": { bg: "#fdf6e3", fg: "#657b83", comment: "#93a1a1", added: null, deleted: null },
    "solarized-dark": { bg: "#002b36", fg: "#839496", comment: "#586e75", added: null, deleted: null },
    "vitesse-light": { bg: "#ffffff", fg: "#393a34", comment: "#a0ada0", added: "#1e754f", deleted: "#ab5959" },
    "vitesse-dark": { bg: "#121212", fg: "#dbd7ca", comment: "#758575", added: "#4d9375", deleted: "#cb7676" },
    "material-theme-lighter": { bg: "#fafafa", fg: "#90a4ae", comment: "#90a4ae", added: null, deleted: "#e53935" },
    "material-theme": { bg: "#263238", fg: "#eeffff", comment: "#546e7a", added: null, deleted: "#f07178" },
    "light-plus": { bg: "#ffffff", fg: "#000000", comment: "#008000", added: null, deleted: null },
    "dark-plus": { bg: "#1e1e1e", fg: "#d4d4d4", comment: "#6a9955", added: null, deleted: null },
};

export type Theme = {
    id: string;
    label: string;
    light: string;
    dark: string;
    /** Buzz's sidebar gradient; only the default carries it. */
    gradient?: boolean;
};

/** Paired light/dark, as Buzz's THEME_PAIRS; the default first. */
export const THEMES: Theme[] = [
    { id: "default", label: "Default", light: "github-light", dark: "github-dark", gradient: true },
    { id: "github", label: "GitHub", light: "github-light", dark: "github-dark" },
    { id: "github-contrast", label: "GitHub high contrast", light: "github-light-high-contrast", dark: "github-dark-high-contrast" },
    { id: "one", label: "One", light: "one-light", dark: "one-dark-pro" },
    { id: "vscode", label: "VS Code", light: "light-plus", dark: "dark-plus" },
    { id: "min", label: "Min", light: "min-light", dark: "min-dark" },
    { id: "slack", label: "Slack", light: "slack-ochin", dark: "slack-dark" },
    { id: "solarized", label: "Solarized", light: "solarized-light", dark: "solarized-dark" },
    { id: "gruvbox", label: "Gruvbox", light: "gruvbox-light-medium", dark: "gruvbox-dark-medium" },
    { id: "everforest", label: "Everforest", light: "everforest-light", dark: "everforest-dark" },
    { id: "rose-pine", label: "Rosé Pine", light: "rose-pine-dawn", dark: "rose-pine" },
    { id: "catppuccin", label: "Catppuccin", light: "catppuccin-latte", dark: "catppuccin-mocha" },
    { id: "kanagawa", label: "Kanagawa", light: "kanagawa-lotus", dark: "kanagawa-wave" },
    { id: "material", label: "Material", light: "material-theme-lighter", dark: "material-theme" },
    { id: "vitesse", label: "Vitesse", light: "vitesse-light", dark: "vitesse-dark" },
];

export const DEFAULT_THEME_ID = "default";

/** Buzz's sidebar gradient, light and dark (theme.css). */
export const BUZZ_GRADIENT = {
    light: { top: "#e6e6b6", bottom: "#c4d0da" },
    dark: { top: "#4a4616", bottom: "#0a1423" },
};

// --- colour maths, ported from Buzz ------------------------------------------

type Rgb = { r: number; g: number; b: number };

function hexToRgb(hex: string): Rgb {
    const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})/i.exec(hex);
    if (!m) return { r: 0, g: 0, b: 0 };
    return { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) };
}

function rgbToHex({ r, g, b }: Rgb): string {
    const c = (v: number) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0");
    return `#${c(r)}${c(g)}${c(b)}`;
}

export function luminance(hex: string): number {
    const { r, g, b } = hexToRgb(hex);
    const [rs, gs, bs] = [r, g, b].map((v) => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
}

export function mix(a: string, b: string, factor: number): string {
    const c1 = hexToRgb(a);
    const c2 = hexToRgb(b);
    return rgbToHex({ r: c1.r + (c2.r - c1.r) * factor, g: c1.g + (c2.g - c1.g) * factor, b: c1.b + (c2.b - c1.b) * factor });
}

function adjust(hex: string, amount: number): string {
    return mix(hex, amount > 0 ? "#ffffff" : "#000000", Math.abs(amount));
}

function overlay(hex: string, alpha: number): string {
    const { r, g, b } = hexToRgb(hex);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

const CONTRAST_VALUE = 0.035;
const CONTRAST_OFFSET = 0.0135;

function findColorWithLuminance(base: string, target: number): string {
    const baseLum = luminance(base);
    if (Math.abs(baseLum - target) < 0.001) return base;
    const toward = target < baseLum ? "#000000" : "#ffffff";
    let lo = 0;
    let hi = 1;
    for (let i = 0; i < 20; i++) {
        const mid = (lo + hi) / 2;
        const test = luminance(mix(base, toward, mid));
        if (Math.abs(test - target) < 0.001) break;
        if (toward === "#000000") {
            if (test > target) lo = mid;
            else hi = mid;
        } else if (test < target) lo = mid;
        else hi = mid;
    }
    return mix(base, toward, (lo + hi) / 2);
}

function contrast(a: string, b: string): number {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
}

/** Muted text: the theme's comment colour, moved toward its text colour
 *  just until it reads at 4.5:1 -- some themes' comments are too faint to
 *  set a paragraph in. */
function readable(muted: string, fg: string, bg: string, from = 0.25): string {
    for (let step = from; step < 1; step += 0.05) {
        const candidate = mix(muted, fg, step);
        if (contrast(candidate, bg) >= 4.5) return candidate;
    }
    return fg;
}

/** The chrome (sidebar) colour one step below the page, and the page itself. */
function chromeColors(bg: string): { chrome: string; primary: string } {
    const bgLum = luminance(bg);
    const diff = CONTRAST_VALUE * Math.log(1 + (bgLum + CONTRAST_OFFSET) * 10);
    const target = bgLum - diff;
    if (target >= 0) return { chrome: findColorWithLuminance(bg, target), primary: bg };
    return { chrome: findColorWithLuminance(bg, 0), primary: findColorWithLuminance(bg, diff) };
}

// --- tokens ------------------------------------------------------------------

/**
 * Every colour token this app reads, from one side of a theme.
 *
 * The accent is neutral, as in Buzz's own themes: the primary button, the
 * focus ring and the selected row are the text colour, and links are the
 * text colour too (underlined where they are links). Colour is kept for what
 * colour means: red for destructive, green for success, amber for warning.
 */
export function themeTokens(name: string, gradient = false): Record<string, string> {
    const c = THEME_COLORS[name];
    const isDark = luminance(c.bg) < 0.5;
    const { chrome, primary: bg } = chromeColors(c.bg);
    // A theme's own text colour, nudged only if it misses 4.5:1 (Solarized
    // Light's does, at 4.1).
    const fg = readable(c.fg, isDark ? "#ffffff" : "#000000", bg, 0);
    const dir = isDark ? 1 : -1;
    const elevate = (amount: number) => adjust(bg, dir * amount);
    const hover = elevate(0.06);
    const border = mix(bg, fg, isDark ? 0.15 : 0.12);
    const muted = readable(c.comment, fg, bg);
    const green = c.added ?? (isDark ? "#3fb950" : "#1a7f37");
    const red = c.deleted ?? (isDark ? "#f85149" : "#cf222e");
    const orange = isDark ? "#d29922" : "#9a6700";
    const shell = gradient ? BUZZ_GRADIENT[isDark ? "dark" : "light"] : { top: chrome, bottom: chrome };
    const ink = gradient && !isDark ? "#24292e" : fg;

    return {
        "--background": bg,
        "--foreground": fg,
        "--card": bg,
        "--card-foreground": fg,
        "--popover": elevate(0.04),
        "--popover-foreground": fg,
        "--primary": fg,
        "--primary-foreground": bg,
        "--primary-pressed": mix(fg, bg, 0.2),
        "--secondary": hover,
        "--secondary-foreground": fg,
        "--muted": hover,
        "--muted-foreground": muted,
        "--muted-foreground-on-paper": muted,
        "--accent": hover,
        "--accent-foreground": fg,
        "--destructive": red,
        "--destructive-foreground": bg,
        "--warning": orange,
        "--success": green,
        "--border": border,
        "--input": border,
        "--ring": fg,
        "--accent-brand": fg,
        "--accent-brand-soft": overlay(fg, 0.08),
        "--accent-brand-tint": hover,
        "--brand-blue": fg,
        "--brand-blue-hover": mix(fg, bg, 0.25),
        "--brand-blue-soft": overlay(fg, 0.08),
        "--brand-blue-glow": overlay(fg, 0.12),
        "--brand-panel": bg,
        "--brand-heading": fg,
        "--brand-body": muted,
        "--brand-chip": hover,
        "--brand-chip-border": border,
        "--brand-chip-fg": fg,
        "--brand-card": bg,
        "--cta": fg,
        "--cta-foreground": bg,
        "--tint-flame": mix(bg, red, 0.12),
        "--tint-amber": mix(bg, orange, 0.14),
        "--tint-sand": hover,
        "--tint-slate": hover,
        "--color-soft-mint": mix(bg, green, 0.14),
        "--shell-gradient-top": shell.top,
        "--shell-gradient-bottom": shell.bottom,
        "--shell-ink": ink,
        "--rail-foreground": ink,
        "--rail-accent": overlay(ink, isDark ? 0.14 : 0.08),
        "--rail-accent-foreground": ink,
        "--rail-border": overlay(ink, 0.12),
        "--sidebar-foreground": ink,
        "--sidebar-primary": ink,
        "--sidebar-primary-foreground": isDark ? chrome : "#ffffff",
        "--sidebar-accent": gradient ? (isDark ? "rgba(255, 255, 255, 0.16)" : "rgba(255, 255, 255, 0.6)") : overlay(fg, 0.08),
        "--sidebar-accent-foreground": ink,
        "--sidebar-border": overlay(ink, 0.12),
        "--sidebar-ring": ink,
        "--glass-fill": overlay(bg, 0.72),
        "--glass-fill-strong": bg,
        "--glass-edge": border,
        "--glass-accent-fill": fg,
        "--glass-accent-fg": bg,
        "--glass-amber-fill": mix(bg, orange, 0.14),
        "--glass-amber-edge": mix(bg, orange, 0.3),
        "--glass-amber-fg": isDark ? mix(orange, "#ffffff", 0.3) : mix(orange, "#000000", 0.25),
    };
}

export function themeById(id: string | null | undefined): Theme {
    return THEMES.find((t) => t.id === id) ?? THEMES[0];
}

function declarations(vars: Record<string, string>): string {
    return Object.entries(vars)
        .map(([k, v]) => `${k}:${v}`)
        .join(";");
}

/** A theme as a stylesheet: one rule for light, one for dark. The default
 *  needs none -- globals.css is it. */
export function themeCss(theme: Theme): string {
    if (theme.id === DEFAULT_THEME_ID) return "";
    return `:root:not(.dark){${declarations(themeTokens(theme.light, theme.gradient))}}:root.dark{${declarations(themeTokens(theme.dark, theme.gradient))}}`;
}

export const THEME_STORAGE_KEY = "decibyl.palette";
export const THEME_STYLE_ID = "decibyl-theme";

/** What older builds wrote for the accent colour; cleared wherever a theme is
 *  applied, so a stored purple never comes back. */
export const LEGACY_ACCENT_KEY = "decibyl.accent";
const LEGACY_ACCENT_STYLE_ID = "decibyl-accent";
const LEGACY_INLINE = [
    "--sidebar-primary", "--sidebar-primary-foreground", "--accent-brand", "--accent-brand-soft",
    "--accent-brand-tint", "--ring", "--sidebar-ring", "--brand-blue", "--brand-blue-hover",
    "--brand-blue-soft", "--brand-blue-glow", "--rail", "--rail-foreground", "--rail-accent",
    "--rail-accent-foreground", "--rail-border", "--sidebar", "--sidebar-foreground",
    "--sidebar-accent", "--sidebar-accent-foreground", "--sidebar-border",
];

function clearLegacyAccent(): void {
    const style = document.documentElement.style;
    for (const name of LEGACY_INLINE) style.removeProperty(name);
    document.getElementById(LEGACY_ACCENT_STYLE_ID)?.remove();
    try {
        window.localStorage.removeItem(LEGACY_ACCENT_KEY);
    } catch {
        /* storage blocked: nothing stored either */
    }
}

export function readStoredTheme(): Theme {
    try {
        return themeById(window.localStorage.getItem(THEME_STORAGE_KEY));
    } catch {
        return themeById(null);
    }
}

/** Put a theme on the page and remember it on this device. */
export function applyTheme(theme: Theme, { store = true }: { store?: boolean } = {}): void {
    if (typeof document === "undefined") return;
    clearLegacyAccent();
    const css = themeCss(theme);
    let sheet = document.getElementById(THEME_STYLE_ID);
    if (!css) sheet?.remove();
    else {
        if (!sheet) {
            sheet = document.createElement("style");
            sheet.id = THEME_STYLE_ID;
            document.head.appendChild(sheet);
        }
        sheet.textContent = css;
    }
    if (!store) return;
    try {
        if (theme.id === DEFAULT_THEME_ID) window.localStorage.removeItem(THEME_STORAGE_KEY);
        else window.localStorage.setItem(THEME_STORAGE_KEY, theme.id);
    } catch {
        /* a preference, not worth failing over */
    }
}

/**
 * Before first paint: drop what an older build stored for the accent (the
 * purple), and put back a chosen theme. The stylesheet itself is computed at
 * render time and inlined per theme, so the script needs no colour maths.
 */
export const THEME_BOOT_SCRIPT = `(function(){try{
var s=document.documentElement.style;var dead=${JSON.stringify(LEGACY_INLINE)};
for(var i=0;i<dead.length;i++)s.removeProperty(dead[i]);
localStorage.removeItem(${JSON.stringify(LEGACY_ACCENT_KEY)});
var id=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});
var css=${JSON.stringify(Object.fromEntries(THEMES.filter((t) => t.id !== DEFAULT_THEME_ID).map((t) => [t.id, themeCss(t)])))}[id];
if(!css)return;
var el=document.createElement("style");el.id=${JSON.stringify(THEME_STYLE_ID)};el.textContent=css;document.head.appendChild(el);
}catch(e){}})();`;
