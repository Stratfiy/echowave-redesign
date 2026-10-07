import * as Crypto from 'expo-crypto';

/** A new thread is just a new id: the server has no create call; the first
 * message to an id starts it (as the web does with crypto.randomUUID()). */
export function newThreadId(): string {
    return Crypto.randomUUID();
}
