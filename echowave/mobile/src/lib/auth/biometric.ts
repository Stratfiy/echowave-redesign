/**
 * Optional biometric unlock (Face ID, Touch ID, fingerprint).
 *
 * It guards opening the app on this phone; it is not a second factor on the
 * server and never replaces the password. Off until the person turns it on
 * in Settings, and only offered when the phone has a biometric enrolled.
 */
import * as LocalAuthentication from 'expo-local-authentication';
import { Platform } from 'react-native';

export async function biometricAvailable(): Promise<boolean> {
    if (Platform.OS === 'web') return false;
    try {
        const [hardware, enrolled] = await Promise.all([
            LocalAuthentication.hasHardwareAsync(),
            LocalAuthentication.isEnrolledAsync(),
        ]);
        return hardware && enrolled;
    } catch {
        return false;
    }
}

export async function confirmBiometric(prompt: string, fallback: string): Promise<boolean> {
    try {
        const result = await LocalAuthentication.authenticateAsync({
            promptMessage: prompt,
            fallbackLabel: fallback,
            disableDeviceFallback: false,
        });
        return result.success;
    } catch {
        return false;
    }
}
