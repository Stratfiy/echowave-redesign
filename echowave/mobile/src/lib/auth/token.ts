/**
 * The bearer token every API request carries.
 *
 * One interceptor, installed once, reads the current token at request time
 * (never a copy baked into a closure), so signing out takes effect on the
 * very next call. A 401 on a request that carried a token means the token
 * expired or was refused: the listener signs the person out.
 */
import { client } from '@/client/client.gen';

let current: string | null = null;
let installed = false;
let onUnauthorized: (() => void) | null = null;

export function setToken(token: string | null): void {
    current = token;
}

export function getToken(): string | null {
    return current;
}

export function setUnauthorizedListener(listener: (() => void) | null): void {
    onUnauthorized = listener;
}

/** Headers for calls that do not go through the generated client (uploads to
 * a presigned URL never get one; the WebSocket uses its own protocol). */
export function authHeader(): Record<string, string> {
    return current ? { Authorization: `Bearer ${current}` } : {};
}

export function installAuth(): void {
    if (installed) return;
    installed = true;
    client.interceptors.request.use(async (request) => {
        if (current && !request.headers.get('Authorization')) {
            request.headers.set('Authorization', `Bearer ${current}`);
        }
        return request;
    });
    client.interceptors.response.use(async (response, request) => {
        const isLogin = request.url.includes('/auth/login') || request.url.includes('/auth/signup');
        if (response.status === 401 && current && !isLogin) onUnauthorized?.();
        return response;
    });
}
