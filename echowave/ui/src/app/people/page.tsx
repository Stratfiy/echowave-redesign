"use client";

import { PeopleGate } from "@/components/people/PeopleGate";
import { PeopleList } from "@/components/people/PeopleList";

/** People: the person's own contacts, with context (PEOPLE.md). */
export default function PeoplePage() {
    return (
        <PeopleGate>
            <PeopleList />
        </PeopleGate>
    );
}
