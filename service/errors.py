"""Stable error codes for the Music Service.

Message text is meant for a user; the code is what clients and logs may rely on.
Neither ever carries credentials, signed media URLs or a machine path.
"""


class ServiceError(RuntimeError):
    """Base error with a fixed, lowercase code."""

    code = 'service_error'

    def __init__(self, message='', code=None):
        self.code = code or type(self).code
        super().__init__(message or self.code)


class ProviderError(ServiceError):
    """A source refused or returned something the service will not accept."""

    code = 'provider_error'


class LimitError(ServiceError):
    """A configured resource limit would be exceeded; the numbers stay visible."""

    code = 'limit_exceeded'
