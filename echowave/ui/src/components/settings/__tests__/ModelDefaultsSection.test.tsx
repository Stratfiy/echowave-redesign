import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ModelDefaultsSection } from "../ModelDefaultsSection";

vi.mock("@/components/agent/SimpleModelPicker", () => ({
    SimpleModelPicker: () => <div>Simple picker</div>,
}));

vi.mock("@/components/ModelConfigurationV2", () => ({
    default: () => <div>Advanced editor</div>,
}));

describe("model defaults view", () => {
    beforeEach(() => localStorage.clear());

    it("reopens the view the customer selected", async () => {
        const first = render(<ModelDefaultsSection />);
        fireEvent.click(screen.getByRole("tab", { name: "Advanced" }));
        expect(screen.getByText("Advanced editor")).toBeTruthy();
        first.unmount();

        render(<ModelDefaultsSection />);

        await waitFor(() =>
            expect(screen.getByRole("tab", { name: "Advanced" }).getAttribute("aria-selected"))
                .toBe("true"),
        );
        expect(screen.getByText("Advanced editor")).toBeTruthy();
    });
});
