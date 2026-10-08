/**
 * The design's blob faces: exact shapes and pastels, picked from the id.
 */

import { cleanup, render } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { BLOB_PASTELS, BLOB_PATHS, BlobFace, blobLook } from "../BlobFace";

afterEach(cleanup);

describe("BlobFace", () => {
    it("wears one of the design's shapes and pastels", () => {
        for (const seed of [0, 1, 2, 5, 8, 13, 101, "Front desk"]) {
            const look = blobLook(seed);
            expect(BLOB_PATHS).toContain(look.path);
            expect(BLOB_PASTELS).toContain(look.fill);
        }
    });

    it("is the same face for the same seed, and nine looks across seeds", () => {
        expect(blobLook(42)).toEqual(blobLook(42));
        const looks = new Set(Array.from({ length: 9 }, (_, i) => JSON.stringify(blobLook(i))));
        expect(looks.size).toBe(9);
    });

    it("keeps a colour the owner picked, as a pastel", () => {
        expect(blobLook(0, { color: "rose" }).fill).toBe("#F7B5E3");
        expect(blobLook(0, { color: "vert" }).fill).toBe("#CDEB7A");
        // Ink has no pastel: the seed's own colour.
        expect(blobLook(0, { color: "encre" }).fill).toBe(blobLook(0).fill);
    });

    it("is decorative, with the design's eyes", () => {
        const { container } = render(<BlobFace seed={3} size={26} />);
        const svg = container.querySelector("svg")!;
        expect(svg.getAttribute("aria-hidden")).toBe("true");
        expect(svg.getAttribute("viewBox")).toBe("0 0 100 100");
        const eyes = svg.querySelectorAll("ellipse");
        expect(eyes).toHaveLength(2);
        expect(eyes[0].getAttribute("cx")).toBe("38");
        expect(eyes[1].getAttribute("cx")).toBe("62");
        expect(eyes[0].getAttribute("rx")).toBe("6");
        expect(eyes[0].getAttribute("ry")).toBe("8");
        expect(eyes[0].getAttribute("fill")).toBe("#0d0d0d");
    });

    it("closes its eyes when resting", () => {
        const { container } = render(<BlobFace seed={3} mood="resting" />);
        expect(container.querySelectorAll("ellipse")).toHaveLength(0);
        expect(container.querySelectorAll("rect")).toHaveLength(2);
    });
});
