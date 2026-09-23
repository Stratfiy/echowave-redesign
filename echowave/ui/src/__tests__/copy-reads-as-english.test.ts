import { readdirSync, readFileSync, statSync } from "fs";
import { join } from "path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

// The product has been renamed by find-and-replace twice ("agent" to "bot",
// then back to "agent" on 23 Sept 2026), and each pass is how "an bot" or
// "a agent" reaches a screen. Copy is the product; a slip like this reads as
// carelessness to the person deciding whether to trust us.
const ROOT = join(__dirname, "..");
const SLIPS = [/\ban bot\b/i, /\ba agent\b/i, /\ba assistant\b/i, /\ban workflow\b/i];

// Agent is the product's one word for what a business hires (founder, 23 Sept
// 2026): "bot" beside "agent" was two names for one thing.
// Internal workflow terminology should still not leak into these user-facing
// labels. These patterns match JSX text nodes, not code identifiers.
const OLD_NAMES = [
    />[^<\n]*\bworkflow runs?\b[^<\n]*</i,
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

    it("keeps internal workflow names out of user-facing labels", () => {
        const offenders: string[] = [];
        for (const file of walk(ROOT)) {
            if (/[\\/]superadmin[\\/]/.test(file)) continue;
            const text = readFileSync(file, "utf8");
            for (const slip of OLD_NAMES) if (slip.test(text)) offenders.push(file.replace(ROOT, "") + ": " + slip);
        }
        expect(offenders).toEqual([]);
    });

    it("calls them agents: no screen text says bot", () => {
        // Parsed, not grepped: JSX text and the strings people read (anything
        // with a space, or a capitalised label). Code names -- BotAvatar,
        // kind: "bot", an icon called Bot -- are not copy and are not seen.
        const offenders: string[] = [];
        for (const file of walk(ROOT)) {
            const src = readFileSync(file, "utf8");
            if (!/\bbots?\b/i.test(src)) continue;
            const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
            const visit = (n: ts.Node) => {
                if (ts.isImportDeclaration(n)) return;
                const isText =
                    ts.isJsxText(n) ||
                    ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isTemplateHead(n) || ts.isTemplateMiddle(n) || ts.isTemplateTail(n)) &&
                        (/\s/.test(n.text) || /^[A-Z][a-z]*s?$/.test(n.text)));
                if (isText && /\bbots?\b/i.test((n as ts.JsxText).text)) offenders.push(`${file.replace(ROOT, "")}: ${(n as ts.JsxText).text.trim().slice(0, 80)}`);
                ts.forEachChild(n, visit);
            };
            visit(sf);
        }
        expect(offenders).toEqual([]);
    });
});
