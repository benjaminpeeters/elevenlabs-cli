"""One exception type for every user-facing failure.

Commands raise ``CliError`` with a message that says what went wrong and, when
possible, what to do about it. ``cli.main`` prints it and exits non-zero.
"""


class CliError(Exception):
    """A failure the user must act on; the message is printed verbatim."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code
