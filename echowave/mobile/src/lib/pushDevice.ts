/** The real phone behind `PushDeps` (src/lib/push.ts). */
import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

import {
    registerMobileTokenApiV1MeMobilePushTokensPost,
    unregisterMobileTokenApiV1MeMobilePushTokensRemovePost,
} from '@/client/sdk.gen';
import { call } from '@/lib/api';
import { easProjectId } from '@/lib/config';
import { secure } from '@/lib/storage';

import type { PushDeps } from './push';

export function phonePushDeps(): PushDeps {
    return {
        platform: Platform.OS,
        isDevice: Device.isDevice,
        deviceName: Device.deviceName ?? Device.modelName ?? null,
        appVersion: Constants.expoConfig?.version ?? null,
        projectId: easProjectId(),
        notifications: Notifications,
        androidImportanceHigh: Notifications.AndroidImportance.HIGH,
        register: (body) => call(registerMobileTokenApiV1MeMobilePushTokensPost({ body })),
        unregister: (body) => call(unregisterMobileTokenApiV1MeMobilePushTokensRemovePost({ body })),
        store: secure,
    };
}

/** While the person is looking at a thread, a reply to that thread is not
 * also shown as a banner. Everything else is. */
let visibleThread: string | null = null;

export function setVisibleThread(threadId: string | null): void {
    visibleThread = threadId;
}

export function installNotificationHandler(): void {
    if (Platform.OS === 'web') return;
    Notifications.setNotificationHandler({
        handleNotification: async (notification) => {
            const url = String(notification.request.content.data?.url ?? '');
            const thread = /[?&]thread=([^&]+)/.exec(url)?.[1] ?? null;
            const hide = thread !== null && thread === visibleThread;
            return {
                shouldPlaySound: !hide,
                shouldSetBadge: false,
                shouldShowBanner: !hide,
                shouldShowList: !hide,
            };
        },
    });
}
