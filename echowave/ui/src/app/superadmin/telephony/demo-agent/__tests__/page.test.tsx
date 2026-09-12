import { render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DemoAgentPage from "../page";

const api = vi.hoisted(() => ({ read: vi.fn(), set: vi.fn() }));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/client/sdk.gen", () => ({
    readDemoAgentApiV1AdminTelephonyDemoAgentGet: api.read,
    setDemoAgentApiV1AdminTelephonyAgentsWorkflowIdDemoPost: api.set,
}));

beforeEach(() => {
    api.read.mockReset();
    api.set.mockReset();
});

describe("the demo agent screen says which agent it is", () => {
    it("names the agent a prospect will hear", async () => {
        // The screen read "Agent #3". Whoever opens it is deciding which of our
        // own agents represents the product; an id they have to go and look up
        // is not an answer to that.
        api.read.mockResolvedValue({
            data: {
                workflow_id: "3",
                name: "Meera — Decibyl Sales Assistant",
                url: "https://app.decibyl.ai/talk/emb_abc",
                number: "+91 80 3530 2788",
            },
        });
        render(<DemoAgentPage />);
        expect(await screen.findByText("Meera — Decibyl Sales Assistant")).toBeTruthy();
        // The id stays, quietly — it is what the field below takes.
        expect(screen.getByText("#3")).toBeTruthy();
        expect(screen.getByText("Live")).toBeTruthy();
    });

    it("falls back to the id rather than rendering a blank where a name goes", async () => {
        api.read.mockResolvedValue({
            data: { workflow_id: "3", url: "https://app.decibyl.ai/talk/emb_abc" },
        });
        render(<DemoAgentPage />);
        expect(await screen.findByText("Agent #3")).toBeTruthy();
    });

    it("says it is unreachable when neither a link nor a number exists", async () => {
        // The state that leaves every calling role unlisted. It has to be
        // visible here, because the shelf being empty is the only other clue.
        api.read.mockResolvedValue({
            data: { workflow_id: "3", name: "Meera" },
        });
        render(<DemoAgentPage />);
        expect(await screen.findByText("Unreachable")).toBeTruthy();
    });
});
