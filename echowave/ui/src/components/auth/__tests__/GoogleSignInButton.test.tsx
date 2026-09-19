import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GoogleSignInButton } from "../GoogleSignInButton";

vi.mock("@/context/AppConfigContext", () => ({
    useAppConfig: () => ({ config: { backendApiEndpoint: "" } }),
}));

describe("the Google button and the sentence that belongs to it", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it("carries its own click-wrap line, so the two cannot come apart", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true } as Response));
        render(<GoogleSignInButton label="Sign up with Google" notice={<p>Continuing with Google means you agree.</p>} />);
        await waitFor(() => expect(screen.getByTestId("google-signin-button")).toBeTruthy());
        expect(screen.getByText(/Continuing with Google/)).toBeTruthy();
    });

    it("takes the line with it when the deployment has no Google", async () => {
        // The button hides itself where Google is not configured -- an
        // air-gapped install, or a dev box with no API. The notice used to sit
        // in the page beside it and stayed behind: a sentence about a button
        // that is not on the screen.
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false } as Response));
        render(<GoogleSignInButton label="Sign up with Google" notice={<p>Continuing with Google means you agree.</p>} />);
        await waitFor(() => expect(screen.queryByTestId("google-signin-button")).toBeNull());
        expect(screen.queryByText(/Continuing with Google/)).toBeNull();
    });
});
