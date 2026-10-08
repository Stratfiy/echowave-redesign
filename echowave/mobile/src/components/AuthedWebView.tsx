/**
 * A web app screen, signed in, inside the app (src/lib/webScreens.ts).
 *
 * The first page is loaded from a string with the web app as its base URL,
 * so its one request -- POST /api/auth/session, the web's own sign-in step --
 * is same-origin and sets the web app's httpOnly cookie; then it goes to the
 * screen. Links that leave the web app open in the phone's browser, never in
 * this view.
 */
import * as WebBrowser from 'expo-web-browser';
import { useState } from 'react';
import { View } from 'react-native';
import { WebView } from 'react-native-webview';

import { Loading, Notice } from '@/components/ui';
import { getToken } from '@/lib/auth/token';
import { webAppUrl } from '@/lib/config';
import { useI18n } from '@/lib/i18n';
import { bootstrapHtml } from '@/lib/webScreens';

export function AuthedWebView({ path, user }: { path: string; user: unknown }) {
    const { t } = useI18n();
    const [failed, setFailed] = useState(false);
    const base = webAppUrl();
    const token = getToken();
    if (!token) return <Notice text={t('web.failed')} tone="bad" />;
    return (
        <View style={{ flex: 1 }}>
            {failed ? <Notice text={t('web.failed')} tone="bad" /> : null}
            <WebView
                source={{ html: bootstrapHtml(token, user, path), baseUrl: `${base}/` }}
                originWhitelist={[base, 'about:*']}
                sharedCookiesEnabled
                thirdPartyCookiesEnabled={false}
                startInLoadingState
                renderLoading={() => <Loading label={t('web.loading')} />}
                onMessage={(event) => {
                    if (event.nativeEvent.data === 'failed') setFailed(true);
                }}
                onShouldStartLoadWithRequest={(request) => {
                    if (request.url.startsWith(base) || request.url.startsWith('about:')) return true;
                    void WebBrowser.openBrowserAsync(request.url);
                    return false;
                }}
                testID="authed-webview"
            />
        </View>
    );
}
