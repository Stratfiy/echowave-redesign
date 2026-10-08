"use client";

import { useSyncExternalStore } from "react";

/**
 * The two Chat entry points another stream owns, as clean hooks.
 *
 * Talk opens the live voice session (screen 05, stream `voice`); Meeting
 * mode opens meeting capture (screen 11, stream `meetings`). Chat must not
 * import either, and must not pretend either works before it does. So each
 * stream registers a handler when its feature is ready, and the composer
 * reads whether one is there:
 *
 *   registerTalk((ctx) => openVoiceSession(ctx));     // voice stream
 *   const talk = useEntryPoint("talk");                // composer
 *   talk.available ? talk.open(ctx) : <unavailable state>
 *
 * Nothing registered is a real state -- "Live voice is not available yet"
 * -- never a button that does nothing.
 */

export type EntryContext = {
    /** Which of Decibyl's conversations the person is in. */
    threadId: string | null;
    /** The words already in the box, so a session can pick up from them. */
    draft: string;
};

export type EntryPointName = "talk" | "meeting";
export type EntryHandler = (context: EntryContext) => void;

const handlers = new Map<EntryPointName, EntryHandler>();
const listeners = new Set<() => void>();
let version = 0;

function emit() {
    version += 1;
    for (const listener of listeners) listener();
}

/** Register the handler for an entry point. Returns an unregister function. */
export function registerEntryPoint(name: EntryPointName, handler: EntryHandler): () => void {
    handlers.set(name, handler);
    emit();
    return () => {
        if (handlers.get(name) === handler) {
            handlers.delete(name);
            emit();
        }
    };
}

export const registerTalk = (handler: EntryHandler) => registerEntryPoint("talk", handler);
export const registerMeetingMode = (handler: EntryHandler) => registerEntryPoint("meeting", handler);

export function hasEntryPoint(name: EntryPointName): boolean {
    return handlers.has(name);
}

export function openEntryPoint(name: EntryPointName, context: EntryContext): boolean {
    const handler = handlers.get(name);
    if (!handler) return false;
    handler(context);
    return true;
}

function subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
}

/** Whether an entry point has a handler, re-rendering when one registers. */
export function useEntryPoint(name: EntryPointName): {
    available: boolean;
    open: (context: EntryContext) => boolean;
} {
    useSyncExternalStore(subscribe, () => version, () => 0);
    return {
        available: handlers.has(name),
        open: (context) => openEntryPoint(name, context),
    };
}

/**
 * A conversation started somewhere other than Chat's composer -- Talk from
 * the start screen, where the server starts a new one of the person's own
 * when the original is not theirs. Chat listens and switches to it, so what
 * was said is on screen rather than in a thread nobody opened.
 */
export type ThreadStartedListener = (threadId: string) => void;

const threadListeners = new Set<ThreadStartedListener>();

export function announceThreadStarted(threadId: string) {
    for (const listener of threadListeners) listener(threadId);
}

/** Listen for conversations started elsewhere. Returns an unsubscribe. */
export function onThreadStarted(listener: ThreadStartedListener): () => void {
    threadListeners.add(listener);
    return () => {
        threadListeners.delete(listener);
    };
}

/** For tests. */
export function resetEntryPoints() {
    handlers.clear();
    threadListeners.clear();
    emit();
}
