/**
 * Background work, defined when the bundle loads (index.ts) so the operating
 * system can wake the app for it.
 *
 * One task today: re-sync the address book incrementally. It uses
 * expo-background-task (WorkManager on Android, BGTaskScheduler on iOS),
 * which replaced expo-background-fetch -- deprecated in SDK 57 -- and is the
 * same idea: the system picks the moment, at most about every
 * `MINIMUM_INTERVAL_MINUTES`, when the battery and network allow.
 *
 * It does nothing without the person's consent, without Contacts
 * permission, or when nobody is signed in on this phone.
 */
import * as BackgroundTask from 'expo-background-task';
import * as TaskManager from 'expo-task-manager';
import { Platform } from 'react-native';

import { loadSession } from '@/lib/auth/session';
import { installAuth, setToken } from '@/lib/auth/token';
import { contactSource } from '@/lib/contacts/source';
import { syncContacts } from '@/lib/contacts/sync';
import { createPeopleApi } from '@/lib/people/api';
import { plain, secure } from '@/lib/storage';

export const CONTACTS_SYNC_TASK = 'decibyl-contacts-sync';
export const MINIMUM_INTERVAL_MINUTES = 6 * 60;

export async function runContactsSync() {
    const session = await loadSession(secure);
    if (!session) return { status: 'signed_out' as const };
    installAuth();
    setToken(session.token);
    return syncContacts({ source: contactSource(), people: createPeopleApi(plain), store: plain });
}

if (Platform.OS !== 'web') {
    TaskManager.defineTask(CONTACTS_SYNC_TASK, async () => {
        try {
            const result = await runContactsSync();
            return result.status === 'failed'
                ? BackgroundTask.BackgroundTaskResult.Failed
                : BackgroundTask.BackgroundTaskResult.Success;
        } catch {
            return BackgroundTask.BackgroundTaskResult.Failed;
        }
    });
}

/** Called once consent is given (and on every app open after). */
export async function scheduleContactsSync(): Promise<void> {
    if (Platform.OS === 'web') return;
    try {
        if (await TaskManager.isTaskRegisteredAsync(CONTACTS_SYNC_TASK)) return;
        await BackgroundTask.registerTaskAsync(CONTACTS_SYNC_TASK, { minimumInterval: MINIMUM_INTERVAL_MINUTES });
    } catch {
        // Background work restricted on this phone: syncing on open still works.
    }
}

export async function cancelContactsSync(): Promise<void> {
    if (Platform.OS === 'web') return;
    try {
        await BackgroundTask.unregisterTaskAsync(CONTACTS_SYNC_TASK);
    } catch {
        // Not registered.
    }
}
