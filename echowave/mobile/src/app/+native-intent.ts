/**
 * Links from outside, before the router sees them: decibyl:// links,
 * universal links / App Links on the web app's address, and the share sheet
 * (expo-sharing opens the app on the `expo-sharing` host). Each becomes one
 * native route (src/lib/links.ts).
 */
import { routeForUrl } from '@/lib/links';

export function redirectSystemPath({ path }: { path: string; initial: boolean }): string {
    try {
        if (/^[a-z][a-z0-9+.-]*:\/\/expo-sharing/i.test(path)) return '/share';
        // A bare path is already one of ours.
        if (path.startsWith('/')) return path;
        return routeForUrl(path) ?? path;
    } catch {
        return '/';
    }
}
