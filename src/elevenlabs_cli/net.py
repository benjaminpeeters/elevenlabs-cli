"""Downloading a file, with network and write failures reported apart (their remedies differ)."""

from __future__ import annotations

import http.client
import urllib.request
from pathlib import Path

from .errors import CliError

TIMEOUT = 60  # seconds without data before a download fails


class FetchError(CliError):
    """The file could not be fetched: network, server or timeout."""


class WriteError(CliError):
    """The file was fetched but could not be written."""


def download(url: str, target: Path) -> None:
    """Fetch ``url`` into ``target``; on failure no partial file is left behind.

    The body is read into memory before anything is written: the files fetched here (voice
    previews, denoise models) are a few megabytes, and keeping the two steps apart is what
    tells a broken connection from an unwritable folder.
    """
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            data = response.read()
    # URLError, timeouts and connection resets are OSError; a body cut short (IncompleteRead) or a
    # malformed status line are HTTPException, which is not
    except (OSError, http.client.HTTPException) as exc:
        raise FetchError(f"could not download {url}: {exc!r}") from exc
    try:
        target.write_bytes(data)
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise WriteError(f"cannot write {target}: {exc.strerror}") from exc
