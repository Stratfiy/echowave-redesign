"""People: numbers, addresses and files read into one shape (no database)."""

from __future__ import annotations

import pytest

from api.services.people import imports, normalise


@pytest.mark.parametrize(
    "raw,want",
    [
        ("+91 98765 43210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("98765-43210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("(+91) 98765 43210 ext. 12", "+919876543210"),
        ("0044 20 7946 0958", "+442079460958"),
        ("+1 (415) 555-0100", "+14155550100"),
        ("12345", None),
        ("2345678", None),  # a landline with no area code reaches nobody
        ("", None),
        (None, None),
    ],
)
def test_phones_become_e164_with_india_the_default(raw, want):
    assert normalise.phone(raw) == want


def test_emails_are_lower_case_and_checked():
    assert normalise.email(" Ravi@Example.IN ") == "ravi@example.in"
    assert normalise.email("mailto:a@b.co") == "a@b.co"
    assert normalise.email("not an address") is None


def test_a_contact_with_no_name_is_named_by_what_it_has():
    item = normalise.Incoming(phones=["9876543210"]).clean()
    assert item.name == "+919876543210"
    assert normalise.Incoming(phones=["12"]).clean() is None


VCARD = b"""BEGIN:VCARD
VERSION:3.0
FN:Ravi Kumar
N:Kumar;Ravi;;;
ORG:Kumar Traders;Sales
item1.TEL;TYPE=CELL:+91 98765 43210
EMAIL;TYPE=INTERNET:ravi@example.in
END:VCARD
BEGIN:VCARD
VERSION:2.1
N;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=E0=A4=AA=E0=A5=8D=E0=A4=B0=E0=A4=BF=E0=A4=AF=E0=A4=BE;;;;
TEL;CELL:09812345678
END:VCARD
BEGIN:VCARD
VERSION:4.0
FN:Folded
 Name
TEL:+44 20 7946 0958
END:VCARD
BEGIN:VCARD
VERSION:3.0
NOTE:nobody
END:VCARD
"""


def test_a_vcard_file_from_a_phone():
    contacts, skipped, source = imports.parse_file("contacts.vcf", VCARD)
    assert source == "vcard"
    assert skipped == 1
    ravi, priya, folded = contacts
    assert (ravi.name, ravi.phones, ravi.emails, ravi.company) == (
        "Ravi Kumar",
        ["+919876543210"],
        ["ravi@example.in"],
        "Kumar Traders",
    )
    assert priya.name == "प्रिया"  # quoted-printable, old Android export
    assert priya.phones == ["+919812345678"]
    assert folded.name == "FoldedName"


GOOGLE_CSV = (
    "First Name,Last Name,E-mail 1 - Value,Phone 1 - Value,Organization Name\n"
    "Priya,Sharma,priya@example.in,09812345678 ::: +91 99887 76655,Sharma Co\n"
    "No,Contact,,,\n"
).encode()


def test_a_google_contacts_csv():
    contacts, skipped, source = imports.parse_file("contacts.csv", GOOGLE_CSV)
    assert source == "csv" and skipped == 1
    (priya,) = contacts
    assert priya.name == "Priya Sharma"
    assert priya.phones == ["+919812345678", "+919988776655"]
    assert priya.company == "Sharma Co"


def test_a_csv_with_no_phone_or_email_column_is_refused_in_words():
    with pytest.raises(imports.ImportRefused) as refused:
        imports.parse_file("x.csv", b"Name,City\nRavi,Pune\n")
    assert "Phone" in str(refused.value)


def test_other_files_are_refused():
    with pytest.raises(imports.ImportRefused):
        imports.parse_file("photo.png", b"\x89PNG")


def test_the_contact_picker_shape():
    contacts, skipped = imports.parse_picker(
        [
            {"name": ["Anil"], "tel": ["99887 76655"], "email": []},
            {"name": [], "tel": [], "email": []},
        ]
    )
    assert skipped == 1
    assert contacts[0].phones == ["+919988776655"]
