import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
    avatarOf,
    COLOR_LABEL,
    COLOR_OPTIONS,
    DEFAULT_AVATAR,
    EXPRESSION_IDS,
    EXPRESSION_LABEL,
    faceOf,
    moodForTone,
    SHAPE_IDS,
    SHAPE_LABEL,
    stateForTone,
    suggestedAvatar,
} from "../avatar";

/** The id lists the server accepts, read from api/schemas/agent_avatar.py. */
function serverIds(name: string): string[] {
    // Tests run from ui/, and the API sits beside it.
    const source = readFileSync(resolve(process.cwd(), "../api/schemas/agent_avatar.py"), "utf8");
    const block = source.match(new RegExp(`${name} = Literal\\[([^\\]]*)\\]`))?.[1] ?? "";
    return [...block.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

describe("the server and the engine agree on every id", () => {
    it.each([
        ["AvatarShape", SHAPE_IDS],
        ["AvatarColor", COLOR_OPTIONS.map((c) => c.id)],
        ["AvatarExpression", EXPRESSION_IDS],
    ])("%s", (name, ids) => {
        expect(new Set(serverIds(name))).toEqual(new Set(ids));
    });

    it("has a label for every id", () => {
        for (const id of SHAPE_IDS) expect(SHAPE_LABEL[id]).toBeTruthy();
        for (const { id } of COLOR_OPTIONS) expect(COLOR_LABEL[id]).toBeTruthy();
        for (const id of EXPRESSION_IDS) expect(EXPRESSION_LABEL[id]).toBeTruthy();
    });
});

describe("avatarOf", () => {
    it("fills in what is missing and replaces what it cannot draw", () => {
        expect(avatarOf(null)).toEqual(DEFAULT_AVATAR);
        expect(avatarOf({ color: "vert" })).toEqual({ ...DEFAULT_AVATAR, color: "vert" });
        expect(avatarOf({ shape: "star", color: "#fff", expression: "angry" })).toEqual(DEFAULT_AVATAR);
    });
});

describe("faceOf", () => {
    it("uses the agent's choice when it has one", () => {
        expect(faceOf(3, { shape: "nuage", color: "rose", expression: "fier" })).toEqual({
            shape: "nuage",
            color: "rose",
            expression: "fier",
        });
    });

    it("gives an agent without one a stable starter face, varied across agents", () => {
        expect(faceOf(5, null)).toEqual(suggestedAvatar(5));
        expect(faceOf(5, null)).toEqual(faceOf(5, undefined));
        const faces = new Set([1, 2, 3, 4, 5, 6].map((id) => JSON.stringify(faceOf(id, null))));
        expect(faces.size).toBe(6);
        // never a colour that vanishes on one of the two themes
        for (let id = 0; id < 40; id++) expect(["encre", "creme"]).not.toContain(faceOf(id, null).color);
    });
});

describe("moodForTone", () => {
    it("makes a paused agent sleepy and leaves the rest as chosen", () => {
        const face = { shape: "nuage", color: "rose", expression: "fier" };
        expect(moodForTone(face, "paused")).toEqual({ ...face, expression: "somnolent" });
        expect(moodForTone(face, "working")).toBe(face);
    });
});

describe("stateForTone", () => {
    it.each([
        ["attention", "notify"],
        ["working", "idle"],
        ["paused", "idle"],
        ["idle", "idle"],
        [undefined, "idle"],
    ])("draws %s as %s", (tone, state) => {
        expect(stateForTone(tone)).toBe(state);
    });
});
