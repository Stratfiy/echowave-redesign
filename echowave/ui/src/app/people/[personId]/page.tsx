"use client";

import { useParams } from "next/navigation";

import { PeopleGate } from "@/components/people/PeopleGate";
import { PersonPage } from "@/components/people/PersonPage";

/** One contact: the brief, how to reach them and what happened. */
export default function PersonRoute() {
    const params = useParams<{ personId: string }>();
    return (
        <PeopleGate>
            <PersonPage personId={params.personId} />
        </PeopleGate>
    );
}
