/**
 * The two small inputs (TB-3): labels added on Enter and removed by their
 * x, and a thread box that offers an agent's @handle while it is typed.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { LabelPicker, MentionTextarea } from "../TaskInputs";

const BOTS = [
    { id: 2, name: "Billing", handle: "billing" },
    { id: 3, name: "Bookings", handle: "bookings" },
    { id: 4, name: "Sales", handle: "sales" },
];

function Box({ onSubmit = vi.fn() }: { onSubmit?: () => void }) {
    const [text, setText] = useState("");
    return <MentionTextarea aria-label="Line" value={text} onChange={setText} bots={BOTS} onSubmit={onSubmit} />;
}

const type = (el: HTMLElement, value: string) => fireEvent.change(el, { target: { value, selectionStart: value.length } });

describe("the @ box", () => {
    it("offers matching agents while an @word is typed", () => {
        render(<Box />);
        const box = screen.getByLabelText("Line");
        type(box, "please @bo");
        const options = screen.getAllByRole("option");
        expect(options.map((o) => o.textContent)).toEqual(["@bookingsBookings"]);
        expect(box.getAttribute("aria-expanded")).toBe("true");
    });

    it("picks with the keyboard and writes the handle in place", () => {
        render(<Box />);
        const box = screen.getByLabelText("Line") as HTMLTextAreaElement;
        type(box, "@b");
        fireEvent.keyDown(box, { key: "ArrowDown" });
        fireEvent.keyDown(box, { key: "Enter" });
        expect(box.value).toBe("@bookings ");
        expect(screen.queryByRole("listbox")).toBeNull();
    });

    it("picks with the mouse", () => {
        render(<Box />);
        const box = screen.getByLabelText("Line") as HTMLTextAreaElement;
        type(box, "ask @s");
        fireEvent.mouseDown(screen.getByRole("option", { name: /@sales/ }));
        expect(box.value).toBe("ask @sales ");
    });

    it("closes on Escape, and Enter then is an ordinary key", () => {
        const onSubmit = vi.fn();
        render(<Box onSubmit={onSubmit} />);
        const box = screen.getByLabelText("Line");
        type(box, "@b");
        fireEvent.keyDown(box, { key: "Escape" });
        expect(screen.queryByRole("listbox")).toBeNull();
        fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
        expect(onSubmit).toHaveBeenCalled();
    });

    it("offers nothing inside an email address", () => {
        render(<Box />);
        type(screen.getByLabelText("Line"), "mail ravi@b");
        expect(screen.queryByRole("listbox")).toBeNull();
    });
});

describe("the label picker", () => {
    function Picker({ onChange }: { onChange: (l: string[]) => void }) {
        const [labels, setLabels] = useState<string[]>(["urgent"]);
        return (
            <LabelPicker
                id="l"
                value={labels}
                known={["urgent", "billing"]}
                onChange={(next) => {
                    setLabels(next);
                    onChange(next);
                }}
            />
        );
    }

    it("adds on Enter, ignores a repeat, and removes by its x", () => {
        const onChange = vi.fn();
        const { container } = render(<Picker onChange={onChange} />);
        const input = container.querySelector("#l") as HTMLInputElement;
        fireEvent.change(input, { target: { value: "VIP  client" } });
        fireEvent.keyDown(input, { key: "Enter" });
        expect(onChange).toHaveBeenLastCalledWith(["urgent", "VIP client"]);
        fireEvent.change(input, { target: { value: "URGENT" } });
        fireEvent.keyDown(input, { key: "Enter" });
        expect(onChange).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByRole("button", { name: "Remove label urgent" }));
        expect(onChange).toHaveBeenLastCalledWith(["VIP client"]);
    });

    it("suggests the board's labels the task does not have yet", () => {
        const { container } = render(<Picker onChange={vi.fn()} />);
        const options = [...container.querySelectorAll("datalist option")].map((o) => o.getAttribute("value"));
        expect(options).toEqual(["billing"]);
    });
});
