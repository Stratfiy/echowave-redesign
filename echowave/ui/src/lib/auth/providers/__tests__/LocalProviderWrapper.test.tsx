import { render, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "../AuthProvider";
import { LocalProviderWrapper } from "../LocalProviderWrapper";

/** Grabs the context's token getter the moment the provider renders. */
function Grabber({ onAuth }: { onAuth: (getToken: () => Promise<string>) => void }) {
  const auth = useAuth();
  onAuth(auth.getAccessToken);
  return null;
}

describe("LocalProviderWrapper", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("a token asked for before the cookie is read waits for it rather than answering empty", async () => {
    let releaseFetch: (v: Response) => void = () => {};
    const fetchMock = vi.fn(
      () => new Promise<Response>((resolve) => { releaseFetch = resolve; }),
    );
    vi.stubGlobal("fetch", fetchMock);

    let getToken: (() => Promise<string>) | null = null;
    render(
      <LocalProviderWrapper>
        <Grabber onAuth={(g) => { getToken = g; }} />
      </LocalProviderWrapper>,
    );
    expect(getToken).not.toBeNull();

    // Asked before /api/auth/oss has answered: this is the first fetch of
    // any screen. It must not resolve to "" here.
    const pending = getToken!();
    let settled = false;
    void pending.then(() => { settled = true; });
    await new Promise((r) => setTimeout(r, 20));
    expect(settled).toBe(false);

    releaseFetch(
      new Response(JSON.stringify({ token: "tok-123", user: { id: "u1", provider: "local" } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await waitFor(async () => expect(await pending).toBe("tok-123"));
  });
});
