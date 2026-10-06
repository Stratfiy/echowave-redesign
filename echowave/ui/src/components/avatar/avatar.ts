/**
 * An agent's face, as the server stores it and the customiser offers it.
 *
 * The ids are bloub's own (src/lib/bloub/skins.ts, expressions.ts) and the
 * server refuses any other (api/schemas/agent_avatar.py). The labels are the
 * English ones from bloub's locale.
 */

import { DEFAULT_EXPRESSION, EXPRESSIONS } from "@/lib/bloub/expressions";
import { COLORS, DEFAULT_COLOR, DEFAULT_SHAPE, SHAPES } from "@/lib/bloub/skins";
import type { StateId } from "@/lib/bloub/states";

export type Avatar = { shape: string; color: string; expression: string };

export const DEFAULT_AVATAR: Avatar = { shape: DEFAULT_SHAPE, color: DEFAULT_COLOR, expression: DEFAULT_EXPRESSION };

export const SHAPE_LABEL: Record<string, string> = {
    cercle: "Circle",
    galet: "Pebble",
    squircle: "Squircle",
    capsule: "Capsule",
    triangle: "Triangle",
    hexagone: "Hexagon",
    nuage: "Cloud",
    goutte: "Droplet",
};

export const COLOR_LABEL: Record<string, string> = {
    encre: "Ink",
    creme: "Cream",
    brun: "Brown",
    rouge: "Red",
    orange: "Orange",
    ambre: "Amber",
    vert: "Green",
    turquoise: "Turquoise",
    bleu: "Blue",
    violet: "Purple",
    rose: "Pink",
    gris: "Grey",
};

export const EXPRESSION_LABEL: Record<string, string> = {
    neutre: "Neutral",
    attentif: "Attentive",
    surpris: "Surprised",
    excite: "Excited",
    heureux: "Happy",
    hilare: "Laughing",
    colere: "Angry",
    triste: "Sad",
    effraye: "Scared",
    mefiant: "Suspicious",
    confus: "Confused",
    curieux: "Curious",
    fier: "Proud",
    timide: "Shy",
    blase: "Unimpressed",
    somnolent: "Sleepy",
};

export const SHAPE_IDS = SHAPES.map((s) => s.id as string);
export const COLOR_OPTIONS = COLORS.map((c) => ({ id: c.id as string, hex: c.hex }));
export const EXPRESSION_IDS = EXPRESSIONS.map((e) => e.id as string);

/** The stored value with the defaults filled in; anything else reads as the default face. */
export function avatarOf(value: Partial<Avatar> | null | undefined): Avatar {
    return {
        shape: value?.shape && SHAPE_IDS.includes(value.shape) ? value.shape : DEFAULT_AVATAR.shape,
        color: value?.color && COLOR_OPTIONS.some((c) => c.id === value.color) ? value.color : DEFAULT_AVATAR.color,
        expression:
            value?.expression && EXPRESSION_IDS.includes(value.expression) ? value.expression : DEFAULT_AVATAR.expression,
    };
}

/**
 * What the face is doing, from what the agent is doing (/team/status tone).
 *
 * Only states that keep the agent's own body: bloub's "thinking" and "sleep"
 * replace the body with dots, and on a roster an agent you cannot recognise
 * is worse than one that does not act out its state. So a notification dot
 * when it needs you, and its resting face otherwise; the status dot beside
 * the face says working or idle. Paused is told by the expression instead
 * (moodForTone).
 */
export function stateForTone(tone: string | null | undefined): StateId {
    return tone === "attention" ? "notify" : "idle";
}

/** The face to show for a tone: a paused agent looks sleepy, whatever it wears. */
export function moodForTone(face: Avatar, tone: string | null | undefined): Avatar {
    return tone === "paused" ? { ...face, expression: "somnolent" } : face;
}

/** A stable starter face per agent, so a roster without choices is not a row of identical faces. */
export function suggestedAvatar(seed: number): Avatar {
    // Not ink or cream: one vanishes on the dark theme, the other on the light.
    const colors = COLOR_OPTIONS.filter((c) => c.id !== "creme" && c.id !== "encre");
    return {
        shape: SHAPE_IDS[seed % SHAPE_IDS.length],
        color: colors[(seed * 7) % colors.length].id,
        expression: DEFAULT_AVATAR.expression,
    };
}

/** The face to draw: the agent's own choice, or its starter face when it has none. */
export function faceOf(workflowId: number, stored: Partial<Avatar> | null | undefined): Avatar {
    return stored ? avatarOf(stored) : suggestedAvatar(workflowId);
}
