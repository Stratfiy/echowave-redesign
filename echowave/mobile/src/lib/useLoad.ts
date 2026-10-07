import { useEffect, type DependencyList } from 'react';

/**
 * Run an async load when a screen mounts (and when `deps` change), after the
 * render that mounted it rather than inside it. The loads set state when
 * their request answers; starting them from a microtask keeps them out of
 * the effect's synchronous body (react-hooks/set-state-in-effect).
 */
export function useLoad(load: () => Promise<unknown> | void, deps: DependencyList): void {
    useEffect(() => {
        let alive = true;
        void Promise.resolve().then(() => (alive ? load() : undefined));
        return () => {
            alive = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps -- the caller names the deps
    }, deps);
}
