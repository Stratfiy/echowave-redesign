/**
 * The bar above a bot's tabs can carry the chat's buttons. They used to sit
 * on the tab strip's row, and at 1280px the two did not fit: "Advanced" and
 * "Share" were clipped behind About / Test / Share with no scrollbar to say
 * so. The bar had the room all along.
 */

import { render, screen, within } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { AgentHeader } from "../AgentHeader";

describe("the agent header", () => {
    it("carries the actions it is given, beside the name", () => {
        render(<AgentHeader workflowId={7} name="Front desk" actions={<button>About</button>} />);
        const bar = screen.getByRole("banner");
        expect(within(bar).getByText("Front desk")).toBeTruthy();
        expect(within(bar).getByRole("button", { name: "About" })).toBeTruthy();
    });

    it("adds nothing when there are none", () => {
        render(<AgentHeader workflowId={7} name="Front desk" />);
        expect(screen.getAllByRole("button")).toHaveLength(1); // the way back
    });
});
