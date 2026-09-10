import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type VoiceOption,VoicePicker } from "../VoicePicker";

// jsdom has no real audio; capture what the picker tries to play.
let lastSrc = "";
const play = vi.fn().mockResolvedValue(undefined);
beforeEach(() => {
  lastSrc = "";
  play.mockClear();
  // @ts-expect-error minimal Audio stub
  global.Audio = class {
    set src(v: string) { lastSrc = v; }
    pause() {}
    play() { return play(); }
  };
});

function voice(partial: Partial<VoiceOption>): VoiceOption {
  return {
    voice_id: "v1", name: "Roger", gender: "male", description: null,
    is_default: false, sample_url: null, sample_url_hi: null, preview_url: null,
    ...partial,
  };
}

describe("VoicePicker previews", () => {
  it("shows a play button and plays preview_url when it is the only source (ElevenLabs)", () => {
    // The bug: ElevenLabs voices arrive with sample_url null and the preview
    // in preview_url, and the picker showed no play affordance for any of them.
    render(
      <VoicePicker
        voices={[voice({ preview_url: "https://cdn/eleven/roger.mp3" })]}
        selected="" onSelect={() => {}}
      />,
    );
    const playBtn = screen.getByRole("button", { name: /hear/i });
    fireEvent.click(playBtn);
    expect(play).toHaveBeenCalled();
    expect(lastSrc).toBe("https://cdn/eleven/roger.mp3");
  });

  it("prefers sample_url over preview_url when both exist", () => {
    render(
      <VoicePicker
        voices={[voice({ sample_url: "https://s/en.wav", preview_url: "https://cdn/x.mp3" })]}
        selected="" onSelect={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /hear/i }));
    expect(lastSrc).toBe("https://s/en.wav");
  });
});
