import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ServiceConfigurationForm } from "@/components/ServiceConfigurationForm";

const catalogue = {
    tts: [{
        provider: "elevenlabs",
        model: "eleven_flash_v2_5",
        label: "Flash",
        paise_per_minute: 400,
        approximate: false,
    }],
};

const getCatalogueMock = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    getCarriageBasisApiV1AgentOptionsCarriageGet: vi.fn(async () => ({ data: {} })),
    getCatalogueApiV1AgentOptionsCatalogueGet: getCatalogueMock,
    getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet: vi.fn(),
}));

vi.mock("@/components/CostPerMinuteBar", () => ({
    CostPerMinuteBar: () => null,
}));

vi.mock("@/components/VoiceSelector", () => ({
    VoiceSelector: () => null,
}));

vi.mock("@/context/UserConfigContext", () => ({
    useUserConfig: () => ({ userConfig: null }),
}));

const schema = (model: string) => ({
    properties: {
        provider: { type: "string" },
        model: { type: "string", default: model },
    },
});

describe("ServiceConfigurationForm", () => {
    it("saves a catalogued ElevenLabs model on Decibyl's key", async () => {
        getCatalogueMock.mockResolvedValue({ data: { catalogue } });
        vi.stubGlobal("ResizeObserver", class {
            observe() {}
            unobserve() {}
            disconnect() {}
        });
        const onSave = vi
            .fn<(config: Record<string, unknown>) => Promise<void>>()
            .mockResolvedValue(undefined);
        render(
            <ServiceConfigurationForm
                mode="global"
                keysFromVault
                keysHeld={{}}
                platformKeyProviders={{ tts: ["elevenlabs"] }}
                configurationDefaults={{
                    llm: { decibyl: schema("default") },
                    tts: { elevenlabs: schema("eleven_flash_v2_5") },
                    stt: { decibyl: schema("default") },
                    embeddings: {},
                    realtime: {},
                    default_providers: {
                        llm: "decibyl",
                        tts: "elevenlabs",
                        stt: "decibyl",
                    },
                }}
                initialConfig={{
                    is_realtime: false,
                    llm: { provider: "decibyl", model: "default", api_key: "" },
                    tts: {
                        provider: "elevenlabs",
                        model: "eleven_flash_v2_5",
                        api_key: "",
                        use_platform_key: false,
                    },
                    stt: { provider: "decibyl", model: "default", api_key: "" },
                }}
                onSave={onSave}
            />,
        );

        await waitFor(() => expect(getCatalogueMock).toHaveBeenCalledOnce());
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 50));
        });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));

        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0]).toMatchObject({
            tts: {
                provider: "elevenlabs",
                model: "eleven_flash_v2_5",
                use_platform_key: true,
            },
        });
    });
});
