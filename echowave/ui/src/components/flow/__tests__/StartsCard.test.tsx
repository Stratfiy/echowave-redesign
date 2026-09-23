import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@xyflow/react", () => ({ Position: { Right: "right" } }));
vi.mock("../nodes/BaseHandle", () => ({ BaseHandle: () => <span data-testid="source-port" /> }));

import { StartsCard } from "../nodes/StartsCard";

describe("the Starts-when card", () => {
    it("lists what starts the agent, saying which are off", () => {
        render(
            <StartsCard
                data={{
                    starts: [
                        { kind: "phone", label: "A call to +91 80 3530 2788", detail: null, active: true },
                        { kind: "routine", label: "Morning report", detail: "Every weekday when you open", active: false },
                    ],
                }}
            />,
        );
        expect(screen.getByLabelText("What starts this agent")).toBeTruthy();
        expect(screen.getByText("A call to +91 80 3530 2788")).toBeTruthy();
        expect(screen.getByText("Every weekday when you open")).toBeTruthy();
        expect(screen.getByText("(off)")).toBeTruthy();
        expect(screen.getByTestId("source-port")).toBeTruthy();
    });

    it("says so when nothing starts it", () => {
        render(<StartsCard data={{ starts: [] }} />);
        expect(screen.getByText(/Nothing starts it yet/)).toBeTruthy();
    });
});
