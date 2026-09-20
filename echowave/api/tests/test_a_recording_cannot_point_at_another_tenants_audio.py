"""A recording row may only name an object inside its own organization.

``POST /workflow-recordings/`` took ``storage_key`` from the request body and
wrote it to the row verbatim. The two-step upload is what made that look
reasonable: ``/upload-url`` mints a presigned PUT for
``recordings/{organization_id}/{recording_id}/{filename}`` and hands the key
back, so the client is expected to echo it. Nothing checked that it did.

The row lookup behind playback *is* org-scoped, which is what hid this. An
account could only read rows belonging to itself -- but it could create a row
belonging to itself whose ``storage_key`` addressed another tenant's object,
and then the scoped lookup returned it and the runtime fetched the bytes. The
tenant check ran on the wrong thing: the row, not the object the row points at.

AGENTS.md states the rule this broke -- never trust an id from the request body
to imply ownership. A storage key is an id.

So the key is validated against the caller's own prefix, and the shape of that
prefix is owned by one function rather than written out at each call site.
"""

from __future__ import annotations

import pytest

from api.services.workflow import recording_storage


class TestTheKeyShapeIsOwnedInOnePlace:
    def test_a_key_is_built_under_the_organizations_own_prefix(self):
        key = recording_storage.storage_key_for(
            organization_id=42, recording_id="ab12cd34", filename="greeting.mp3"
        )
        assert key.startswith(recording_storage.prefix_for(42))
        assert key == "recordings/42/ab12cd34/greeting.mp3"

    def test_one_organizations_prefix_is_not_a_prefix_of_anothers(self):
        """`recordings/4` is a string prefix of `recordings/42/...`.

        A naive ``startswith`` on the bare id would let org 4 claim org 42's
        objects, so the trailing separator is load-bearing rather than
        cosmetic.
        """
        assert not recording_storage.prefix_for(42).startswith(
            recording_storage.prefix_for(4)
        )
        assert (
            recording_storage.belongs_to_organization("recordings/42/ab12cd34/a.mp3", 4)
            is False
        )


class TestWhichKeysAnOrganizationMayClaim:
    def test_its_own_key_is_accepted(self):
        assert (
            recording_storage.belongs_to_organization(
                "recordings/42/ab12cd34/greeting.mp3", 42
            )
            is True
        )

    def test_another_tenants_key_is_refused(self):
        """The bug: this was written to the row and later played back."""
        assert (
            recording_storage.belongs_to_organization(
                "recordings/7/zz99yy88/confidential-call.mp3", 42
            )
            is False
        )

    @pytest.mark.parametrize(
        "key,why",
        [
            ("recordings/42/../7/x.mp3", "traversal back out of the prefix"),
            ("recordings/42/ab/../../7/x.mp3", "traversal from deeper in"),
            ("recordings/42/%2e%2e/7/x.mp3", "percent-encoded traversal"),
            ("/recordings/42/x.mp3", "absolute path, a different key entirely"),
            ("recordings//42/x.mp3", "empty segment"),
            ("kyc-documents/42/passport.pdf", "another bucket's namespace"),
            ("", "empty"),
            ("   ", "blank"),
        ],
    )
    def test_keys_that_are_not_plainly_inside_the_prefix_are_refused(self, key, why):
        assert recording_storage.belongs_to_organization(key, 42) is False, why

    def test_a_key_with_a_backslash_is_refused(self):
        """Windows-style separators are not ours, and normalising them is a
        second parsing rule that can disagree with the storage backend's."""
        assert (
            recording_storage.belongs_to_organization("recordings/42\\..\\7\\x.mp3", 42)
            is False
        )


class TestTheRouteRefusesRatherThanStoring:
    async def test_creating_a_recording_over_another_tenants_key_is_rejected(self):
        from fastapi import HTTPException

        from api.routes.workflow_recording import _validated_storage_key

        with pytest.raises(HTTPException) as exc:
            _validated_storage_key("recordings/7/zz99yy88/x.mp3", organization_id=42)
        # 400, not 403: the caller is authenticated and entitled to this
        # endpoint. What is wrong is the key they sent.
        assert exc.value.status_code == 400

    async def test_its_own_key_passes_through_unchanged(self):
        from api.routes.workflow_recording import _validated_storage_key

        key = "recordings/42/ab12cd34/greeting.mp3"
        assert _validated_storage_key(key, organization_id=42) == key

    def test_the_upload_step_and_the_create_step_agree_on_the_shape(self):
        """If /upload-url ever built a key the validator rejects, every upload
        would fail on the second call -- so the two are tied to one function
        and this asserts the round trip."""
        from api.routes.workflow_recording import _validated_storage_key

        key = recording_storage.storage_key_for(
            organization_id=42, recording_id="ab12cd34", filename="greeting.mp3"
        )
        assert _validated_storage_key(key, organization_id=42) == key


class TestTheShapeRuleDoesNotBreakOrdinaryUploads:
    """A shape rule that refuses real filenames is a shape rule that takes the
    feature down. ``/upload-url`` takes the browser's filename, so whatever it
    builds must be something the create step accepts -- otherwise every upload
    of a file with a bracket in its name fails on the second call, and the
    error points at the key rather than at the name.

    So ``storage_key_for`` reduces the filename to the shape rather than
    hoping it already matches, and these are the round trips that must hold.
    """

    @pytest.mark.parametrize(
        "filename",
        [
            "greeting.mp3",
            "my recording (final).mp3",
            "Recording 2026-01-02 15:04:05.wav",
            "greeting#1&2.mp3",
            "ग्रीटिंग.mp3",
            "already/nested.mp3",
            "../escape.mp3",
            "..",
            "",
        ],
    )
    def test_whatever_the_browser_sends_round_trips(self, filename):
        from api.routes.workflow_recording import _validated_storage_key

        key = recording_storage.storage_key_for(
            organization_id=42, recording_id="ab12cd34", filename=filename
        )
        assert recording_storage.belongs_to_organization(key, 42) is True
        assert _validated_storage_key(key, organization_id=42) == key

    def test_a_filename_keeps_its_extension_where_it_has_one(self):
        """The backends and the browser both read the suffix, so losing it
        turns a playable mp3 into an octet-stream download."""
        key = recording_storage.storage_key_for(
            organization_id=42, recording_id="ab12cd34", filename="my (take 2).mp3"
        )
        assert key.endswith(".mp3")

    def test_two_uploads_do_not_collide_into_one_object(self):
        """Sanitising maps many filenames onto one -- "a#b.mp3" and "a&b.mp3"
        both become "a_b.mp3" -- so what keeps two uploads apart must be
        something else.

        It is the recording id: ``_generate_unique_recording_id`` mints a fresh
        one per file, including per file within a batch, and it is a directory
        component of the key. This pins that, because if the id ever stopped
        being part of the key, sanitising would start overwriting recordings
        with no error anywhere.
        """
        first = recording_storage.storage_key_for(
            organization_id=42, recording_id="ab12cd34", filename="a#b.mp3"
        )
        second = recording_storage.storage_key_for(
            organization_id=42, recording_id="zz99yy88", filename="a&b.mp3"
        )
        assert first != second
        assert "ab12cd34" in first and "zz99yy88" in second
