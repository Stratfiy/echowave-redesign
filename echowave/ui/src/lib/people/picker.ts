/**
 * The Contact Picker API (Android Chrome): the person picks contacts from
 * their phone's own address book, one tap per contact. Nothing else in a
 * browser can read the phone's contacts, so where it does not exist (iPhone,
 * desktop, the Electron app) the button is not shown at all.
 */

export type PickedContact = {
    name?: string[];
    tel?: string[];
    email?: string[];
};

type ContactsManager = {
    select: (properties: string[], options?: { multiple?: boolean }) => Promise<PickedContact[]>;
    getProperties?: () => Promise<string[]>;
};

export function contactPicker(nav: Navigator | undefined = typeof navigator === "undefined" ? undefined : navigator): ContactsManager | null {
    if (!nav) return null;
    const contacts = (nav as Navigator & { contacts?: ContactsManager }).contacts;
    if (!contacts || typeof contacts.select !== "function") return null;
    return contacts;
}

export async function pickContacts(manager: ContactsManager): Promise<PickedContact[]> {
    let properties = ["name", "tel", "email"];
    if (manager.getProperties) {
        const supported = await manager.getProperties();
        properties = properties.filter((p) => supported.includes(p));
    }
    return manager.select(properties, { multiple: true });
}
