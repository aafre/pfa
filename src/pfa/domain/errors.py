class PFAError(Exception):
    """Base exception for expected application errors."""


class ValidationError(PFAError):
    """Input or domain validation failed."""


class ImportRowError(PFAError):
    """A single source row could not be imported."""


class UploadRejected(PFAError):
    """A staged upload failed a policy check (size, type, or signature)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class BatchError(PFAError):
    """An import batch lifecycle operation could not proceed."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
