import { Stack, useLocalSearchParams } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';

import { AuthedWebView } from '@/components/AuthedWebView';
import { useAuth } from '@/lib/auth/AuthProvider';
import { useTheme } from '@/lib/theme';
import { safeWebPath, WEB_SCREENS } from '@/lib/webScreens';

/** An existing web screen, signed in (see src/lib/webScreens.ts for the list). */
export default function Web() {
    const params = useLocalSearchParams<{ path?: string; title?: string }>();
    const { user } = useAuth();
    const theme = useTheme();
    const path = safeWebPath(params.path);
    const title = params.title ?? WEB_SCREENS.find((s) => s.path === path)?.title ?? '';
    return (
        <SafeAreaView edges={['bottom', 'left', 'right']} style={{ flex: 1, backgroundColor: theme.colors.paper }} testID="screen-web">
            <Stack.Screen options={{ title }} />
            <AuthedWebView path={path} user={user} />
        </SafeAreaView>
    );
}
