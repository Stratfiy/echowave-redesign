import type { ConfigContext, ExpoConfig } from 'expo/config';

/**
 * Decibyl for iOS and Android.
 *
 * Values the founder confirms (RELEASE.md) come from the environment, set
 * per build profile in eas.json, so nothing here changes for a release:
 *
 * - DECIBYL_APP_NAME, DECIBYL_IOS_BUNDLE_ID, DECIBYL_ANDROID_PACKAGE
 * - EAS_PROJECT_ID (from `eas init`; Expo push tokens are per project)
 * - EXPO_PUBLIC_API_URL / EXPO_PUBLIC_WEB_URL (which Decibyl the build talks to)
 * - GOOGLE_SERVICES_JSON (an EAS file secret: FCM for Android push)
 *
 * Permission texts are what iOS and Android show before each permission;
 * the in-app consent for contacts comes first (src/app/(tabs)/people.tsx).
 */
const APP_NAME = process.env.DECIBYL_APP_NAME || 'Decibyl';
const IOS_BUNDLE_ID = process.env.DECIBYL_IOS_BUNDLE_ID || 'ai.decibyl.app';
const ANDROID_PACKAGE = process.env.DECIBYL_ANDROID_PACKAGE || 'ai.decibyl.app';
const WEB_HOSTS = ['app.decibyl.ai'];

const CONTACTS_TEXT =
    'Decibyl reads the names, phone numbers and email addresses in your contacts, after you agree in the app, so it can tell you who someone is and keep a short brief on each person. You can stop at any time in Settings.';
const MICROPHONE_TEXT = 'Decibyl uses the microphone when you talk to it or record a voice note.';
const CAMERA_TEXT = 'Decibyl uses the camera when you take a photo to send in a chat.';
const PHOTOS_TEXT = 'Decibyl opens your photos when you choose one to send in a chat.';
const FACE_ID_TEXT = 'Decibyl uses Face ID to unlock the app when you turn this on in Settings.';

export default ({ config }: ConfigContext): ExpoConfig => ({
    ...config,
    name: APP_NAME,
    slug: 'decibyl',
    scheme: 'decibyl',
    version: '0.1.0',
    orientation: 'portrait',
    icon: './assets/icon.png',
    userInterfaceStyle: 'automatic',
    runtimeVersion: { policy: 'appVersion' },
    ios: {
        bundleIdentifier: IOS_BUNDLE_ID,
        supportsTablet: false,
        associatedDomains: WEB_HOSTS.map((host) => `applinks:${host}`),
        config: { usesNonExemptEncryption: false },
        infoPlist: {
            NSContactsUsageDescription: CONTACTS_TEXT,
            NSMicrophoneUsageDescription: MICROPHONE_TEXT,
            NSCameraUsageDescription: CAMERA_TEXT,
            NSPhotoLibraryUsageDescription: PHOTOS_TEXT,
            NSFaceIDUsageDescription: FACE_ID_TEXT,
            UIBackgroundModes: ['audio', 'remote-notification'],
        },
    },
    android: {
        package: ANDROID_PACKAGE,
        adaptiveIcon: {
            backgroundColor: '#ffffff',
            foregroundImage: './assets/android-icon-foreground.png',
            backgroundImage: './assets/android-icon-background.png',
            monochromeImage: './assets/android-icon-monochrome.png',
        },
        googleServicesFile: process.env.GOOGLE_SERVICES_JSON,
        permissions: [
            'android.permission.READ_CONTACTS',
            'android.permission.RECORD_AUDIO',
            'android.permission.CAMERA',
            'android.permission.USE_BIOMETRIC',
            'android.permission.POST_NOTIFICATIONS',
            'android.permission.MODIFY_AUDIO_SETTINGS',
        ],
        // Decibyl only reads contacts; it never writes the address book. Nor
        // does it draw over other apps (a dev-client default).
        blockedPermissions: ['android.permission.WRITE_CONTACTS', 'android.permission.SYSTEM_ALERT_WINDOW'],
        intentFilters: [
            {
                action: 'VIEW',
                autoVerify: true,
                data: WEB_HOSTS.flatMap((host) =>
                    ['/overview', '/chat', '/tasks/approvals', '/tasks/reminders', '/tasks'].map((pathPrefix) => ({
                        scheme: 'https',
                        host,
                        pathPrefix,
                    })),
                ),
                category: ['BROWSABLE', 'DEFAULT'],
            },
        ],
        predictiveBackGestureEnabled: false,
    },
    web: { bundler: 'metro', output: 'single', favicon: './assets/favicon.png' },
    plugins: [
        'expo-router',
        'expo-secure-store',
        'expo-localization',
        'expo-web-browser',
        'expo-font',
        'expo-background-task',
        ['expo-splash-screen', { image: './assets/splash-icon.png', imageWidth: 160, backgroundColor: '#ffffff' }],
        ['expo-contacts', { contactsPermission: CONTACTS_TEXT }],
        ['expo-audio', { microphonePermission: MICROPHONE_TEXT }],
        ['expo-image-picker', { cameraPermission: CAMERA_TEXT, photosPermission: PHOTOS_TEXT, microphonePermission: false }],
        ['expo-local-authentication', { faceIDPermission: FACE_ID_TEXT }],
        ['expo-notifications', { color: '#0d0d0d', defaultChannel: 'default' }],
        ['@config-plugins/react-native-webrtc', { cameraPermission: CAMERA_TEXT, microphonePermission: MICROPHONE_TEXT }],
        [
            'expo-sharing',
            {
                ios: {
                    enabled: true,
                    activationRule: { supportsText: true, supportsWebUrlWithMaxCount: 1, supportsImageWithMaxCount: 1, supportsFileWithMaxCount: 1 },
                },
                android: {
                    enabled: true,
                    singleShareMimeTypes: ['text/plain', 'image/*', 'application/pdf', 'text/*', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/json'],
                },
            },
        ],
    ],
    extra: {
        apiUrl: process.env.EXPO_PUBLIC_API_URL,
        webUrl: process.env.EXPO_PUBLIC_WEB_URL,
        eas: process.env.EAS_PROJECT_ID ? { projectId: process.env.EAS_PROJECT_ID } : undefined,
    },
    owner: process.env.EXPO_OWNER || undefined,
});
