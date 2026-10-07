"use client";

import { useEffect, useState } from "react";

/**
 * The motion tokens of app/motion.css, for code that needs a number (a
 * timer that waits out an exit, a scroll that should not travel). Kept in
 * step with the CSS by the motion test.
 */
export const MOTION = {
    m1Hover: 120,
    m1Press: 90,
    m2: 200,
    m3Enter: 240,
    m3Exit: 180,
    m4Enter: 200,
    m4Exit: 150,
    m5: 120,
    m6: 180,
    m8: 120,
} as const;

/** What each token becomes under reduced motion: instant, or a fade of at
 *  most 100ms. */
export const REDUCED_MOTION: Record<keyof typeof MOTION, number> = {
    m1Hover: 0,
    m1Press: 0,
    m2: 100,
    m3Enter: 100,
    m3Exit: 100,
    m4Enter: 100,
    m4Exit: 100,
    m5: 0,
    m6: 0,
    m8: 100,
};

const QUERY = "(prefers-reduced-motion: reduce)";

export function prefersReducedMotion(): boolean {
    try {
        return typeof window !== "undefined" && !!window.matchMedia?.(QUERY).matches;
    } catch {
        return false;
    }
}

/** True while the person has asked for reduced motion; follows changes. */
export function useReducedMotion(): boolean {
    const [reduced, setReduced] = useState(prefersReducedMotion);
    useEffect(() => {
        if (typeof window === "undefined" || !window.matchMedia) return;
        const media = window.matchMedia(QUERY);
        const onChange = () => setReduced(media.matches);
        media.addEventListener?.("change", onChange);
        return () => media.removeEventListener?.("change", onChange);
    }, []);
    return reduced;
}

/** A token's duration for this person. */
export function duration(token: keyof typeof MOTION, reduced: boolean): number {
    return reduced ? REDUCED_MOTION[token] : MOTION[token];
}

/** Scroll behaviour that never travels for someone who asked for less
 *  motion. */
export function scrollBehavior(reduced: boolean): ScrollBehavior {
    return reduced ? "auto" : "smooth";
}
