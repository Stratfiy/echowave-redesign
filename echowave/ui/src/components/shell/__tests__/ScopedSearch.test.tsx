import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ScopedSearch } from "../ScopedSearch";

const scopes = [
    { id: "personal", label: "Just you" },
    { id: "workspace", label: "Clinic" },
];

function Harness({ search }: { search: (q: string, s: string) => Promise<string[]> }) {
    const [scope, setScope] = useState("personal");
    return (
        <ScopedSearch
            scopes={scopes}
            scope={scope}
            onScopeChange={setScope}
            search={search}
            renderResult={(r) => <span>{r}</span>}
            resultKey={(r) => r}
            debounceMs={0}
        />
    );
}

describe("ScopedSearch", () => {
    it("searches the chosen scope and says when nothing matches", async () => {
        const search = vi.fn(async (q: string, s: string) => (q === "none" ? [] : [`${s}:${q}`]));
        render(<Harness search={search} />);
        fireEvent.change(screen.getByLabelText("Search Just you"), { target: { value: "ravi" } });
        await screen.findByText("personal:ravi");
        fireEvent.change(screen.getByLabelText("Search Just you"), { target: { value: "none" } });
        await screen.findByText("Nothing in Just you matches that.");
    });

    it("clears the last scope's results and drops a late answer for it", async () => {
        let resolvePersonal: (value: string[]) => void = () => {};
        const search = vi.fn((q: string, s: string) =>
            s === "personal"
                ? new Promise<string[]>((resolve) => {
                      resolvePersonal = resolve;
                  })
                : Promise.resolve([`workspace:${q}`]),
        );
        render(<Harness search={search} />);
        fireEvent.change(screen.getByLabelText("Search Just you"), { target: { value: "ravi" } });
        await waitFor(() => expect(search).toHaveBeenCalledWith("ravi", "personal"));
        fireEvent.click(screen.getByRole("radio", { name: "Clinic" }));
        await screen.findByText("workspace:ravi");
        // The personal answer lands late and must not paint over the workspace.
        await act(async () => resolvePersonal(["personal:secret"]));
        expect(screen.queryByText("personal:secret")).toBeNull();
        expect(screen.getByText("workspace:ravi")).toBeTruthy();
    });

    it("offers a retry when a search fails", async () => {
        const search = vi.fn().mockRejectedValueOnce(new Error("down")).mockResolvedValue(["again"]);
        render(<Harness search={search} />);
        fireEvent.change(screen.getByLabelText("Search Just you"), { target: { value: "x" } });
        fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
        await screen.findByText("again");
    });
});
