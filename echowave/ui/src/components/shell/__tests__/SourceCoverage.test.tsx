import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { coverageLine, SourceCoverage } from "../SourceCoverage";

const sources = [
    { kind: "team", label: "Your team", status: "read" as const, detail: "2 agents" },
    { kind: "memory", label: "Confirmed facts", status: "read" as const, detail: "3 facts" },
    {
        kind: "knowledge",
        label: "Company knowledge",
        status: "unavailable" as const,
        detail: "Not set up for this workspace",
        documents: [],
    },
];

describe("SourceCoverage", () => {
    it("counts what was checked out of everything it tried", () => {
        expect(coverageLine(sources)).toBe("2 of 3 sources checked");
        expect(coverageLine([])).toBe("No sources checked");
    });

    it("lists the source that could not run instead of dropping it", () => {
        render(<SourceCoverage sources={sources} />);
        const toggle = screen.getByRole("button", { name: /2 of 3 sources checked/ });
        expect(toggle.getAttribute("aria-expanded")).toBe("false");
        fireEvent.click(toggle);
        expect(toggle.getAttribute("aria-expanded")).toBe("true");
        expect(screen.getByText("Company knowledge")).toBeTruthy();
        expect(screen.getByText(/not checked · Not set up/)).toBeTruthy();
    });

    it("names the documents a passage came from", () => {
        render(
            <SourceCoverage
                defaultOpen
                sources={[{ kind: "knowledge", label: "Company knowledge", status: "read", documents: ["Price list.pdf"] }]}
            />,
        );
        expect(screen.getByText("Price list.pdf")).toBeTruthy();
    });
});
