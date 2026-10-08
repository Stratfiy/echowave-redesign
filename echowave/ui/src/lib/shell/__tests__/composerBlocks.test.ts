import { describe, expect, it } from "vitest";

import { blockLabel, composeMessage } from "../composerBlocks";

describe("composer blocks", () => {
    it("labels notes by length and voice notes by duration", () => {
        expect(blockLabel({ id: "1", kind: "notes", text: "one two three" })).toBe("Notes (3 words)");
        expect(blockLabel({ id: "1", kind: "notes", text: "one" })).toBe("Notes (1 word)");
        expect(blockLabel({ id: "2", kind: "voice", text: "x", seconds: 42.4 })).toBe("Voice note (0:42)");
        expect(blockLabel({ id: "2", kind: "voice", text: "x", seconds: 75 })).toBe("Voice note (1:15)");
    });

    it("joins the question and each block under its own label", () => {
        expect(
            composeMessage("Draft a reply", [
                { id: "1", kind: "notes", text: "He asked about Friday" },
                { id: "2", kind: "voice", text: "Say yes, after four", seconds: 5 },
            ]),
        ).toBe("Draft a reply\n\nNotes:\nHe asked about Friday\n\nVoice note (0:05), transcribed:\nSay yes, after four");
        expect(composeMessage("  just this  ", [])).toBe("just this");
        expect(composeMessage("", [{ id: "1", kind: "notes", text: "only notes" }])).toBe("Notes:\nonly notes");
    });
});
