/**
 * The customer's model picker, and the two things it must never get wrong.
 *
 * **Who pays the vendor.** A slot showing a managed price and saving
 * `use_platform_key: false` resolves no credential at dial time: it saves
 * cleanly, builds an agent, and fails on the first live call. The rule that
 * decides it is `runsOnPlatformKey`, and it is asserted here rather than
 * inferred from a rendered screen because it is a billing rule, not a layout.
 *
 * **What a number means.** Every price on this screen is one slot's
 * contribution to a minute, and some of them are the vendor's default rate
 * rather than that model's. Two models showing the same figure reads as a
 * broken calculator unless it is said, so it is said.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
    CatalogueModelSelect,
    type CatalogueOption,
    findCatalogueOption,
    OwnKeyModelSelect,
    priceCaption,
    runsOnPlatformKey,
} from "../ModelSlotSelect";

const discover = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    discoverModelsApiV1ProviderKeysModelsGet: discover,
}));

function option(overrides: Partial<CatalogueOption> = {}): CatalogueOption {
    return {
        provider: "openai",
        model: "gpt-4.1",
        label: "Smart",
        paise_per_minute: 71,
        approximate: false,
        ...overrides,
    };
}

describe("who pays the vendor", () => {
    it("is us when we sell it and the account holds no key of its own", () => {
        expect(
            runsOnPlatformKey({ sells: true, holdsOwnKey: false, saved: false }),
        ).toBe(true);
    });

    it("is them when we do not sell that vendor at all", () => {
        // The escape hatch. Nothing about holding a key, or about what the
        // stored configuration says, can make us the payer for a vendor we
        // never put on sale.
        expect(
            runsOnPlatformKey({ sells: false, holdsOwnKey: true, saved: false }),
        ).toBe(false);
        expect(
            runsOnPlatformKey({ sells: false, holdsOwnKey: false, saved: true }),
        ).toBe(false);
    });

    it("leaves a stored own-key choice alone on a vendor we also sell", () => {
        // Moving a live agent onto our bill is a decision, and it stays theirs.
        expect(
            runsOnPlatformKey({ sells: true, holdsOwnKey: true, saved: false }),
        ).toBe(false);
    });

    it("keeps a stored managed choice managed even once they add their own key", () => {
        expect(
            runsOnPlatformKey({ sells: true, holdsOwnKey: true, saved: true }),
        ).toBe(true);
    });
});

describe("what the price says", () => {
    it("names the slot, not the call", () => {
        expect(priceCaption(option())).toBe("₹0.71 a minute for this slot");
    });

    it("says so when the figure is the vendor's default rather than this model's", () => {
        const caption = priceCaption(option({ approximate: true }));
        expect(caption).toContain("About");
        expect(caption).toContain("default rate");
    });

    it("never turns a missing rate into a number", () => {
        // Zero would read as free, which is the one thing it never means.
        expect(priceCaption(option({ paise_per_minute: null }))).toBe(
            "No price on file for this model.",
        );
        expect(priceCaption(option({ paise_per_minute: null }))).not.toContain("₹0");
    });

    it("matches a saved slot to its catalogue entry only on both halves", () => {
        const options = [option(), option({ provider: "google", model: "gpt-4.1" })];
        expect(findCatalogueOption(options, "openai", "gpt-4.1")?.provider).toBe("openai");
        expect(findCatalogueOption(options, "openai", "o3-pro")).toBeUndefined();
        expect(findCatalogueOption(options, undefined, "gpt-4.1")).toBeUndefined();
    });
});

describe("the managed picker", () => {
    it("prices the model that is selected", () => {
        render(
            <CatalogueModelSelect
                options={[option(), option({ model: "gpt-4.1-mini", label: "Normal", paise_per_minute: 14 })]}
                value="gpt-4.1-mini"
                onChange={vi.fn()}
            />,
        );

        expect(screen.getByText("₹0.14 a minute for this slot")).toBeDefined();
    });

    it("keeps a withdrawn model selected and explains it", () => {
        // Silently blanking somebody's stored choice is how an agent changes
        // behaviour without anyone deciding to change it.
        render(
            <CatalogueModelSelect
                options={[option()]}
                value="gpt-4o"
                onChange={vi.fn()}
            />,
        );

        expect(screen.getByText(/no longer offer this model/i)).toBeDefined();
    });
});

describe("the own-key picker", () => {
    beforeEach(() => {
        discover.mockReset();
    });

    it("says who is billing them, and how many we hid because we sell them", async () => {
        discover.mockResolvedValue({
            data: {
                models: [{ id: "o3-pro", suggested: false }],
                source: "vendor",
                note: null,
                allow_custom_model: true,
                provided_by_decibyl: 2,
            },
        });

        render(
            <OwnKeyModelSelect
                component="llm"
                provider="openai"
                value="o3-pro"
                onChange={vi.fn()}
            />,
        );

        await waitFor(() => {
            expect(screen.getByText(/billed by openai to your account/i)).toBeDefined();
        });
        expect(screen.getByText(/2 more are hidden/i)).toBeDefined();
    });

    it("falls back to typing the model id when the vendor cannot be asked", async () => {
        // A key that works and a list endpoint that does not must not strand
        // the customer behind our ignorance.
        discover.mockResolvedValue({ error: { detail: "upstream refused" } });

        render(
            <OwnKeyModelSelect
                component="stt"
                provider="deepgram"
                value=""
                onChange={vi.fn()}
            />,
        );

        await waitFor(() => {
            expect(screen.getByPlaceholderText("Model id")).toBeDefined();
        });
        expect(screen.getByText(/could not read the model list/i)).toBeDefined();
    });

    it("does not offer a list of nothing", async () => {
        discover.mockResolvedValue({
            data: {
                models: [],
                source: "vendor",
                note: null,
                allow_custom_model: false,
                provided_by_decibyl: 5,
            },
        });

        render(
            <OwnKeyModelSelect
                component="llm"
                provider="openai"
                value=""
                onChange={vi.fn()}
            />,
        );

        await waitFor(() => {
            expect(
                screen.getByText(/reaches nothing we don't already sell/i),
            ).toBeDefined();
        });
    });
});
