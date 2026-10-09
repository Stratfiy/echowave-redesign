/**
 * Where a face looks when it follows the pointer, after bloub's
 * src/ui/gaze.ts (github.com/jeremy-prt/bloub, MIT; see src/lib/bloub/README).
 *
 * The constants are bloub's, measured off the reference video, less the
 * sideways turn bloub's own page gives its bot (it sits beside a panel; ours
 * sits in the middle of the page, so it faces the reader).
 */

import type { Look } from "@/lib/bloub/engine";

/** How far the eyes travel, in degrees, at the edge of the screen. */
export const YAW_MAX = 16;
export const PITCH_MAX = 13;
/** The resting tilt: a face looks slightly up at the reader. */
export const PITCH = 10;
/** Seconds for the head to come round to the pointer. */
export const TURN_TIME = 1.1;

/**
 * `nx`, `ny`: the pointer from the face's centre, -1..1 across half the
 * window. `mix`: how much the pointer, rather than the pose, decides where
 * the eyes go. Without a pointer the face keeps its idle drift.
 */
export function lookAt(nx: number, ny: number, mix: number, pointer: boolean): Look {
    return {
        yaw: nx * YAW_MAX,
        // positive pitch looks up; screen y grows downwards
        pitch: PITCH - ny * PITCH_MAX,
        mix,
        spin: 0,
        wander: pointer ? 0 : 1,
    };
}
