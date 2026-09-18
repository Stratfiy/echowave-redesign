"use client";

/**
 * Decibyl's About on its own page, for a deep link and for a phone, where
 * there is no room for a panel beside the thread.
 *
 * The body is `DecibylAbout`, shared with the panel that opens beside the
 * thread. It used to be written out here with a tab strip of its own that
 * disagreed with Home's -- this one offered History, Home's offered Tasks
 * and Requests, and which one you saw depended on which you had clicked.
 */

import { DecibylAbout } from "@/components/home/DecibylAbout";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

export default function DecibylAboutPage() {
    return (
        <>
            <PageHeader
                title="About Decibyl"
                description="What the assistant reads, what it can propose, and what it remembers."
            />
            <PageBody className="max-w-2xl">
                <DecibylAbout />
            </PageBody>
        </>
    );
}
