/**
 * The brains a chat can ask for, mirroring
 * api/services/configuration/chat_presets.py. Three words on the menu
 * (Everyday, Smart, Deep) and, behind "More models", the catalogue by
 * vendor that the API sends for the vendors it holds a key for. Slugs are
 * what the API accepts; the words are what the picker shows. The choice is
 * remembered per chat on this device, because the person who picked Deep
 * for the accounts channel meant it for the next message there too.
 */

export type ChatPreset = { slug: string; label: string; blurb: string };

export const CHAT_PRESETS: ChatPreset[] = [
    { slug: 'everyday', label: 'Everyday', blurb: 'Quick answers. Fine for most messages.' },
    { slug: 'smart', label: 'Smart', blurb: 'A stronger brain for tools and documents.' },
    { slug: 'deep', label: 'Deep', blurb: 'For messages where getting it wrong is expensive.' },
];

/** null is "the bot's own brain", which is what nothing chosen means. */
export const OWN_BRAIN = { slug: '', label: 'Bot’s own', blurb: 'Whatever this bot is set up with.' };

/** One model under "More models": `model:<vendor>/<model>` and its name. */
export type BrainModel = { slug: string; label: string };
/** A vendor's shelf, as the API sends it (only vendors with a key). */
export type BrainVendor = { id: string; label: string; models: BrainModel[] };

/** What the button says for a choice. A slug the menu no longer carries
 *  (a device remembering "advanced", or a vendor whose key was removed)
 *  still shows something readable rather than the raw slug. */
export function labelFor(slug: string, vendors: BrainVendor[] = []): string {
    if (!slug) return OWN_BRAIN.label;
    const preset = CHAT_PRESETS.find((p) => p.slug === slug);
    if (preset) return preset.label;
    for (const vendor of vendors) {
        const model = vendor.models.find((m) => m.slug === slug);
        if (model) return model.label;
    }
    if (slug === 'advanced') return 'Advanced';
    if (slug.startsWith('model:')) return slug.slice('model:'.length).split('/')[1] ?? slug;
    return slug;
}

/** 3200 → "3.2k", 16000 → "16k", 800 → "800". The meter, not an invoice. */
export function formatTokens(n: number): string {
    if (n < 1000) return String(Math.max(0, Math.round(n)));
    const k = n / 1000;
    return `${k >= 10 ? Math.round(k) : Math.round(k * 10) / 10}k`;
}

const KEY = 'decibyl.chat-preset';

function read(): Record<string, string> {
    try {
        return JSON.parse(localStorage.getItem(KEY) || '{}') as Record<string, string>;
    } catch {
        return {};
    }
}

export function rememberedPreset(chatKey: string): string {
    return read()[chatKey] ?? '';
}

export function rememberPreset(chatKey: string, slug: string): void {
    try {
        const all = read();
        if (slug) all[chatKey] = slug;
        else delete all[chatKey];
        localStorage.setItem(KEY, JSON.stringify(all));
    } catch {
        // A remembered convenience, not state: losing it costs one click.
    }
}
