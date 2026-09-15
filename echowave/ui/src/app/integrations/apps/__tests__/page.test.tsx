/**
 * The app catalogue lives in the Marketplace now. These routes stay so old
 * links and bookmarks land somewhere rather than 404, and the only thing
 * worth asserting about them is that they hand over.
 */

import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

import IntegrationsAppsMorePage from "../more/page";
import IntegrationsAppsPage from "../page";

beforeEach(() => replace.mockReset());

describe("the old app catalogue routes", () => {
    it("sends the catalogue to the Marketplace's Integrations shelf", () => {
        render(<IntegrationsAppsPage />);
        expect(replace).toHaveBeenCalledWith("/marketplace/integrations");
    });

    it("sends the long tail there too, where Other and search cover it", () => {
        render(<IntegrationsAppsMorePage />);
        expect(replace).toHaveBeenCalledWith("/marketplace/integrations");
    });
});
