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

  it("keeps the product behind a session", () => {
    expect(isPublicPath("/")).toBe(false);
    expect(isPublicPath("/workflow/19")).toBe(false);
    expect(isPublicPath("/talkative")).toBe(false);
  });
});
