"""Exceptions of the INWX client."""


class InwxError(Exception):
    """Base error of the INWX client."""


class InwxConnectionError(InwxError):
    """Error to indicate the API could not be reached."""


class InwxAuthError(InwxError):
    """Error to indicate the credentials were rejected."""


class InwxTwoFactorRequiredError(InwxAuthError):
    """Error to indicate the account needs a 2FA shared secret."""


class InwxTwoFactorError(InwxAuthError):
    """Error to indicate the 2FA code was rejected."""


class InwxInvalidSharedSecretError(InwxAuthError):
    """Error to indicate the 2FA shared secret is not valid Base32."""


class InwxApiError(InwxError):
    """Error to indicate the API answered with an error code or an invalid response."""

    def __init__(self, code: int | None, message: str) -> None:
        """Initialize the error."""
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
