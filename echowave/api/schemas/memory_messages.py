from pydantic import BaseModel


class MemoryMessages(BaseModel):
    """The person's own switches for what memory may say unasked
    (UserConfigurationKey.MEMORY_MESSAGES). All off until turned on."""

    sunday_review: bool = False
    connections: bool = False
    spaced_recall: bool = False
