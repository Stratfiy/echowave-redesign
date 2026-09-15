// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { POST } from "./route";

function parse(header: string) {
  const [nameValue, ...attrs] = header.split("; ");
  const eq = nameValue.indexOf("=");
  return {
    name: nameValue.slice(0, eq),
    maxAge: Number(attrs.find((a) => a.startsWith("Max-Age="))?.slice(8)),
    partitioned: attrs.includes("Partitioned"),
    domain: attrs.find((a) => a.startsWith("Domain="))?.slice(7),
  };
}

describe("POST /impersonate/stop", () => {
  it("ends the borrowed session in every jar and sends the staffer to sign in", async () => {
    const headers = new Headers({
      cookie: [
        "__Host-hexclave-refresh-proj--default=x",
        "hexclave-access-proj=y",
        "decibyl-impersonating=owner%40clinic.example",
        "stack-is-https=true",
      ].join("; "),
      "x-forwarded-proto": "https",
    });
    const response = await POST(
      new NextRequest("https://app.decibyl.ai/impersonate/stop", {
        method: "POST",
        headers,
      }),
    );
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      "https://app.decibyl.ai/auth/login?next=/superadmin",
    );
    const cookies = response.headers.getSetCookie().map(parse);
    // Every session cookie deleted in every scope -- host-only, the host
    // itself and the parent domain -- in both jars.
    const access = cookies.filter((c) => c.name === "hexclave-access-proj");
    expect(access.length).toBe(3 * 2);
    expect(access.every((c) => c.maxAge === 0)).toBe(true);
    expect(access.some((c) => c.partitioned)).toBe(true);
    expect(access.some((c) => c.domain === "decibyl.ai")).toBe(true);
    // __Host- cookies never carry a Domain.
    const host = cookies.filter((c) => c.name.startsWith("__Host-"));
    expect(host.length).toBe(2);
    expect(host.every((c) => c.domain === undefined)).toBe(true);
    // The marker goes with it; the unrelated SDK flag is left alone.
    const marker = cookies.find((c) => c.name === "decibyl-impersonating");
    expect(marker?.maxAge).toBe(0);
    expect(cookies.some((c) => c.name === "stack-is-https")).toBe(false);
  });

  it("is harmless with no session at all", async () => {
    const response = await POST(
      new NextRequest("http://localhost:3000/impersonate/stop", {
        method: "POST",
      }),
    );
    expect(response.status).toBe(303);
    const cookies = response.headers.getSetCookie().map(parse);
    expect(cookies.map((c) => c.name)).toEqual(["decibyl-impersonating"]);
  });
});
