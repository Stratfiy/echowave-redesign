/**
 * The 3D pictures: a job's picture agrees with its icon, every name is a real
 * file, and none of them is purple.
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { art3d, industryArt, jobArt } from "@/lib/art";

const PURPLE = ["lab", "chart", "bulb", "magic-trick", "chat-text"];

const JOBS = [
    "Clinic front desk",
    "Appointment reminders",
    "Quote desk",
    "Payment reminders",
    "Billing receipts",
    "Order delivery updates",
    "Inventory check",
    "Narayani Dental Clinic",
    "Restaurant reservations",
    "Front desk",
    "Support helpdesk",
    "Telecaller",
    "Customer survey",
    "WhatsApp enquiries",
    "Prospect outreach",
    "KYC documents",
    "Weekly report",
    "Expense clerk",
    "Data entry",
    "Leave approval",
    "Answer staff questions from our documents",
    "Returns desk",
    "Admissions counsellor",
    "Real estate listings",
    "Hotel reservation",
    "Voice note taker",
    "Ticket triage",
    "Sales coach",
    "Procurement document drafter",
    "RFQ and quote comparer",
    "PO follow-up and delivery chaser",
    "Invoice 3-way match",
    "Procurement",
    "Agent 3",
    "",
];

const INDUSTRIES = [
    "Healthcare",
    "Real estate",
    "Lending",
    "Education",
    "E-commerce",
    "Hospitality",
    "Any business",
    "Retail and D2C",
    "Logistics",
    "Procurement",
    "Something new",
];

describe("3D art", () => {
    it("serves from public/art/3d", () => {
        expect(art3d("rocket")).toBe("/art/3d/rocket.webp");
    });

    it("reads the job out of the name, in BotAvatar's order", () => {
        expect(jobArt("Appointment reminders")).toBe("calender");
        expect(jobArt("Quote desk")).toBe("file-text");
        expect(jobArt("Payment reminders")).toBe("wallet");
        expect(jobArt("Billing receipts")).toBe("rupee");
        expect(jobArt("Order delivery")).toBe("travel");
        expect(jobArt("Stock check")).toBe("cube");
        expect(jobArt("Narayani Dental Clinic")).toBe("notify-heart");
        expect(jobArt("Restaurant host")).toBe("tea-cup");
        expect(jobArt("Front desk")).toBe("headphone");
        expect(jobArt("Telecaller")).toBe("call-ringing");
        expect(jobArt("Customer survey")).toBe("notebook");
        expect(jobArt("WhatsApp enquiries")).toBe("chat-bubble");
    });

    it("has pictures for the jobs the icons do not know", () => {
        expect(jobArt("Prospect outreach")).toBe("megaphone");
        expect(jobArt("KYC documents")).toBe("shield");
        expect(jobArt("Weekly report")).toBe("target");
        expect(jobArt("Expense clerk")).toBe("credit-card");
        expect(jobArt("Data entry")).toBe("computer");
        expect(jobArt("Leave approval")).toBe("tick");
        expect(jobArt("Answer staff questions from our documents")).toBe("folder");
        expect(jobArt("Returns desk")).toBe("bag");
        expect(jobArt("Admissions counsellor")).toBe("notebook");
        expect(jobArt("Real estate listings")).toBe("map-pin");
        expect(jobArt("Hotel reservation")).toBe("tea-cup");
        expect(jobArt("Voice note taker")).toBe("mic");
        expect(jobArt("Ticket triage")).toBe("tools");
        expect(jobArt("Sales coach")).toBe("megaphone");
        expect(jobArt("Coach")).toBe("trophy");
    });

    it("falls back to the rocket, or to what the caller asks for", () => {
        expect(jobArt("Agent 3")).toBe("rocket");
        expect(jobArt("")).toBe("rocket");
        expect(jobArt("What happened this week?", "sphere")).toBe("sphere");
    });

    it("files industries", () => {
        expect(industryArt("Healthcare")).toBe("notify-heart");
        expect(industryArt("Lending")).toBe("money-bag");
        expect(industryArt("Retail and D2C")).toBe("gift");
        expect(industryArt("Something new")).toBe("cube");
    });

    it("never returns a purple picture, and every picture is a real file", () => {
        const names = [...JOBS.map((j) => jobArt(j)), ...JOBS.map((j) => jobArt(j, "sphere")), ...INDUSTRIES.map(industryArt)];
        for (const name of names) {
            expect(PURPLE).not.toContain(name);
            expect(existsSync(join(process.cwd(), "public", art3d(name)))).toBe(true);
        }
    });

    it("every picture has a transparent background", () => {
        // A white square behind each one shows as a white tile on every
        // tinted surface and in dark mode. WebP carries alpha in VP8X.
        const dir = join(process.cwd(), "public", "art", "3d");
        const files = readdirSync(dir).filter((f) => f.endsWith(".webp"));
        expect(files.length).toBeGreaterThan(40);
        for (const file of files) {
            const head = readFileSync(join(dir, file)).subarray(0, 21);
            expect(head.subarray(12, 16).toString("latin1"), file).toBe("VP8X");
            expect(head[20] & 0x10, file).toBe(0x10);
        }
    });
});

describe("the roles on today's shelf", () => {
    it("gives outreach its megaphone, not a telephone", () => {
        expect(jobArt("Outbound Prospecting")).toBe("megaphone");
    });

    it("gives each procurement desk its own picture, not a quotation, a lorry or a bill", () => {
        // As the marketplace card asks: the role's name and its function.
        expect(jobArt("Procurement document drafter Do the paperwork")).toBe("file-text");
        expect(jobArt("RFQ and quote comparer Buy from vendors")).toBe("calculator");
        expect(jobArt("PO follow-up and delivery chaser Send reminders")).toBe("clock");
        expect(jobArt("Invoice 3-way match Do the paperwork")).toBe("tick");
        expect(jobArt("Procurement")).toBe("file-text");
        expect(industryArt("Procurement")).toBe("bag");
    });

    it("leaves the office drafter and the invoice clerk as they were", () => {
        expect(jobArt("Document drafter to your format Do the paperwork")).toBe("notebook");
        expect(jobArt("Supplier invoice and PO clerk Do the paperwork")).toBe("file-text");
    });

    it("gives every industry on the shelf a picture of its own, not the fallback cube", () => {
        for (const industry of ["Manufacturing", "Financial services", "Recruitment and HR", "Procurement"]) {
            expect(industryArt(industry)).not.toBe("cube");
        }
    });
});
