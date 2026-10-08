import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { Pressable, View } from 'react-native';

import { useI18n } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';

/** Profile in the header (the shell's rule): Settings live behind it. */
export function ProfileButton() {
    const router = useRouter();
    const theme = useTheme();
    const { t } = useI18n();
    return (
        <Pressable
            testID="open-settings"
            accessibilityRole="button"
            accessibilityLabel={t('settings.title')}
            onPress={() => router.push('/settings')}
            hitSlop={8}
            style={{ paddingHorizontal: 12, minHeight: 44, justifyContent: 'center' }}
        >
            <Ionicons name="person-circle-outline" size={28} color={theme.colors.ink} />
        </Pressable>
    );
}

export function TalkButton({ threadId }: { threadId?: string | null }) {
    const router = useRouter();
    const theme = useTheme();
    const { t } = useI18n();
    return (
        <View style={{ flexDirection: 'row' }}>
            <Pressable
                testID="open-voice"
                accessibilityRole="button"
                accessibilityLabel={t('chat.composer.talk')}
                onPress={() => router.push({ pathname: '/voice', params: threadId ? { thread: threadId } : {} })}
                hitSlop={8}
                style={{ paddingHorizontal: 10, minHeight: 44, justifyContent: 'center' }}
            >
                <Ionicons name="call-outline" size={24} color={theme.colors.ink} />
            </Pressable>
        </View>
    );
}
