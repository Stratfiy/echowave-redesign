/**
 * The person's agreement to send their contacts to Decibyl.
 *
 * Nothing in the address book leaves the phone until this says yes. The
 * record names the wording version they agreed to, so a change in what is
 * sent (a new field) asks again rather than riding on an old yes.
 */
import type { KeyValue } from '@/lib/storage';

export const CONSENT_KEY = 'decibyl.people.consent';
/** Bump when what is read or where it goes changes. Version 1: name,
 * phones and emails, kept on the phone (DevicePeopleApi). The server adapter
 * must bump it, so nobody's contacts leave the phone on an old yes. */
export const CONSENT_VERSION = 1;

export type Consent = { agreed: boolean; version: number; at: string };

export async function readConsent(store: KeyValue): Promise<Consent | null> {
    const raw = await store.get(CONSENT_KEY);
    if (!raw) return null;
    try {
        const parsed = JSON.parse(raw) as Consent;
        return parsed && typeof parsed.agreed === 'boolean' ? parsed : null;
    } catch {
        return null;
    }
}

/** Agreed to the current wording. An older version is not agreement. */
export async function hasConsent(store: KeyValue): Promise<boolean> {
    const consent = await readConsent(store);
    return Boolean(consent?.agreed && consent.version === CONSENT_VERSION);
}

export async function giveConsent(store: KeyValue, now = new Date()): Promise<Consent> {
    const consent = { agreed: true, version: CONSENT_VERSION, at: now.toISOString() };
    await store.set(CONSENT_KEY, JSON.stringify(consent));
    return consent;
}

export async function withdrawConsent(store: KeyValue): Promise<void> {
    await store.remove(CONSENT_KEY);
}
