"""Stable failures for malformed or contradictory persisted evidence."""


class EvidenceIntegrityError(ValueError):
    """Required evaluation evidence could not be trusted."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
