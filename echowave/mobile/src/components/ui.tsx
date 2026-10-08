/**
 * The app's small set of building blocks, drawn from the theme so light,
 * dark and Simple mode come for free. Every pressable is at least the
 * theme's control height (44pt, 56pt in Simple mode) and carries an
 * accessibility role and label.
 */
import type { ReactNode } from 'react';
import {
    ActivityIndicator,
    Pressable,
    ScrollView,
    StyleSheet,
    Switch,
    Text,
    TextInput,
    View,
    type StyleProp,
    type TextInputProps,
    type TextStyle,
    type ViewStyle,
} from 'react-native';
import { SafeAreaView, type Edge } from 'react-native-safe-area-context';

import { useTheme } from '@/lib/theme';

type TxtProps = {
    children: ReactNode;
    size?: number;
    weight?: '400' | '500' | '600' | '700';
    tone?: 'ink' | 'ink2' | 'ink3' | 'bad' | 'live' | 'haldi' | 'onPrimary';
    style?: StyleProp<TextStyle>;
    numberOfLines?: number;
    selectable?: boolean;
    testID?: string;
};

export function Txt({ children, size = 16, weight = '400', tone = 'ink', style, numberOfLines, selectable, testID }: TxtProps) {
    const theme = useTheme();
    return (
        <Text
            testID={testID}
            selectable={selectable}
            numberOfLines={numberOfLines}
            style={[{ color: theme.colors[tone], fontSize: size * theme.scale, lineHeight: size * theme.scale * 1.4, fontWeight: weight }, style]}
        >
            {children}
        </Text>
    );
}

export function Title({ children, testID }: { children: ReactNode; testID?: string }) {
    return (
        <Txt size={24} weight="700" testID={testID} style={{ marginBottom: 4 }}>
            {children}
        </Txt>
    );
}

export function Screen({
    children,
    scroll = true,
    edges = ['top', 'bottom', 'left', 'right'],
    style,
    testID,
}: {
    children: ReactNode;
    scroll?: boolean;
    edges?: Edge[];
    style?: StyleProp<ViewStyle>;
    testID?: string;
}) {
    const theme = useTheme();
    const body = scroll ? (
        <ScrollView contentContainerStyle={[{ padding: 16, gap: 12 }, style]} keyboardShouldPersistTaps="handled">
            {children}
        </ScrollView>
    ) : (
        <View style={[{ flex: 1 }, style]}>{children}</View>
    );
    return (
        <SafeAreaView testID={testID} edges={edges} style={{ flex: 1, backgroundColor: theme.colors.paper }}>
            {body}
        </SafeAreaView>
    );
}

type ButtonProps = {
    label: string;
    onPress?: () => void;
    kind?: 'primary' | 'secondary' | 'quiet' | 'danger';
    disabled?: boolean;
    busy?: boolean;
    icon?: string;
    accessibilityHint?: string;
    testID?: string;
    style?: StyleProp<ViewStyle>;
    compact?: boolean;
};

export function Button({ label, onPress, kind = 'primary', disabled, busy, icon, accessibilityHint, testID, style, compact }: ButtonProps) {
    const theme = useTheme();
    const c = theme.colors;
    const bg = kind === 'primary' ? c.primary : kind === 'danger' ? c.bad : kind === 'secondary' ? c.paper2 : 'transparent';
    const fg = kind === 'primary' ? c.onPrimary : kind === 'danger' ? '#ffffff' : c.ink;
    const inactive = disabled || busy;
    return (
        <Pressable
            testID={testID}
            accessibilityRole="button"
            accessibilityLabel={label}
            accessibilityHint={accessibilityHint}
            accessibilityState={{ disabled: Boolean(inactive), busy: Boolean(busy) }}
            disabled={inactive}
            onPress={onPress}
            style={({ pressed }) => [
                {
                    minHeight: compact ? Math.min(theme.control, 40) : theme.control,
                    paddingHorizontal: compact ? 12 : 18,
                    borderRadius: theme.radius.pill,
                    backgroundColor: bg,
                    borderWidth: kind === 'secondary' ? StyleSheet.hairlineWidth : 0,
                    borderColor: c.line,
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexDirection: 'row',
                    gap: 8,
                    opacity: inactive ? 0.5 : pressed ? 0.8 : 1,
                },
                style,
            ]}
        >
            {busy ? <ActivityIndicator color={fg} /> : null}
            {icon ? <Text style={{ fontSize: 16 * theme.scale, color: fg }}>{icon}</Text> : null}
            <Text style={{ color: fg, fontSize: (compact ? 14 : 16) * theme.scale, fontWeight: '600' }}>{label}</Text>
        </Pressable>
    );
}

export function Chip({ label, onPress, testID, selected }: { label: string; onPress?: () => void; testID?: string; selected?: boolean }) {
    const theme = useTheme();
    return (
        <Pressable
            testID={testID}
            accessibilityRole="button"
            accessibilityLabel={label}
            onPress={onPress}
            style={({ pressed }) => ({
                minHeight: Math.min(theme.control, 44),
                paddingHorizontal: 14,
                paddingVertical: 8,
                borderRadius: theme.radius.pill,
                borderWidth: 1,
                borderColor: selected ? theme.colors.ink : theme.colors.line,
                backgroundColor: selected ? theme.colors.paper2 : theme.colors.paper,
                justifyContent: 'center',
                opacity: pressed ? 0.7 : 1,
            })}
        >
            <Txt size={14} weight="500">
                {label}
            </Txt>
        </Pressable>
    );
}

export function Field({ label, hint, error, ...input }: TextInputProps & { label: string; hint?: string; error?: string | null }) {
    const theme = useTheme();
    return (
        <View style={{ gap: 6 }}>
            <Txt size={14} weight="600">
                {label}
            </Txt>
            <TextInput
                accessibilityLabel={label}
                placeholderTextColor={theme.colors.ink3}
                {...input}
                style={[
                    {
                        minHeight: theme.control,
                        borderWidth: 1,
                        borderColor: error ? theme.colors.bad : theme.colors.line,
                        borderRadius: theme.radius.md,
                        paddingHorizontal: 12,
                        color: theme.colors.ink,
                        fontSize: 16 * theme.scale,
                        backgroundColor: theme.colors.paper,
                    },
                    input.style,
                ]}
            />
            {hint ? (
                <Txt size={13} tone="ink2">
                    {hint}
                </Txt>
            ) : null}
            {error ? (
                <Txt size={13} tone="bad">
                    {error}
                </Txt>
            ) : null}
        </View>
    );
}

export function Card({ children, style, testID }: { children: ReactNode; style?: StyleProp<ViewStyle>; testID?: string }) {
    const theme = useTheme();
    return (
        <View
            testID={testID}
            style={[
                {
                    borderWidth: StyleSheet.hairlineWidth,
                    borderColor: theme.colors.line,
                    borderRadius: theme.radius.lg,
                    padding: 14,
                    gap: 8,
                    backgroundColor: theme.colors.paper,
                },
                style,
            ]}
        >
            {children}
        </View>
    );
}

export function Section({ title, children, testID }: { title?: string; children: ReactNode; testID?: string }) {
    return (
        <View style={{ gap: 8 }} testID={testID}>
            {title ? (
                <Txt size={13} weight="600" tone="ink2" style={{ textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    {title}
                </Txt>
            ) : null}
            {children}
        </View>
    );
}

export function Row({
    title,
    subtitle,
    right,
    onPress,
    testID,
    chevron,
}: {
    title: string;
    subtitle?: string | null;
    right?: ReactNode;
    onPress?: () => void;
    testID?: string;
    chevron?: boolean;
}) {
    const theme = useTheme();
    const content = (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: theme.control, paddingVertical: 8 }}>
            <View style={{ flex: 1 }}>
                <Txt weight="500" numberOfLines={2}>
                    {title}
                </Txt>
                {subtitle ? (
                    <Txt size={13} tone="ink2" numberOfLines={2}>
                        {subtitle}
                    </Txt>
                ) : null}
            </View>
            {right}
            {chevron ? <Txt tone="ink3">›</Txt> : null}
        </View>
    );
    if (!onPress) return <View testID={testID}>{content}</View>;
    return (
        <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={title} onPress={onPress} style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}>
            {content}
        </Pressable>
    );
}

export function Toggle({ label, hint, value, onChange, disabled, testID }: { label: string; hint?: string; value: boolean; onChange: (v: boolean) => void; disabled?: boolean; testID?: string }) {
    const theme = useTheme();
    return (
        <Row
            title={label}
            subtitle={hint}
            testID={testID}
            right={
                <Switch
                    accessibilityLabel={label}
                    value={value}
                    onValueChange={onChange}
                    disabled={disabled}
                    trackColor={{ true: theme.colors.live, false: theme.colors.line }}
                />
            }
        />
    );
}

export function Divider() {
    const theme = useTheme();
    return <View style={{ height: StyleSheet.hairlineWidth, backgroundColor: theme.colors.line }} />;
}

export function Notice({ text, tone = 'ink2', testID }: { text: string; tone?: 'ink2' | 'bad' | 'live' | 'haldi'; testID?: string }) {
    const theme = useTheme();
    return (
        <View
            testID={testID}
            accessibilityRole="alert"
            style={{ borderRadius: theme.radius.md, padding: 12, backgroundColor: theme.colors.paper2, borderLeftWidth: 3, borderLeftColor: theme.colors[tone] }}
        >
            <Txt size={14} tone={tone === 'ink2' ? 'ink' : tone}>
                {text}
            </Txt>
        </View>
    );
}

export function Loading({ label }: { label?: string }) {
    const theme = useTheme();
    return (
        <View style={{ padding: 24, alignItems: 'center', gap: 8 }} accessibilityRole="progressbar" accessibilityLabel={label}>
            <ActivityIndicator color={theme.colors.ink2} />
            {label ? (
                <Txt size={14} tone="ink2">
                    {label}
                </Txt>
            ) : null}
        </View>
    );
}

export function Empty({ text, action }: { text: string; action?: ReactNode }) {
    return (
        <View style={{ padding: 24, alignItems: 'center', gap: 12 }}>
            <Txt tone="ink2" style={{ textAlign: 'center' }}>
                {text}
            </Txt>
            {action}
        </View>
    );
}

export function Failed({ text, onRetry, retryLabel }: { text: string; onRetry?: () => void; retryLabel: string }) {
    return (
        <View style={{ gap: 8 }}>
            <Notice text={text} tone="bad" />
            {onRetry ? <Button label={retryLabel} kind="secondary" onPress={onRetry} compact /> : null}
        </View>
    );
}
