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

describe("a voice whose vendor published no gender", () => {
    // OpenAI publishes none for any of its eleven, and ElevenLabs' account
    // voices often carry none either. They used to match no group and vanish
    // — for a whole provider that reads as "this model has no voices".
    const ungendered = [
        {
            voice_id: "alloy",
            name: "Alloy",
            gender: null,
            description: null,
            is_default: true,
            sample_url: null,
            sample_url_hi: null,
        },
        {
            voice_id: "verse",
            name: "Verse",
            gender: null,
            description: null,
            is_default: false,
            sample_url: null,
            sample_url_hi: null,
        },
    ];

    it("is listed rather than dropped", () => {
        render(
            <VoicePicker voices={ungendered} selected="alloy" onSelect={() => {}} />,
        );

        expect(screen.getByText(/Alloy/)).toBeTruthy();
        expect(screen.getByText("Verse")).toBeTruthy();
        expect(screen.getByText("Other")).toBeTruthy();
    });

    it("can still be chosen", () => {
        const onSelect = vi.fn();
        render(
            <VoicePicker voices={ungendered} selected="alloy" onSelect={onSelect} />,
        );

        fireEvent.click(screen.getByText("Verse"));

        expect(onSelect).toHaveBeenCalledWith("verse", "Other");
    });

    it("does not invent an Other heading when every voice has a gender", () => {
        render(
            <VoicePicker
                voices={[{ ...ungendered[0], gender: "female" }]}
                selected="alloy"
                onSelect={() => {}}
            />,
        );

        expect(screen.queryByText("Other")).toBeNull();
    });
});
