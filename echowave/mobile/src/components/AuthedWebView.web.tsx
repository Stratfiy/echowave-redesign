/**
 * The web build's stand-in for the in-app web view: an iframe of the same
 * web app page. It has no way to hand over the session itself (the page is
 * another origin), so it shows the page as the browser is signed in to it.
 * Store builds use AuthedWebView.tsx.
 */
import { createElement } from 'react';
import { View } from 'react-native';

import { webAppUrl } from '@/lib/config';
import { safeWebPath } from '@/lib/webScreens';

export function AuthedWebView({ path }: { path: string; user: unknown }) {
    return (
        <View style={{ flex: 1 }} testID="authed-webview">
            {createElement('iframe', {
                src: `${webAppUrl()}${safeWebPath(path)}`,
                style: { border: 0, width: '100%', height: '100%' },
                title: path,
            })}
        </View>
    );
}
