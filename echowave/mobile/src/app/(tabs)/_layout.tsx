import { Ionicons } from '@expo/vector-icons';
import { Tabs } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ProfileButton } from '@/components/HeaderButtons';
import { useI18n } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';

/** Chat and Today are the destinations (LAUNCH-PLAN.md); People sits beside
 * them on the phone because the address book lives there. */
export default function TabsLayout() {
    const { t } = useI18n();
    const theme = useTheme();
    const insets = useSafeAreaInsets();
    return (
        <Tabs
            screenOptions={{
                headerStyle: { backgroundColor: theme.colors.paper },
                headerTintColor: theme.colors.ink,
                headerTitleStyle: { fontSize: 20 * theme.scale, fontWeight: '700' },
                headerRight: () => <ProfileButton />,
                tabBarActiveTintColor: theme.colors.ink,
                tabBarInactiveTintColor: theme.colors.ink3,
                tabBarStyle: {
                    backgroundColor: theme.colors.paper,
                    borderTopColor: theme.colors.line,
                    height: (theme.simple ? 80 : 68) + insets.bottom,
                    paddingBottom: insets.bottom,
                },
                tabBarItemStyle: { paddingTop: 6, paddingBottom: 4 },
                tabBarLabelStyle: { fontSize: 12 * theme.scale, lineHeight: 16 * theme.scale, fontWeight: '600' },
                sceneStyle: { backgroundColor: theme.colors.paper },
                tabBarHideOnKeyboard: true,
            }}
        >
            <Tabs.Screen
                name="index"
                options={{
                    title: t('tabs.chat'),
                    tabBarButtonTestID: 'tab-chat',
                    tabBarIcon: ({ color, size }) => <Ionicons name="chatbubble-ellipses-outline" color={color} size={size} />,
                }}
            />
            <Tabs.Screen
                name="today"
                options={{
                    title: t('tabs.today'),
                    tabBarButtonTestID: 'tab-today',
                    tabBarIcon: ({ color, size }) => <Ionicons name="today-outline" color={color} size={size} />,
                }}
            />
            <Tabs.Screen
                name="people"
                options={{
                    title: t('tabs.people'),
                    tabBarButtonTestID: 'tab-people',
                    tabBarIcon: ({ color, size }) => <Ionicons name="people-outline" color={color} size={size} />,
                }}
            />
        </Tabs>
    );
}
