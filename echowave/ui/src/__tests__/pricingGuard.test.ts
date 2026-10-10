import { readdirSync, readFileSync, statSync } from "fs";
import { join, sep } from "path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { PRICES_SHOWN } from "@/lib/pricing";

// No pricing is shown to users. Decided by the founder on 9 Oct 2026, with
// the positioning "an intelligent agent that grows and evolves with you"
// (AGENTS.md, "Product decisions that are already settled"). This fails when
// a string a person can read carries a price, a per-minute rate, "pricing",
// "upgrade" or a plan name -- unless it sits behind PRICES_SHOWN
// (lib/pricing.ts) or in a file allowlisted below with its reason.
//
// Parsed, not grepped, like copy-reads-as-english: JSX text and the strings
// people read. Comments, identifiers and API paths are not copy.

const ROOT = join(__dirname, "..");

/** Files and folders allowed to carry price copy, and why. */
const ALLOWED: { path: string; why: string }[] = [
    // Staff and admin cost screens: not user facing (category C).
    { path: "app/superadmin/", why: "staff cost and billing screens" },
    { path: "components/superadmin/", why: "staff cost and billing screens" },
    { path: "components/billing/ManagedMarkupCard.tsx", why: "staff markup card" },
    { path: "components/billing/MarkupOverridesCard.tsx", why: "staff markup overrides" },
    { path: "components/billing/ProviderCatalogue.tsx", why: "staff provider rate catalogue" },
    { path: "lib/billing/", why: "money formatters, no copy of their own" },
    // The checkout is gone (the plans, add-credit, autopay, balance chip,
    // gift menu and trial box files were deleted, and /numbers no longer
    // quotes a rental), so their entries are gone from this list too. The
    // Documents page needs none: it carries no price copy.
    { path: "app/partner/", why: "partner commissions, an agreement with a partner" },
    // Price components rendered only behind PRICES_SHOWN at their call site.
    { path: "components/CostPerMinuteBar.tsx", why: "rendered only behind PRICES_SHOWN" },
    { path: "components/agent/EstimatedRate.tsx", why: "rendered only behind PRICES_SHOWN" },
    { path: "app/workflow/[workflowId]/components/SpendCard.tsx", why: "rendered only behind PRICES_SHOWN" },
    { path: "lib/chatPresets.ts", why: "replyCostLabel, called only behind PRICES_SHOWN" },
    // The person's own money, not a Decibyl price.
    { path: "components/helpers/WhoOwesMe.tsx", why: "the person's own invoices" },
    { path: "components/ApprovalsSection.tsx", why: "approval thresholds for the person's payments" },
    { path: "components/shell/ActionPreview.tsx", why: "the amount of a payment the person approves" },
    { path: "components/today/ApprovalDock.tsx", why: "the amount of a payment the person approves" },
    { path: "components/reach/", why: "orders the person places" },
    { path: "app/workflow/[workflowId]/components/EscalationSettingsCard.tsx", why: "a business's refund limit" },
    { path: "components/settings/pages/PersonalizationSettings.tsx", why: "an example of a tax reminder" },
    {
        path: "app/workflow/[workflowId]/components/workflow-tester/AiSimulatorPlaceholder.tsx",
        why: "a test caller's script about the business's own prices",
    },
];

const PATTERNS: { name: string; re: RegExp; sentence?: boolean }[] = [
    { name: "₹", re: /₹/ },
    { name: "/min", re: /(\d|\}|credits?|cr)\s*\/\s*min\b/i },
    { name: "credit rate", re: /\b(\d+|\{\})\s+credits?\s+(a|per|each)\b|\bcredits? (a|per) (minute|message|reply|run)\b/i },
    { name: "cr/reply", re: /\bcr\/reply\b/i },
    { name: "pricing", re: /\bpricing\b/i, sentence: true },
    { name: "upgrade", re: /\bupgrade\b/i, sentence: true },
    { name: "upgrade button", re: /^\s*Upgrade\b/ },
    {
        name: "plan name",
        re: /\b(free|trial|everyday|business|growth|scale|pro|starter|campus builder|personal|go)\s+plan\b/i,
        sentence: true,
    },
    {
        name: "plans",
        re: /\b(see|choose|compare|pick|start|cancel|change)\s+(a |the |this |your )?plans?\b|\bon (your|the|this) plan\b|\bplan's (credits|price|cap)\b|\{\} plans?\b|\bpast your plan's\b/i,
        sentence: true,
    },
];

const allowed = (rel: string) => ALLOWED.some((a) => rel.split(sep).join("/").startsWith(a.path));

function walk(dir: string, out: string[] = []): string[] {
    for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (name === "node_modules" || name === "__tests__" || name === "client") continue;
        if (statSync(p).isDirectory()) walk(p, out);
        else if (/\.(tsx?|jsx?)$/.test(name) && !/\.test\.|\.gen\.ts$/.test(name)) out.push(p);
    }
    return out;
}

/** The text a node shows, or null when it is not copy. */
function copyOf(n: ts.Node): string | null {
    if (ts.isJsxText(n)) return n.text;
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) return n.text;
    if (ts.isTemplateExpression(n)) {
        return n.head.text + n.templateSpans.map((s) => "{}" + s.literal.text).join("");
    }
    return null;
}

/** Behind the switch: `PRICES_SHOWN && …` or `PRICES_SHOWN ? … : …`. */
function gated(n: ts.Node): boolean {
    if (ts.isBinaryExpression(n) && n.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) {
        return /\bPRICES_SHOWN\b/.test(n.left.getText());
    }
    return false;
}

function offenders(): string[] {
    const found: string[] = [];
    for (const file of walk(ROOT)) {
        const rel = file.slice(ROOT.length + 1);
        if (allowed(rel)) continue;
        const src = readFileSync(file, "utf8");
        const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
        const visit = (n: ts.Node) => {
            if (ts.isImportDeclaration(n) || ts.isExportDeclaration(n)) return;
            if (gated(n)) return;
            if (ts.isConditionalExpression(n) && /\bPRICES_SHOWN\b/.test(n.condition.getText())) {
                // Only the "shown" branch may carry a price.
                visit(n.whenFalse);
                return;
            }
            const text = copyOf(n);
            if (text !== null) {
                for (const p of PATTERNS) {
                    if (p.sentence && !/\s/.test(text.trim())) continue;
                    if (p.re.test(text)) found.push(`${rel}: [${p.name}] ${text.trim().replace(/\s+/g, " ").slice(0, 90)}`);
                }
                if (ts.isTemplateExpression(n)) {
                    n.templateSpans.forEach((s) => visit(s.expression));
                }
                return;
            }
            ts.forEachChild(n, visit);
        };
        visit(sf);
    }
    return found;
}

describe("no pricing is shown to users (founder, 9 Oct 2026)", () => {
    it("keeps the switch off", () => {
        // Turning prices back on is the founder's decision, with new copy.
        expect(PRICES_SHOWN).toBe(false);
    });

    it("has no price, rate, plan name or upgrade prompt in user-facing copy", () => {
        expect(offenders()).toEqual([]);
    });

    it("catches the copy it is meant to catch", () => {
        // A guard that matches nothing passes for ever. These are the shapes
        // the repositioning removed.
        const samples = [
            "₹999 a month",
            "{}/min",
            "{} credits/min",
            "See plans",
            "Choose a plan to switch them back on.",
            "in mind on the {} plan. The Everyday plan keeps more.",
            "or upgrade for a larger allowance",
            "Custom pricing for volume",
            "Upgrade",
            "in mind on the {} plan.",
            "{} credits a message",
            "1 credit per {} typed pages",
            "Past your plan's {} pages",
        ];
        for (const sample of samples) {
            const hit = PATTERNS.some((p) => (!p.sentence || /\s/.test(sample.trim())) && p.re.test(sample));
            expect(hit, sample).toBe(true);
        }
    });

    it("lets ordinary words through", () => {
        for (const sample of ["Help me plan today", "Your plan for this week", "Everyday", "Learning plan", "upgrade"]) {
            const hit = PATTERNS.some((p) => (!p.sentence || /\s/.test(sample.trim())) && p.re.test(sample));
            expect(hit, sample).toBe(false);
        }
    });
});
