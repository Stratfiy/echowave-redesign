import { describe, expect, it } from "vitest";

import { isPublicPath } from "../publicPaths";

describe("isPublicPath", () => {
  it("lets a prospect open the share page with no account", () => {
    expect(isPublicPath("/talk/emb_abc")).toBe(true);
  });

  it("lets the auth pages through", () => {
    expect(isPublicPath("/auth/login")).toBe(true);
    expect(isPublicPath("/auth/forgot/reset")).toBe(true);
  });

  it("lets the embed widget script load with no account", () => {
    // The share page waits on this script's onload before it stops saying
    // "Preparing…", and every customer site loads it from a page that has
    // no Decibyl session at all.
    expect(isPublicPath("/embed/decibyl-widget.js")).toBe(true);
  });

  it("keeps the product behind a session", () => {
    expect(isPublicPath("/")).toBe(false);
    expect(isPublicPath("/workflow/19")).toBe(false);
    expect(isPublicPath("/talkative")).toBe(false);
    expect(isPublicPath("/embedded-analytics")).toBe(false);
  });
});
