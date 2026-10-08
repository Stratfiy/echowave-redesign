/**
 * On the live account every visitor to API keys & SDKs read a red banner
 * telling them to "Set MPS_API_URL to a reachable service", above a
 * "Decibyl Service Keys" section whose two create buttons could only fail
 * the same way. That sentence is for whoever runs the box; a customer cannot
 * act on it, and a button that can only 503 is not a feature.
 *
 * When the model service is unreachable the section says so once, calmly,
 * offers nothing to click, and the API keys above it carry on as normal. Any
 * other failure still shows its error, because that one is worth reporting.
 */

import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getApiKeys = vi.hoisted(() => vi.fn());
const getServiceKeys = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    getApiKeysApiV1UserApiKeysGet: getApiKeys,
    getServiceKeysApiV1UserServiceKeysGet: getServiceKeys,
    archiveApiKeyApiV1UserApiKeysApiKeyIdDelete: vi.fn(),
    archiveServiceKeyApiV1UserServiceKeysServiceKeyIdDelete: vi.fn(),
    createApiKeyApiV1UserApiKeysPost: vi.fn(),
    createServiceKeyApiV1UserServiceKeysPost: vi.fn(),
    reactivateApiKeyApiV1UserApiKeysApiKeyIdReactivatePut: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({
    useAuth: () => ({
        user: { id: 1 },
        loading: false,
        getAccessToken: async () => "token",
        redirectToLogin: vi.fn(),
    }),
}));
vi.mock("@/context/AppConfigContext", () => ({
    useAppConfig: () => ({ config: { deploymentMode: "oss" } }),
}));
vi.mock("@/components/ConfirmDialog", () => ({
    useConfirm: () => ({ confirm: vi.fn(), dialog: null }),
}));

import APIKeysPage from "../page";

const apiKey = {
    id: 1,
    name: "Default API Key",
    key_prefix: "dcb_5mf3",
    environment: "production",
    is_active: true,
    created_at: "2026-09-18T03:24:00Z",
    last_used_at: null,
    archived_at: null,
};

const unreachable = {
    error: {
        detail:
            "Service keys are issued by the Decibyl model service, which this " +
            "deployment cannot reach. Set MPS_API_URL to a reachable service to use this screen.",
    },
    response: { status: 503 },
};

beforeEach(() => {
    vi.clearAllMocks();
    getApiKeys.mockResolvedValue({ data: [apiKey] });
});

describe("API keys when the model service is unreachable", () => {
    it("says so once, without an operator's setting or a button that can only fail", async () => {
        getServiceKeys.mockResolvedValue(unreachable);
        render(<APIKeysPage />);

        await waitFor(() => expect(screen.getByText("Default API Key")).toBeTruthy());
        await waitFor(() =>
            expect(screen.getByText(/not available on this deployment/i)).toBeTruthy(),
        );

        expect(screen.queryByText(/MPS_API_URL/)).toBeNull();
        expect(screen.queryByRole("button", { name: /Create Service Key/i })).toBeNull();
        expect(screen.queryByRole("button", { name: /Create Your First Service Key/i })).toBeNull();
        expect(screen.queryByText("No service keys found")).toBeNull();
    });

    it("still reports a failure that is not that one", async () => {
        getServiceKeys.mockResolvedValue({ error: { detail: "boom" }, response: { status: 500 } });
        render(<APIKeysPage />);

        await waitFor(() => expect(screen.getByText("boom")).toBeTruthy());
        expect(screen.queryByText(/not available on this deployment/i)).toBeNull();
    });
});
