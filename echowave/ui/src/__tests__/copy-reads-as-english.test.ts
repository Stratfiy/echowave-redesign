import { readdirSync, readFileSync, statSync } from "fs";
import { join } from "path";
import { describe, expect, it } from "vitest";

// The product was renamed from "agent" to "bot" with a find-and-replace,
// which left "an bot" in a dozen screens. Copy is the product; a slip like
// this reads as carelessness to the person deciding whether to trust us.
const ROOT = join(__dirname, "..");
const SLIPS = [/\ban bot\b/i, /\ba agent\b/i, /\ba assistant\b/i, /\ban workflow\b/i];

// The same rename left the old words in visible text: a heading that says
// "Workflow Runs" over a list of calls, "No agents yet" on a screen whose
// sidebar says Bots. These are JSX text nodes, so the `>` and `<` are literal.
const OLD_NAMES = [
    />[^<\n]*\bworkflow runs?\b[^<\n]*</i,
    />[^<\n]*\bno agents yet\b[^<\n]*</i,
    />[^<\n]*\bcreate an agent\b[^<\n]*</i,
    />[^<\n]*\bvoice agent\b[^<\n]*</i,
    />[^<\n]*\binbound workflow\b[^<\n]*</i,
];

function walk(dir: string, out: string[] = []): string[] {
    for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (name === "node_modules" || name === "__tests__") continue;
        if (statSync(p).isDirectory()) walk(p, out);
        else if (/\.(tsx?|jsx?)$/.test(name) && !name.endsWith(".gen.ts")) out.push(p);
    }
    return out;
}

describe("the copy reads as English", () => {
    it("never says 'an bot' or 'a agent'", () => {
        const offenders: string[] = [];
        for (const file of walk(ROOT)) {
            const text = readFileSync(file, "utf8");
            for (const slip of SLIPS) if (slip.test(text)) offenders.push(file.replace(ROOT, "") + ": " + slip);
        }
        expect(offenders).toEqual([]);
    });

    it("never calls a bot an agent or a workflow where a person can read it", () => {
        const offenders: string[] = [];
        for (const file of walk(ROOT)) {
            if (file.includes("/superadmin/")) continue;
            const text = readFileSync(file, "utf8");
            for (const slip of OLD_NAMES) if (slip.test(text)) offenders.push(file.replace(ROOT, "") + ": " + slip);
        }
        expect(offenders).toEqual([]);
    });
});
