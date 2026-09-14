/**
 * The four brains a chat can ask for, mirroring
 * api/services/configuration/chat_presets.py. Slugs are what the API
 * accepts; the words are what the picker shows. The choice is remembered per
 * chat on this device, because the person who picked Deep for the accounts
 * channel meant it for the next message there too.
 */

export type ChatPreset = { slug: string; label: string; blurb: string };

export const CHAT_PRESETS: ChatPreset[] = [
    { slug: 'everyday', label: 'Everyday', blurb: 'Quick answers. Fine for most messages.' },
    { slug: 'smart', label: 'Smart', blurb: 'A stronger brain for tools and documents.' },
    { slug: 'deep', label: 'Deep', blurb: 'For messages where getting it wrong is expensive.' },
    { slug: 'advanced', label: 'Advanced', blurb: 'The strongest model. Thinks before it answers.' },
];

/** null is "the bot's own brain", which is what nothing chosen means. */
export const OWN_BRAIN = { slug: '', label: 'Bot’s own', blurb: 'Whatever this bot is set up with.' };

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
