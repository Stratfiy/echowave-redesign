/**
 * Settings' list shows a launch section only while its switch is on, so
 * turning a switch off restores the list as it was (launch stream identity).
 */
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const flags = vi.hoisted(() => ({ on: new Set<string>() }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => flags.on.has(name) }));
vi.mock("next/navigation", () => ({ usePathname: () => "/settings/team" }));

import { SettingsNav } from "../SettingsNav";

afterEach(() => {
  cleanup();
  flags.on.clear();
});

function titles(): string[] {
  return screen.getAllByRole("link").map((a) => a.textContent ?? "").filter((t) => t !== "Settings");
}

describe("SettingsNav", () => {
  it("leaves the identity sections out while their switches are off", () => {
    render(<SettingsNav />);
    expect(titles()).not.toContain("Connections");
    expect(titles()).not.toContain("Decibyl identity");
    expect(titles()).not.toContain("Notifications");
    expect(titles()).toContain("Team");
  });

  it("lists each one when its switch is on", () => {
    flags.on.add("identity_connections");
    flags.on.add("identity_phone");
    flags.on.add("identity_notifications");
    render(<SettingsNav />);
    expect(titles()).toContain("Connections");
    expect(titles()).toContain("Decibyl identity");
    expect(titles()).toContain("Notifications");
  });
});
