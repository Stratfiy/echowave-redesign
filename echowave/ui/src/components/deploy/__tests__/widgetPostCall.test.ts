/**
 * The post-call card's guards, driven through the real widget.
 *
 * `public/embed/decibyl-widget.js` is a standalone IIFE served to customers'
 * sites, so it cannot be imported. It is evaluated here the way a browser
 * evaluates it — a script tag, a stubbed config endpoint, and then whatever
 * `getState()` reports — which means these assertions run through the actual
 * fetch and merge path rather than through a copy of the logic that could
 * agree with itself while the shipped file disagrees.
 *
 * Two of these are the reason the file has a normaliser at all:
 *
 * - A `javascript:` CTA becomes an href on the customer's own page. It is set
 *   from their own dashboard, but an account is precisely the thing that gets
 *   phished, and "they did it to themselves" is not a defence.
 * - `minSeconds` arriving as something unreadable must not collapse to 0. That
 *   would silently turn off the one guard separating "had a conversation" from
 *   "clicked and closed", and the symptom is a popup on a customer's site.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const WIDGET_SOURCE = readFileSync(
    join(process.cwd(), "public/embed/decibyl-widget.js"),
    "utf-8"
);

type PostCall = {
    enabled: boolean;
    headline: string;
    body: string;
    ctaText: string;
    ctaUrl: string;
    minSeconds: number;
};

declare global {

    var DecibylWidget: {
        getState: () => {
            // workflowId is what the merge sets, and what bootWidget waits on.
            config: { postCall: PostCall; workflowId?: number };
        };
    };
}

/** Run the widget against a config endpoint returning these settings. */
async function bootWidget(settings: Record<string, unknown>): Promise<PostCall> {
    document.head.innerHTML = "";
    document.body.innerHTML = "";

    const script = document.createElement("script");
    script.src =
        "https://app.decibyl.ai/embed/decibyl-widget.js?token=tok_test&apiEndpoint=https://api.decibyl.test";
    document.head.appendChild(script);

    vi.stubGlobal(
        "fetch",
        vi.fn(async () => ({
            ok: true,
            status: 200,
            json: async () => ({ workflow_id: 1, settings }),
        }))
    );

    // The IIFE reads document.currentScript, which jsdom leaves null outside a
    // real script execution; the widget's own fallback query selector is what
    // answers, and it is the same path a deferred script takes in a browser.
    new Function(WIDGET_SOURCE)();

    // Wait on workflowId, not on postCall. DEFAULT_CONFIG already carries a
    // postCall object and is spread into state.config *before* the fetch, so
    // waiting for that field to exist would let every assertion below pass
    // against the defaults whether or not the merge ever ran. workflowId is
    // only ever set by the merge. (This is not hypothetical: the first version
    // of this file waited on postCall and reported four green tests measuring
    // nothing.)
    await vi.waitFor(() => {
        const state = globalThis.DecibylWidget?.getState();
        if (!state?.config?.workflowId) throw new Error("config not merged yet");
    });

    return globalThis.DecibylWidget.getState().config.postCall;
}

const VALID = {
    enabled: true,
    headline: "Want one of these?",
    body: "That was a Decibyl agent.",
    ctaText: "Start free",
    ctaUrl: "https://app.decibyl.ai/auth/signup",
    minSeconds: 15,
};

describe("the post-call card only carries a link a browser should follow", () => {
    beforeEach(() => {
        // @ts-expect-error -- the widget defines it; each boot starts clean.
        delete globalThis.DecibylWidget;
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("keeps an https CTA", async () => {
        const postCall = await bootWidget({ postCall: VALID });
        expect(postCall.ctaUrl).toBe("https://app.decibyl.ai/auth/signup");
        expect(postCall.ctaText).toBe("Start free");
    });

    it("drops a javascript: CTA and keeps the message", async () => {
        const postCall = await bootWidget({

            postCall: { ...VALID, ctaUrl: "javascript:alert(document.cookie)" },
        });
        expect(postCall.ctaUrl).toBe("");
        // The card still has something to say. Dropping the whole card because
        // one field was bad would hide the problem rather than defuse it.
        expect(postCall.headline).toBe("Want one of these?");
    });

    it("drops a data: CTA too", async () => {
        const postCall = await bootWidget({
            postCall: { ...VALID, ctaUrl: "data:text/html,<script>1</script>" },
        });
        expect(postCall.ctaUrl).toBe("");
    });
});

describe("the card waits for a call that actually happened", () => {
    beforeEach(() => {
        // @ts-expect-error -- see above.
        delete globalThis.DecibylWidget;
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("keeps a configured threshold", async () => {
        const postCall = await bootWidget({ postCall: VALID });
        expect(postCall.minSeconds).toBe(15);
    });

    it("falls back to the default when the threshold is unreadable", async () => {
        const postCall = await bootWidget({
            postCall: { ...VALID, minSeconds: "soon" },
        });
        // 10, not 0. Zero would show the card after a call that never
        // connected, which is the failure this guard exists to prevent.
        expect(postCall.minSeconds).toBe(10);
    });

    it("allows an explicit zero", async () => {
        const postCall = await bootWidget({
            postCall: { ...VALID, minSeconds: 0 },
        });
        expect(postCall.minSeconds).toBe(0);
    });
});

describe("an embed that never configured this is unchanged", () => {
    beforeEach(() => {
        // @ts-expect-error -- see above.
        delete globalThis.DecibylWidget;
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("is off when settings carry no postCall at all", async () => {
        const postCall = await bootWidget({ buttonText: "Talk to us" });
        expect(postCall.enabled).toBe(false);
    });

    it("is off unless enabled is exactly true", async () => {
        const postCall = await bootWidget({
            postCall: { ...VALID, enabled: "yes" },
        });
        expect(postCall.enabled).toBe(false);
    });
});
