"use client";

import { useEffect, useState } from "react";

/**
 * A draft that survives going offline, a reload or a closed tab (screen 03,
 * "offline draft"). One per conversation on this device. Browser storage
 * can be missing or full; every read and write is wrapped, and a failure
 * only means the draft is not kept -- never that the box stops working.
 */

const PREFIX = "decibyl.chat.draft:";

export function draftKey(chatKey: string, threadId: string | null | undefined): string {
    return `${PREFIX}${chatKey}:${threadId ?? "main"}`;
}

export function readDraft(key: string): string {
    try {
        return window.localStorage.getItem(key) ?? "";
    } catch {
        return "";
    }
}

export function writeDraft(key: string, text: string): void {
    try {
        if (text.trim()) window.localStorage.setItem(key, text);
        else window.localStorage.removeItem(key);
    } catch {
        // No storage: the draft lives only on screen.
    }
}

export function clearDraft(key: string): void {
    writeDraft(key, "");
}

/** Whether the browser says it is online; follows the online/offline events. */
export function useOnline(): boolean {
    const [online, setOnline] = useState(() => (typeof navigator === "undefined" ? true : navigator.onLine !== false));
    useEffect(() => {
        const up = () => setOnline(true);
        const down = () => setOnline(false);
        window.addEventListener("online", up);
        window.addEventListener("offline", down);
        return () => {
            window.removeEventListener("online", up);
            window.removeEventListener("offline", down);
        };
    }, []);
    return online;
}
