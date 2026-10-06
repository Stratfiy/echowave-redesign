/**
 * One requestAnimationFrame for every face on the page.
 *
 * A roster of thirty agents with thirty loops is thirty timers waking each
 * frame; one loop that hands every subscriber the same bounded delta costs one.
 * The delta is capped (as bloub's own player does) so a tab brought back from
 * the background resumes rather than jumping ahead. The loop stops when the
 * last face unsubscribes.
 */

type Listener = (dt: number) => void;

const listeners = new Set<Listener>();
let raf = 0;
let last = 0;

function tick(ms: number) {
    const dt = last ? Math.min((ms - last) / 1000, 0.064) : 0;
    last = ms;
    for (const listener of listeners) listener(dt);
    raf = listeners.size ? requestAnimationFrame(tick) : 0;
}

export function subscribe(listener: Listener): () => void {
    listeners.add(listener);
    if (!raf && typeof requestAnimationFrame === "function") {
        last = 0;
        raf = requestAnimationFrame(tick);
    }
    return () => {
        listeners.delete(listener);
        if (!listeners.size && raf) {
            cancelAnimationFrame(raf);
            raf = 0;
        }
    };
}
