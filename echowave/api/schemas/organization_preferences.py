from pydantic import BaseModel, Field

from api.schemas.workflow_configurations import AgentSchedule


class OrganizationPreferences(BaseModel):
    test_phone_number: str | None = None
    timezone: str | None = None

    #: The hours this business keeps, for the agents that do not set their own.
    #:
    #: Optional, and ``None`` means the account has never been asked -- most
    #: businesses have one set of opening hours and answering the question
    #: twice is how the two answers start to disagree.
    #:
    #: An agent with its own enabled schedule ignores this entirely. An agent
    #: that wants to answer when the business is shut -- an after-hours
    #: emergency line -- says so by keeping its own schedule rather than by
    #: switching this off, and "all day" is one window of 00:00 to 24:00.
    business_hours: AgentSchedule | None = None

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
    #: Whether this account may bring its own vendor keys at all. Off for
    #: every account until Decibyl switches it on, from the staff account
    #: page: a customer's own key changes what a call costs us and who is
    #: billed for it, and that is a commercial arrangement, not a setting a
    #: customer flips. Ignored on the customer's own preferences save.
    own_keys_allowed: bool = Field(
        default=False,
        description="Staff-set. May this account store and run on its own vendor keys.",
    )
    byok_fallback_to_managed: bool = Field(
        default=False,
        description=(
            "When a slot is set to your own key and no usable key is stored, "
            "run the call on Decibyl's key and bill it at the published rate. "
            "Off means the call is refused instead."
        ),
    )
