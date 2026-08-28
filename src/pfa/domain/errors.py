class PFAError(Exception):
    """Base exception for expected application errors."""


class ValidationError(PFAError):
    """Input or domain validation failed."""


class ImportRowError(PFAError):
    """A single source row could not be imported."""
