/**
 * The image provider card: a provider is chosen and its key pasted here, in
 * the thread that asked -- never on another screen. The key goes to the
 * server and is cleared from the card whatever the answer, a refusal is
 * said in words, and once connected the original request is sent on again.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const providers = vi.hoisted(() => vi.fn());
const connect = vi.hoisted(() => vi.fn());
const imageUrl = vi.hoisted(() => vi.fn());
const download = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({
    imageProvidersApiV1ImagesProvidersGet: providers,
    connectImageProviderApiV1ImagesProviderPut: connect,
    imageUrlApiV1ImagesImageUuidGet: imageUrl,
    downloadImageApiV1ImagesImageUuidFileGet: download,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import { ImageProviderCard } from "../ImageProviderCard";
import { editLine, ImagesCard } from "../ImagesCard";

const event = (kind: string, payload: Record<string, unknown>, summary = "Choose how to make images") =>
    ({
        id: 41,
        at: "2026-10-09T00:00:00Z",
        kind,
        actor: "agent",
        summary,
        payload,
        is_deliverable: false,
        workflow_id: null,
        workflow_run_id: null,
        folder_id: null,
    }) as never;

const provider = (name: string, label: string, over: Record<string, unknown> = {}) => ({
    provider: name,
    label,
    blurb: `${label} images`,
    key_label: `${label} API key`,
    key_hint: `From ${label}.`,
    ready: false,
    source: null,
    masked_key: null,
    takes_references: true,
    ...over,
});

const status = (over: Record<string, unknown> = {}) => ({
    chosen: null,
    ready: false,
    encryption_configured: true,
    providers: [
        provider("google", "Google Gemini"),
        provider("openai", "OpenAI"),
        provider("aws_bedrock", "Amazon Bedrock"),
    ],
    ...over,
});

beforeEach(() => {
    providers.mockReset();
    connect.mockReset();
    imageUrl.mockReset();
    download.mockReset();
});

describe("choosing a provider in the thread", () => {
    it("shows the three providers and connects the picked one with a masked key, then sends the request on", async () => {
        providers.mockResolvedValue({ data: status() });
        connect.mockResolvedValue({
            data: {
                ...status({ chosen: "openai", ready: true }),
                verification: "verified",
                verification_message: "OpenAI accepted the key.",
            },
        });
        const resend = vi.fn();
        render(
            <ImageProviderCard
                event={event("image_provider_offered", { request: "make me a Diwali sale poster" })}
                onConnected={resend}
            />,
        );
        expect(await screen.findByRole("radio", { name: /Google Gemini/ })).toBeTruthy();
        expect(screen.getByRole("radio", { name: /Amazon Bedrock/ })).toBeTruthy();
        // Nothing to connect until a provider is picked.
        expect((screen.getByRole("button", { name: "Connect" }) as HTMLButtonElement).disabled).toBe(true);

        fireEvent.click(screen.getByRole("radio", { name: /OpenAI/ }));
        const input = screen.getByLabelText(/OpenAI API key/) as HTMLInputElement;
        expect(input.type).toBe("password");
        fireEvent.change(input, { target: { value: "sk-test-not-real-1234" } });
        fireEvent.click(screen.getByRole("button", { name: "Connect" }));

        await waitFor(() => expect(connect).toHaveBeenCalled());
        expect(connect.mock.calls[0][0].body).toEqual({
            provider: "openai",
            api_key: "sk-test-not-real-1234",
            verify: true,
        });
        expect(await screen.findByText("OpenAI is connected.")).toBeTruthy();
        expect(screen.getByText("OpenAI accepted the key.")).toBeTruthy();
        await waitFor(() => expect(resend).toHaveBeenCalledWith("make me a Diwali sale poster"));
        expect(screen.getByText("Your request was sent again.")).toBeTruthy();
        // The key is nowhere on the card once sent.
        expect(document.body.textContent).not.toContain("sk-test-not-real-1234");
        expect(screen.getByTestId("image-provider-card").getAttribute("data-state")).toBe("connected");
    });

    it("says a refusal in words, clears the key and sends nothing on", async () => {
        providers.mockResolvedValue({ data: status() });
        connect.mockResolvedValue({ error: { detail: "Gemini refused that key. Check you copied all of it." } });
        const resend = vi.fn();
        render(<ImageProviderCard event={event("image_provider_offered", { request: "poster" })} onConnected={resend} />);
        fireEvent.click(await screen.findByRole("radio", { name: /Google Gemini/ }));
        const input = screen.getByLabelText(/Google Gemini API key/) as HTMLInputElement;
        fireEvent.change(input, { target: { value: "AIza-wrong-key" } });
        fireEvent.click(screen.getByRole("button", { name: "Connect" }));
        expect((await screen.findByRole("alert")).textContent).toBe(
            "Gemini refused that key. Check you copied all of it.",
        );
        expect(input.value).toBe("");
        expect(resend).not.toHaveBeenCalled();
    });

    it("offers a key the workspace already has without asking for it again", async () => {
        providers.mockResolvedValue({
            data: status({
                providers: [
                    provider("google", "Google Gemini", { ready: true, source: "your_key", masked_key: "••••abcd" }),
                    provider("openai", "OpenAI"),
                    provider("aws_bedrock", "Amazon Bedrock"),
                ],
            }),
        });
        connect.mockResolvedValue({ data: { ...status({ chosen: "google", ready: true }), verification: "existing" } });
        render(<ImageProviderCard event={event("image_provider_offered", {})} />);
        expect(await screen.findByText(/Ready on your key ••••abcd/)).toBeTruthy();
        fireEvent.click(screen.getByRole("radio", { name: /Google Gemini/ }));
        fireEvent.click(screen.getByRole("button", { name: "Connect" }));
        await waitFor(() => expect(connect).toHaveBeenCalled());
        expect(connect.mock.calls[0][0].body).toEqual({ provider: "google", api_key: null, verify: true });
    });

    it("comes back for a refused key, preselected, and asks for a new one", async () => {
        providers.mockResolvedValue({
            data: status({
                chosen: "openai",
                ready: true,
                providers: [
                    provider("google", "Google Gemini"),
                    provider("openai", "OpenAI", { ready: true, source: "your_key", masked_key: "••••9z9z" }),
                    provider("aws_bedrock", "Amazon Bedrock"),
                ],
            }),
        });
        render(
            <ImageProviderCard
                event={event("image_provider_offered", {
                    provider: "openai",
                    reason: "Your OpenAI key was refused. Check it on the card and connect it again.",
                })}
            />,
        );
        expect(await screen.findByText(/Your OpenAI key was refused/)).toBeTruthy();
        expect(screen.getByRole("radio", { name: /OpenAI/ }).getAttribute("aria-checked")).toBe("true");
        // The refused key is not offered as "ready": a new one must be pasted.
        expect((screen.getByRole("button", { name: "Connect" }) as HTMLButtonElement).disabled).toBe(true);
    });

    it("shows a provider that is already connected as such", async () => {
        providers.mockResolvedValue({
            data: status({
                chosen: "aws_bedrock",
                ready: true,
                providers: [
                    provider("google", "Google Gemini"),
                    provider("openai", "OpenAI"),
                    provider("aws_bedrock", "Amazon Bedrock", { ready: true, source: "platform" }),
                ],
            }),
        });
        render(<ImageProviderCard event={event("image_provider_offered", {})} />);
        expect(await screen.findByText("Images are made with Amazon Bedrock, on Decibyl's key.")).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
    });

    it("says when keys cannot be stored on this server", async () => {
        providers.mockResolvedValue({ data: status({ encryption_configured: false }) });
        render(<ImageProviderCard event={event("image_provider_offered", {})} />);
        expect(await screen.findByText(/Keys cannot be stored on this server yet/)).toBeTruthy();
    });
});

describe("the grid of options", () => {
    const image = (index: number) => ({
        image_uuid: `img_${String(index).repeat(32)}`,
        kind: "generated",
        provider: "google",
        format: "instagram_square",
        mime_type: "image/png",
        size_bytes: 10,
        option_index: index,
    });

    it("draws each option from a signed URL and sends an edit naming the option's id", async () => {
        imageUrl.mockImplementation(({ path }: { path: { image_uuid: string } }) =>
            Promise.resolve({ data: { url: `https://bucket.example/${path.image_uuid}.png`, expires_in: 3600 } }),
        );
        const edit = vi.fn();
        render(
            <ImagesCard
                event={event(
                    "images_made",
                    {
                        images: [image(0), image(1)],
                        provider_label: "Google Gemini",
                        format_label: "Instagram post (square)",
                    },
                    "2 options for Narayani Sweets (Instagram post (square))",
                )}
                onEdit={edit}
            />,
        );
        expect(await screen.findByAltText("Option 1")).toBeTruthy();
        expect(screen.getAllByTestId("image-option")).toHaveLength(2);
        expect(screen.getByText("Instagram post (square) · made with Google Gemini")).toBeTruthy();
        expect(screen.getByRole("button", { name: "Download Option 2" })).toBeTruthy();

        fireEvent.click(screen.getByRole("button", { name: "Edit Option 2" }));
        fireEvent.change(screen.getByLabelText("What to change on Option 2"), {
            target: { value: "make the headline bigger" },
        });
        fireEvent.click(screen.getByRole("button", { name: "Send" }));
        await waitFor(() =>
            expect(edit).toHaveBeenCalledWith(`Edit option 2 (${image(1).image_uuid}): make the headline bigger`),
        );
    });

    it("says an image that could not be shown rather than leaving a blank", async () => {
        imageUrl.mockResolvedValue({ error: { detail: "No such image here" } });
        render(<ImagesCard event={event("images_made", { images: [image(0)] }, "1 option")} />);
        expect((await screen.findByRole("alert")).textContent).toBe("No such image here");
        // No edit callback, no edit button.
        expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    });

    it("an empty card says so", () => {
        render(<ImagesCard event={event("images_made", {}, "Nothing")} />);
        expect(screen.getByText("No images are on this card.")).toBeTruthy();
    });

    it("the edit line names the option and the id", () => {
        expect(editLine(image(2) as never, "  Tamil version ")).toBe(
            `Edit option 3 (${image(2).image_uuid}): Tamil version`,
        );
    });
});
