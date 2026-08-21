/**
 * The provider-keys form's one real decision: does a key fan out to every
 * component the vendor serves, or only to the one picked.
 *
 * The screen used to lead with "which component" before the vendor was even
 * named, which made adding one Sarvam key feel like adding three — nothing on
 * the form said Sarvam already covered the other two. `planKeySubmission` is
 * what the redesigned form asks instead, and these are the four shapes of
 * answer it can give.
 */

import { describe, expect, it } from "vitest";

import { planKeySubmission } from "../providerKeys";

describe("a vendor that serves more than one component", () => {
    it("fans out by default", () => {
        const plan = planKeySubmission({
            provider: "Sarvam",
            component: "llm",
            matchedComponents: ["llm", "stt", "tts"],
            applyToAll: true,
        });

        expect(plan).toEqual({
            provider: "sarvam",
            component: "llm",
            apply_to_all_components: true,
        });
    });

    it("narrows to the picked component when told to", () => {
        const plan = planKeySubmission({
            provider: "sarvam",
            component: "tts",
            matchedComponents: ["llm", "stt", "tts"],
            applyToAll: false,
        });

        expect(plan).toEqual({
            provider: "sarvam",
            component: "tts",
            apply_to_all_components: false,
        });
    });
});

describe("a vendor that serves exactly one component", () => {
    it("never fans out — there is nothing else to apply it to", () => {
        const plan = planKeySubmission({
            provider: "groq",
            component: "llm",
            matchedComponents: ["llm"],
            applyToAll: true,
        });

        expect(plan.apply_to_all_components).toBe(false);
        expect(plan.component).toBe("llm");
    });
});

describe("a vendor the registry has never heard of", () => {
    it("falls back to whichever component was picked by hand, and never fans out", () => {
        const plan = planKeySubmission({
            provider: "acme-voice",
            component: "stt",
            matchedComponents: undefined,
            applyToAll: true,
        });

        expect(plan).toEqual({
            provider: "acme-voice",
            component: "stt",
            apply_to_all_components: false,
        });
    });
});

describe("what reaches the wire", () => {
    it("lowercases and trims the provider, whatever the form field held", () => {
        const plan = planKeySubmission({
            provider: "  OpenAI  ",
            component: "llm",
            matchedComponents: ["llm", "stt", "tts"],
            applyToAll: true,
        });

        expect(plan.provider).toBe("openai");
    });
});
