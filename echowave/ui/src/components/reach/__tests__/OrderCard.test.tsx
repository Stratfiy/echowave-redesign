/**
 * The order card (stream `reach`): before anything is placed it shows each
 * item, every charge, the total, the address and how it is paid; Approve
 * sends the card's version; a colleague sees no details; a placed order
 * links to the app's payment; a lost outcome can be checked, not re-sent.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const detail = vi.hoisted(() => vi.fn());
const settle = vi.hoisted(() => vi.fn());
const revise = vi.hoisted(() => vi.fn());
const check = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({
    orderDetailApiV1ReachOrdersOrderIdGet: detail,
    settleActionApiV1TimelineActionsSettlePost: settle,
    reviseOrderApiV1ReachOrdersOrderIdRevisePost: revise,
    checkOrderApiV1ReachOrdersOrderIdCheckPost: check,
}));

import { ActionCard } from "@/components/workflow/ActionCard";

import { billLines } from "../OrderPreview";

const event = (payload: Record<string, unknown>) =>
    ({
        id: 41,
        at: "2026-10-08T00:00:00Z",
        kind: "action_proposed",
        actor: "agent",
        summary: "Order from Spice Route on Zomato: 3 items, ₹555",
        payload: {
            action: "place_order",
            label: "Order from Spice Route on Zomato: 3 items, ₹555",
            version: "abc123def456",
            args: { draft: "d-1", digest: "x", provider: "zomato" },
            state: "proposed",
            ...payload,
        },
        is_deliverable: false,
        workflow_id: null,
        workflow_run_id: null,
        folder_id: null,
    }) as never;

const order = {
    id: "d-1",
    provider: "zomato",
    provider_name: "Zomato",
    store: { id: "r1", name: "Spice Route" },
    items: [
        { item_id: "i1", name: "Paneer Tikka", quantity: 2, unit_price_paise: 22000, line_total_paise: 44000 },
        { item_id: "i3", name: "Coke 500ml", quantity: 1, unit_price_paise: 6000, line_total_paise: 6000 },
    ],
    charges: [
        { label: "Delivery", amount_paise: 3000 },
        { label: "Taxes", amount_paise: 2500 },
    ],
    discount_paise: 0,
    subtotal_paise: 50000,
    total_paise: 55500,
    currency: "INR",
    coupon: null,
    address: { id: "a1", label: "Home", line: "12 MG Road, Bengaluru 560001" },
    payment: { method: "upi_qr", label: "UPI (scan the QR Zomato shows)" },
    offers: [],
    status: "proposed",
    digest: "x",
    card_event_id: 41,
};

beforeEach(() => {
    detail.mockReset();
    settle.mockReset();
    revise.mockReset();
    check.mockReset();
});

describe("before it is placed", () => {
    it("shows every item, charge, the total, the address and the payment", async () => {
        detail.mockResolvedValue({ data: order, response: { status: 200 } });
        render(<ActionCard event={event({})} />);
        const preview = await screen.findByTestId("action-preview");
        const text = preview.textContent ?? "";
        expect(text).toContain("2 × Paneer Tikka — ₹440");
        expect(text).toContain("1 × Coke 500ml — ₹60");
        expect(text).toContain("Delivery — ₹30");
        expect(text).toContain("Taxes — ₹25");
        expect(text).toContain("Total — ₹555");
        expect(text).toContain("Home: 12 MG Road, Bengaluru 560001");
        expect(text).toContain("UPI (scan the QR Zomato shows)");
        expect(text).toContain("cannot be undone once placed");
        expect(detail.mock.calls[0][0]).toEqual({ path: { order_id: "d-1" } });
    });

    it("approve sends this card's version", async () => {
        detail.mockResolvedValue({ data: order, response: { status: 200 } });
        settle.mockResolvedValue({ data: event({ state: "armed" }) });
        render(<ActionCard event={event({})} />);
        fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
        await waitFor(() => expect(settle).toHaveBeenCalled());
        expect(settle.mock.calls[0][0].body).toEqual({ event_id: 41, verb: "confirm", version: "abc123def456" });
    });

    it("cancel declines it", async () => {
        detail.mockResolvedValue({ data: order, response: { status: 200 } });
        settle.mockResolvedValue({ data: event({ state: "declined" }) });
        render(<ActionCard event={event({})} />);
        fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
        await waitFor(() => expect(settle.mock.calls[0][0].body).toEqual({ event_id: 41, verb: "decline" }));
    });

    it("an edit is priced again as a new card", async () => {
        detail.mockResolvedValue({ data: order, response: { status: 200 } });
        revise.mockResolvedValue({ data: { status: "proposed", event_id: 42 } });
        const onFired = vi.fn();
        render(<ActionCard event={event({})} onFired={onFired} />);
        fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
        fireEvent.click(screen.getByRole("button", { name: "One fewer Paneer Tikka" }));
        fireEvent.click(screen.getByRole("button", { name: "Price it again" }));
        await waitFor(() => expect(revise).toHaveBeenCalled());
        expect(revise.mock.calls[0][0].body.items).toEqual([
            { item_id: "i1", quantity: 1 },
            { item_id: "i3", quantity: 1 },
        ]);
        await waitFor(() => expect(onFired).toHaveBeenCalled());
    });

    it("a colleague sees that it is not theirs, and no details", async () => {
        detail.mockResolvedValue({ error: { detail: "Not Found" }, response: { status: 404 } });
        render(<ActionCard event={event({})} />);
        expect((await screen.findByTestId("order-not-yours")).textContent).toContain("Only the person who asked");
        expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
        expect(screen.queryByText(/MG Road/)).toBeNull();
    });
});

describe("after", () => {
    it("a placed order links to the app's payment", () => {
        render(
            <ActionCard
                event={event({
                    state: "done",
                    done: { note: "Ordered on Zomato: order Z1001, ₹555." },
                    result: { order_id: "Z1001", payment_link: "https://pay.example.com/upi/c1" },
                })}
            />,
        );
        expect(screen.getByText("Ordered on Zomato: order Z1001, ₹555.")).toBeTruthy();
        expect(screen.getByRole("link", { name: "Pay on the app" }).getAttribute("href")).toBe("https://pay.example.com/upi/c1");
    });

    it("an unknown outcome can be checked, and is never offered again", async () => {
        check.mockResolvedValue({ data: { ...order, status: "placed" } });
        const onFired = vi.fn();
        render(
            <ActionCard
                event={event({ state: "outcome_unknown", error: "We are checking whether this order went through. Please do not order it again until we know." })}
                onFired={onFired}
            />,
        );
        expect(screen.getByText(/Please do not order it again/)).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Check with the app" }));
        await waitFor(() => expect(check).toHaveBeenCalledWith({ path: { order_id: "d-1" } }));
        await waitFor(() => expect(onFired).toHaveBeenCalled());
    });
});

it("the bill reads with the discount and coupon", () => {
    expect(billLines({ ...order, discount_paise: 5000, coupon: "SAVE50", total_paise: 50500 } as never)).toContain(
        "Discount (SAVE50) — −₹50",
    );
});
