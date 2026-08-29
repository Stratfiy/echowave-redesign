import { describe, expect, it } from "vitest";

import { priceableFromV2, pricedStack } from "../pricedStack";

const UPSTREAM = {
    llm: { provider: "openai", model: "gpt-4.1-mini" },
    stt: { provider: "sarvam", model: "saaras:v3" },
    tts: { provider: "sarvam", model: "bulbul:v3" },
};

describe("pricedStack", () => {
    it("passes a bring-your-own-key stack through unchanged", () => {
        const stack = pricedStack({
            stt: { provider: "deepgram", model: "nova-3" },
            llm: { provider: "openai", model: "gpt-4.1" },
            tts: { provider: "elevenlabs", model: "eleven_flash_v2_5" },
        });

        expect(stack).toMatchObject({
            stt_provider: "deepgram",
            llm_provider: "openai",
            tts_provider: "elevenlabs",
            tts_model: "eleven_flash_v2_5",
        });
    });

    it("resolves a managed slot to the vendor that actually serves it", () => {
        // "decibyl" is a billing arrangement, not a vendor. Left as-is the
        // estimator reports the managed option as the one it cannot price,
        // which is the opposite of true.
        const stack = pricedStack(
            { llm: { provider: "decibyl", model: "default" } },
            UPSTREAM,
        );

        expect(stack.llm_provider).toBe("openai");
        expect(stack.llm_model).toBe("gpt-4.1-mini");
    });

    it("leaves a real provider alone even when an upstream map is present", () => {
        const stack = pricedStack(
            { tts: { provider: "cartesia", model: "sonic-3" } },
            UPSTREAM,
        );

        expect(stack.tts_provider).toBe("cartesia");
        expect(stack.tts_model).toBe("sonic-3");
    });

    it("drops the transcriber and voice for speech-to-speech", () => {
        // One model hears and speaks; pricing the empty slots beside it would
        // charge for components that never run.
        const stack = pricedStack({
            llm: { provider: "openai", model: "gpt-realtime-2" },
            stt: { provider: "deepgram", model: "nova-3" },
            tts: { provider: "elevenlabs", model: "eleven_flash_v2_5" },
            is_realtime: true,
        });

        expect(stack.stt_provider).toBeNull();
        expect(stack.tts_provider).toBeNull();
        expect(stack.llm_provider).toBe("openai");
    });

    it("reports an unset slot as null rather than empty string", () => {
        const stack = pricedStack(null);

        expect(stack.llm_provider).toBeNull();
        expect(stack.telephony_provider).toBeNull();
    });

    it("carries telephony through when the caller knows it", () => {
        expect(pricedStack(null, undefined, "twilio").telephony_provider).toBe("twilio");
    });
});

describe("priceableFromV2", () => {
    it("flattens a managed configuration to decibyl slots", () => {
        const priceable = priceableFromV2({
            mode: "decibyl",
            decibyl: { llm_tier: "default", stt_tier: "default", tts_tier: "default" },
        });

        expect(priceable?.llm?.provider).toBe("decibyl");
        expect(priceable?.is_realtime).toBe(false);
    });

    it("round-trips a managed configuration into real vendors", () => {
        const stack = pricedStack(
            priceableFromV2({ mode: "decibyl", decibyl: { llm_tier: "default" } }),
            UPSTREAM,
        );

        expect(stack.llm_provider).toBe("openai");
    });

    it("flattens a pipeline configuration", () => {
        const priceable = priceableFromV2({
            mode: "byok",
            byok: {
                mode: "pipeline",
                pipeline: {
                    llm: { provider: "groq", model: "llama-3.3-70b-versatile" },
                    stt: { provider: "deepgram", model: "nova-3" },
                    tts: { provider: "rime", model: "mistv2" },
                },
            },
        });

        expect(priceable?.llm?.provider).toBe("groq");
        expect(priceable?.tts?.provider).toBe("rime");
        expect(priceable?.is_realtime).toBe(false);
    });

    it("flattens a speech-to-speech configuration and marks it realtime", () => {
        const priceable = priceableFromV2({
            mode: "byok",
            byok: {
                mode: "realtime",
                realtime: { llm: { provider: "openai_realtime", model: "gpt-realtime-2" } },
            },
        });

        expect(priceable?.is_realtime).toBe(true);
        expect(pricedStack(priceable).stt_provider).toBeNull();
    });

    it("returns nothing for an absent configuration", () => {
        expect(priceableFromV2(null)).toBeNull();
        expect(priceableFromV2(undefined)).toBeNull();
    });
});
