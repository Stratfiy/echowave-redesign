import { describe, expect, it } from "vitest";

import { createToolDefinition, getCategoryConfig, getToolTypeLabel, TOOL_CATEGORIES } from "../config";

describe("Spreadsheets tool (U-3)", () => {
    it("is a category a person can pick, with nothing to configure", () => {
        expect(TOOL_CATEGORIES.some((c) => c.value === "tables")).toBe(true);
        expect(getCategoryConfig("tables")?.label).toBe("Spreadsheets");
        expect(createToolDefinition("tables")).toEqual({ schema_version: 1, type: "tables" });
    });

    it("is named for what it is on the tool list", () => {
        expect(getToolTypeLabel("tables")).toBe("Spreadsheet Tool");
    });
});
