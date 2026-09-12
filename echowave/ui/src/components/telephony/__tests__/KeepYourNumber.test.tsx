import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { KeepYourNumber } from "../KeepYourNumber";

describe("keeping your own number", () => {
    it("leads with the case a front desk actually wants", () => {
        // Not "forward everything". A receptionist keeps answering; the agent
        // catches the second caller and the ones nobody reached.
        render(<KeepYourNumber agentNumber="+911234567890" />);
        fireEvent.click(screen.getByRole("button", { name: /show me how/i }));
        expect(screen.getByText("When the line is busy")).toBeTruthy();
        expect(screen.getAllByText(/most front desks use this/i).length).toBe(2);
    });

    it("puts the agent's number into every code", () => {
        render(<KeepYourNumber agentNumber="+911234567890" />);
        fireEvent.click(screen.getByRole("button", { name: /show me how/i }));
        expect(screen.getByText("**67*+911234567890#")).toBeTruthy();
        expect(screen.getByText("**21*+911234567890#")).toBeTruthy();
    });

    it("says so rather than printing a dialable placeholder", () => {
        // A plausible-looking fake number is one somebody dials. The warning
        // is the useful thing here, not a code that forwards nowhere.
        render(<KeepYourNumber />);
        fireEvent.click(screen.getByRole("button", { name: /show me how/i }));
        expect(screen.getByText(/give the agent a number first/i)).toBeTruthy();
        expect(screen.getByText("**67*<your Decibyl number>#")).toBeTruthy();
    });

    it("always tells them to verify, because a silent failure looks like success", () => {
        render(<KeepYourNumber agentNumber="+911234567890" />);
        fireEvent.click(screen.getByRole("button", { name: /show me how/i }));
        expect(screen.getByText(/then check it took/i)).toBeTruthy();
        expect(screen.getByText(/ring your own number from another phone/i)).toBeTruthy();
    });

    it("is collapsed until asked, so it does not bury the rental flow", () => {
        render(<KeepYourNumber agentNumber="+911234567890" />);
        expect(screen.queryByText("When the line is busy")).toBeNull();
    });
});
