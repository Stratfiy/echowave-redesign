/**
 * The Windows and Mac app (echowave/desktop), seen from the web app.
 *
 * When Decibyl runs inside the desktop app, its preload puts
 * `window.decibylDesktop` on the page; in a browser it is absent and every
 * helper here is a no-op. The desktop re-checks every call's origin, so
 * nothing here is a security boundary -- it is the web app's half of a
 * small, typed contract.
 */

export type DesktopNotice = { title: string; body?: string; url?: string };

export type DesktopFile = {
    name: string;
    relativePath: string;
    size: number;
    type: string;
    /** base64 */
    data: string;
};

export type DesktopFiles = { files: DesktopFile[]; skipped: string[] };

export type StartResult = { started: true; sessionId: string } | { started: false; reason: string };

export type DesktopEvent =
    | { type: 'started'; task: string }
    | { type: 'step'; app: string; detail: string }
    | { type: 'blocked'; reason: string }
    | { type: 'awaiting_approval'; id: number; summary: string }
    | { type: 'approval_settled'; id: number; outcome: string }
    | { type: 'progress'; steps: number; seconds: number; costUsd: number }
    | { type: 'finished'; receiptId: string; reason: string };

export interface DesktopBridge {
    isDesktop: true;
    notify(notice: DesktopNotice): Promise<unknown>;
    setSession(session: { apiBase: string; token: string; threadId?: string | null } | null): Promise<unknown>;
    pickFiles(options?: { folders?: boolean }): Promise<DesktopFiles>;
    computer: {
        start(task: string, threadId?: string | null): Promise<StartResult>;
        stop(): Promise<unknown>;
        onEvent(callback: (event: DesktopEvent) => void): () => void;
    };
    onFiles(callback: (files: DesktopFiles) => void): () => void;
}

export function desktopBridge(): DesktopBridge | null {
    if (typeof window === 'undefined') return null;
    const bridge = (window as unknown as { decibylDesktop?: DesktopBridge }).decibylDesktop;
    return bridge && bridge.isDesktop === true && typeof bridge.notify === 'function' ? bridge : null;
}

type Notice = { id: number; title: string; body: string | null; link: string | null; read_at: string | null };

/**
 * Which notices to raise natively: unread ones newer than the newest seen.
 * The first load only sets the mark -- opening the app must not replay a
 * week of notices as a burst of pop-ups.
 */
export function noticesToForward(items: Notice[], lastSeen: number | null): { forward: Notice[]; lastSeen: number } {
    const newest = items.reduce((max, item) => Math.max(max, item.id), lastSeen ?? 0);
    if (lastSeen === null) return { forward: [], lastSeen: newest };
    const forward = items.filter((item) => item.id > lastSeen && !item.read_at).sort((a, b) => a.id - b.id);
    return { forward: forward.slice(-3), lastSeen: newest };
}

/** A notice's link as a path in the app, or "/" (never an outside URL). */
export function noticePath(link: string | null): string {
    if (!link || !link.startsWith('/') || link.startsWith('//')) return '/';
    return link;
}

/** Files from the desktop as browser Files, for the composer's attach. */
export function toFiles(result: DesktopFiles): File[] {
    return result.files.map((file) => {
        const bytes = Uint8Array.from(atob(file.data), (c) => c.charCodeAt(0));
        return new File([bytes], file.name, { type: file.type });
    });
}

/** The Decibyl thread on screen, from the address bar. */
export function currentThread(): string | null {
    if (typeof window === 'undefined') return null;
    try {
        return new URLSearchParams(window.location.search).get('thread');
    } catch {
        return null;
    }
}
