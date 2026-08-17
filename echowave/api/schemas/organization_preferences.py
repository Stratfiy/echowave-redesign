from pydantic import BaseModel, Field


class OrganizationPreferences(BaseModel):
    test_phone_number: str | None = None
    timezone: str | None = None

    #: What to do when a slot is set to the account's own key and no usable key
    #: is stored for it — the key was never added, was paused while rotating at
    #: the vendor, or cannot be decrypted.
    #:
    #: **Off by default, and the default is the safe one.** Falling back runs
    #: the call on Decibyl's key and bills it at the published rate, which is a
    #: charge the account did not ask for; refusing is the only option that
    #: cannot surprise anyone. The customer turns this on knowingly, against
    #: copy that says what it costs.
    #:
    #: Neither option is silence. Before this existed the section kept its
    #: vendor and an empty key, the call connected, and the caller heard nothing
    #: for its duration while we paid the carrier for the minutes.
    byok_fallback_to_managed: bool = Field(
        default=False,
        description=(
            "When a slot is set to your own key and no usable key is stored, "
            "run the call on Decibyl's key and bill it at the published rate. "
            "Off means the call is refused instead."
        ),
    )
