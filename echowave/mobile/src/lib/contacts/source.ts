/**
 * The phone's address book through expo-contacts (SDK 57's `Contact` API).
 *
 * Only the three fields Decibyl uses are read: the full name, phone numbers
 * and email addresses. The web build has no address book; for the
 * development walkthrough only (never a store build) a fixture source can be
 * switched on with EXPO_PUBLIC_DEMO_CONTACTS=1, and the screen labels it.
 */
import { Contact, ContactField, requestPermissionsAsync, getPermissionsAsync } from 'expo-contacts';
import { Platform } from 'react-native';

import type { DeviceContact } from './diff';
import type { ContactSource, Permission } from './sync';

const FIELDS = [ContactField.FULL_NAME, ContactField.PHONES, ContactField.EMAILS] as const;

function asPermission(status: string | undefined): Permission {
    return status === 'granted' ? 'granted' : status === 'denied' ? 'denied' : 'undetermined';
}

export const phoneContacts: ContactSource = {
    available: () => Platform.OS === 'ios' || Platform.OS === 'android',
    async permission() {
        return asPermission((await getPermissionsAsync()).status);
    },
    async request() {
        return asPermission((await requestPermissionsAsync()).status);
    },
    async read() {
        const details = await Contact.getAllDetails(FIELDS);
        return details.map(
            (d): DeviceContact => ({
                id: d.id,
                name: d.fullName ?? null,
                phones: (d.phones ?? []).map((p) => p.number),
                emails: (d.emails ?? []).map((e) => e.address),
            }),
        );
    },
};

/** Development fixtures for the web walkthrough. Clearly not real people. */
export const demoContacts: ContactSource = {
    available: () => true,
    permission: async () => 'granted',
    request: async () => 'granted',
    read: async () => [
        { id: 'demo-1', name: 'Asha Verma (sample)', phones: ['098765 43210'], emails: ['asha@example.com'] },
        { id: 'demo-2', name: 'Rahul Mehta (sample)', phones: ['+91 91234 56789'], emails: [] },
        { id: 'demo-3', name: 'Priya Nair (sample)', phones: ['08012345678'], emails: ['PRIYA@EXAMPLE.COM'] },
        { id: 'demo-4', name: 'Sunita Rao (sample)', phones: ['9988776655', '+91 99887 76655'], emails: [] },
    ],
};

export function contactSource(): ContactSource {
    if (Platform.OS === 'web' && process.env.EXPO_PUBLIC_DEMO_CONTACTS === '1') return demoContacts;
    return phoneContacts;
}

export const isDemoContacts = () => Platform.OS === 'web' && process.env.EXPO_PUBLIC_DEMO_CONTACTS === '1';
