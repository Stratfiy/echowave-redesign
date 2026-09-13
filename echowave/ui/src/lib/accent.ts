/**
 * The accent colour, chosen by the person using the app.
 *
 * One accent is really *two* colours, and that is not a stylistic choice — it
 * is what globals.css already discovered the hard way. The comment above
 * `--brand-blue` records it: the bright brand coral gives 2.86:1 on white, so
 * using it for link text and button fills "would have shipped three
 * inaccessible buttons and two unreadable links". The fix was a second, deeper
 * token for anything that has to be *read*, leaving the bright one for fills,
 * rings and active states where nothing is read off it.
 *
 * So an accent here is a pair:
 *
 *   bright — fills, focus rings, active states, tints. Needs 3:1 on white
 *            (WCAG 1.4.11, non-text UI), never carries text.
 *   deep   — link text, and any fill carrying white text. Needs 4.5:1.
 *
 * A single-value picker would let someone choose pale yellow and silently
 * destroy every focus ring in the product — the "silent absence" shape the
 * API's notes warn about, except here what goes missing is the outline a
 * keyboard user navigates by. Hence `contrastOnWhite`, and hence a custom
 * colour being *checked* rather than trusted.
 *
 * Stored per device in localStorage, matching how the sidebar remembers its
 * collapse state. There is no account-level accent yet; when there is, this
 * stays as the fallback for a signed-out visit.
 */

export const ACCENT_STORAGE_KEY = "decibyl.accent";

export interface Accent {
  /** Stable id — what goes in localStorage. Never a label. */
  id: string;
  label: string;
  /** Fills, rings, active states. Never carries text. */
  bright: string;
  /** Link text, and fills carrying white text. */
  deep: string;
}

/**
 * The curated set. Every pair below was checked with `contrastOnWhite` before
 * it was added: bright >= 3, deep >= 4.5. `api/tests` has no say over this
 * file, so the check lives in the UI test suite instead — see accent.test.ts,
 * which re-derives both numbers rather than trusting this comment.
 *
 * Coral is first because it is what the app already ships; a settings screen
 * whose default is not the current appearance reads as broken.
 */
export const ACCENTS: readonly Accent[] = [
  { id: "coral", label: "Coral", bright: "#e15b53", deep: "#ab3f38" },
  { id: "aubergine", label: "Aubergine", bright: "#8b2fa8", deep: "#6b2280" },
  { id: "indigo", label: "Indigo", bright: "#4f46e5", deep: "#4338ca" },
  { id: "teal", label: "Teal", bright: "#0d9488", deep: "#0f766e" },
  { id: "forest", label: "Forest", bright: "#16a34a", deep: "#15803d" },
  { id: "amber", label: "Amber", bright: "#d97706", deep: "#a1560a" },
  { id: "rose", label: "Rose", bright: "#e11d48", deep: "#be123c" },
  { id: "slate", label: "Slate", bright: "#475569", deep: "#334155" },
] as const;

export const DEFAULT_ACCENT_ID = "coral";

/** The minimum a bright accent may score on white: WCAG 1.4.11, non-text UI. */
export const MIN_BRIGHT_CONTRAST = 3;
/** The minimum a deep accent may score on white: WCAG 1.4.3, body text. */
export const MIN_DEEP_CONTRAST = 4.5;

function parseHex(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6}|[0-9a-f]{3})$/i.exec(hex.trim());
  if (!m) return null;
  let body = m[1];
  if (body.length === 3) body = body.split("").map((c) => c + c).join("");
  return [
    parseInt(body.slice(0, 2), 16),
    parseInt(body.slice(2, 4), 16),
    parseInt(body.slice(4, 6), 16),
  ];
}

function channelLuminance(value: number): number {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/**
 * WCAG relative-contrast of a colour against white. Returns 1 for anything
 * unparseable — the failing answer, deliberately, so a typo in a custom colour
 * is rejected rather than waved through.
 */
export function contrastOnWhite(hex: string): number {
  const rgb = parseHex(hex);
  if (!rgb) return 1;
  const [r, g, b] = rgb;
  const l =
    0.2126 * channelLuminance(r) +
    0.7152 * channelLuminance(g) +
    0.0722 * channelLuminance(b);
  return 1.05 / (l + 0.05);
}

/**
 * Darken a colour until it clears `MIN_DEEP_CONTRAST` on white.
 *
 * This is what makes the custom picker safe rather than decorative. Someone
 * choosing a bright colour they like still gets readable link text, because
 * the deep half is derived here instead of being the same value reused. Steps
 * are small and bounded; a colour that cannot reach the threshold before black
 * returns black, which is readable if not pretty.
 */
export function deepen(hex: string): string {
  const rgb = parseHex(hex);
  if (!rgb) return "#171717";
  let [r, g, b] = rgb;
  for (let i = 0; i < 40; i += 1) {
    const candidate = `#${[r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
    if (contrastOnWhite(candidate) >= MIN_DEEP_CONTRAST) return candidate;
    r = Math.max(0, Math.round(r * 0.92));
    g = Math.max(0, Math.round(g * 0.92));
    b = Math.max(0, Math.round(b * 0.92));
  }
  return "#000000";
}

/** `rgba(r, g, b, alpha)` for a hex — the soft and glow tints are alpha, not a second hex. */
export function withAlpha(hex: string, alpha: number): string {
  const rgb = parseHex(hex);
  if (!rgb) return `rgba(23, 23, 23, ${alpha})`;
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}

/** Mix a colour toward white — the pale wash behind a badge, never a large surface. */
export function tint(hex: string, weight: number): string {
  const rgb = parseHex(hex);
  if (!rgb) return "#f5f5f5";
  const mixed = rgb.map((c) => Math.round(c + (255 - c) * weight));
  return `#${mixed.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

export function accentById(id: string | null | undefined): Accent | null {
  if (!id) return null;
  return ACCENTS.find((a) => a.id === id) ?? null;
}

/**
 * Resolve whatever is in storage to a usable pair.
 *
 * A stored value is either a known id or a literal `#rrggbb` from the custom
 * picker. Anything else — a removed preset, a half-written value, a key some
 * other tab wrote — falls back to the default rather than leaving the app
 * unstyled.
 */
export function resolveAccent(stored: string | null | undefined): Accent {
  const preset = accentById(stored);
  if (preset) return preset;
  if (stored && parseHex(stored) && contrastOnWhite(stored) >= MIN_BRIGHT_CONTRAST) {
    const bright = stored.startsWith("#") ? stored : `#${stored}`;
    return { id: bright, label: "Custom", bright, deep: deepen(bright) };
  }
  return accentById(DEFAULT_ACCENT_ID) ?? ACCENTS[0];
}

/**
 * The CSS custom properties an accent owns.
 *
 * Deliberately *not* `--primary` or `--cta`: every pressable fill in this app
 * is ink (#171717) and stays that way whatever accent is chosen. Widening this
 * map to the button tokens would let a pale accent produce an unreadable
 * primary button, and would also change far more of the screen than someone
 * picking an "accent colour" is asking for.
 */
export function accentVariables(accent: Accent): Record<string, string> {
  return {
    "--accent-brand": accent.bright,
    "--accent-brand-soft": withAlpha(accent.bright, 0.1),
    "--accent-brand-tint": tint(accent.bright, 0.88),
    "--ring": accent.bright,
    "--sidebar-ring": accent.bright,
    "--brand-blue": accent.deep,
    "--brand-blue-hover": deepen(accent.deep),
    "--brand-blue-soft": withAlpha(accent.bright, 0.1),
    "--brand-blue-glow": withAlpha(accent.bright, 0.14),
  };
}

/** Write the accent onto the document. No-op outside a browser. */
export function applyAccent(accent: Accent): void {
  if (typeof document === "undefined") return;
  const style = document.documentElement.style;
  for (const [name, value] of Object.entries(accentVariables(accent))) {
    style.setProperty(name, value);
  }
}

/**
 * What goes into localStorage: the id *and* the resolved variables.
 *
 * Storing the computed map, rather than recomputing it at boot, is what lets
 * the anti-flash script in layout.tsx be four lines of `setProperty` with no
 * colour maths in it. The alternative — reinventing `deepen` and `tint` inside
 * an inline `<script>` — is two implementations of the same rule that drift
 * the first time either is touched, and the symptom of that drift is a
 * different colour before and after hydration.
 */
interface StoredAccent {
  id: string;
  vars: Record<string, string>;
}

export function readStoredAccent(): string | null {
  try {
    const raw = window.localStorage.getItem(ACCENT_STORAGE_KEY);
    if (!raw) return null;
    // A bare id or hex is what an older build wrote. Accept it rather than
    // resetting somebody's choice on upgrade.
    if (!raw.startsWith("{")) return raw;
    const parsed = JSON.parse(raw) as Partial<StoredAccent>;
    return typeof parsed.id === "string" ? parsed.id : null;
  } catch {
    // Private windows, blocked site data and malformed JSON all land here. An
    // accent is a preference, not state worth failing over.
    return null;
  }
}

export function storeAccent(accent: Accent): void {
  try {
    const payload: StoredAccent = { id: accent.id, vars: accentVariables(accent) };
    window.localStorage.setItem(ACCENT_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    /* see readStoredAccent */
  }
}

/**
 * The inline script that restores the accent before first paint.
 *
 * Emitted from layout.tsx, which is a server component, so this is a string
 * built at render time rather than a second copy of the logic. It reads only
 * what `storeAccent` wrote; anything unexpected is ignored and the stylesheet
 * default stands.
 */
export const ACCENT_BOOT_SCRIPT = `(function(){try{
var raw=localStorage.getItem(${JSON.stringify(ACCENT_STORAGE_KEY)});
if(!raw||raw[0]!=="{")return;
var vars=(JSON.parse(raw)||{}).vars;
if(!vars)return;
var s=document.documentElement.style;
for(var k in vars){if(k.indexOf("--")===0)s.setProperty(k,vars[k]);}
}catch(e){}})();`;
