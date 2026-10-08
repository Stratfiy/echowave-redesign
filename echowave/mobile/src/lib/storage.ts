/**
 * Small key-value stores.
 *
 * `secure` is the Keychain / Android Keystore through expo-secure-store: the
 * bearer token, the signed-in user and the biometric switch live there and
 * nowhere else. On the web build (used only for development and the
 * screenshot walkthrough) SecureStore does not exist, so it falls back to
 * localStorage; a store build never runs that branch.
 *
 * `plain` is AsyncStorage, for things that are not secrets: the contact
 * snapshot hashes, the consent record, the last sync time.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

export type KeyValue = {
    get(key: string): Promise<string | null>;
    set(key: string, value: string): Promise<void>;
    remove(key: string): Promise<void>;
};

const webStore: KeyValue = {
    async get(key) {
        try {
            return globalThis.localStorage?.getItem(key) ?? null;
        } catch {
            return null;
        }
    },
    async set(key, value) {
        try {
            globalThis.localStorage?.setItem(key, value);
        } catch {
            // Private mode: the session lasts this page only.
        }
    },
    async remove(key) {
        try {
            globalThis.localStorage?.removeItem(key);
        } catch {
            // Nothing to remove.
        }
    },
};

export const secure: KeyValue =
    Platform.OS === 'web'
        ? webStore
        : {
              get: (key) => SecureStore.getItemAsync(key),
              set: (key, value) => SecureStore.setItemAsync(key, value, { keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK }),
              remove: (key) => SecureStore.deleteItemAsync(key),
          };

export const plain: KeyValue = {
    get: (key) => AsyncStorage.getItem(key),
    set: (key, value) => AsyncStorage.setItem(key, value),
    remove: (key) => AsyncStorage.removeItem(key),
};

export function memoryStore(seed: Record<string, string> = {}): KeyValue & { dump(): Record<string, string> } {
    const data = new Map(Object.entries(seed));
    return {
        async get(key) {
            return data.has(key) ? (data.get(key) as string) : null;
        },
        async set(key, value) {
            data.set(key, value);
        },
        async remove(key) {
            data.delete(key);
        },
        dump() {
            return Object.fromEntries(data);
        },
    };
}
