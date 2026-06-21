"""Download + validate + extract the Winlink Standard Forms ZIP."""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from backend.auth.dns_pin import pin_dns
from backend.auth.service import _ssrf_guard_discovery_url_async
from backend.config import settings
from backend.modules.forms.library import clear_template_cache, forms_library_dir

logger = logging.getLogger(__name__)


DEFAULT_SOURCE_URL = "https://downloads.winlink.org/User%20Programs/Standard_Forms.zip"
DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
DEFAULT_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MB
DEFAULT_MAX_ENTRY_COUNT = 5000

ALLOWED_EXTENSIONS = {".html", ".htm", ".txt", ".xml", ".css"}

ZIP_MAGIC = b"PK\x03\x04"

_VERSION_RE = re.compile(r"_(\d+(?:\.\d+){1,3})\.zip$", re.IGNORECASE)


class FormsFetchError(Exception):
    """Raised when the forms library cannot be fetched or extracted."""


def _derive_version(filename: str, content_sha256: str) -> str:
    """Pull a version string from the filename if present; else use a SHA prefix."""
    m = _VERSION_RE.search(filename or "")
    if m:
        return m.group(1)
    return content_sha256[:12]


async def _download_zip(url: str, *, max_size_bytes: int) -> tuple[bytes, str]:
    """SSRF-guarded HTTPS download of a ZIP. Returns (bytes, served_filename)."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise FormsFetchError(f"forms.source_url must use https:// (got {parsed.scheme or '(none)'})")

    try:
        host, ip = await _ssrf_guard_discovery_url_async(url)
    except ValueError as exc:
        raise FormsFetchError(str(exc)) from exc

    with pin_dns(host, ip):
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content
            if len(content) > max_size_bytes:
                raise FormsFetchError(f"download exceeded max size {max_size_bytes} bytes")

    # Determine served filename (URL basename takes precedence; httpx doesn't
    # always surface Content-Disposition cleanly).
    served_filename = os.path.basename(parsed.path) or "Standard_Forms.zip"
    return content, served_filename


def _validate_and_extract(zip_bytes: bytes, dest_root: Path) -> None:
    """Extract a validated ZIP into dest_root (which must already exist and be empty)."""
    if not zip_bytes.startswith(ZIP_MAGIC):
        raise FormsFetchError("downloaded content is not a ZIP archive")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise FormsFetchError(f"invalid ZIP: {exc}") from exc

    entries = zf.infolist()
    if len(entries) > DEFAULT_MAX_ENTRY_COUNT:
        raise FormsFetchError(f"ZIP entry count {len(entries)} exceeds {DEFAULT_MAX_ENTRY_COUNT}")

    total = sum(info.file_size for info in entries)
    if total > DEFAULT_MAX_UNCOMPRESSED_BYTES:
        raise FormsFetchError(f"ZIP uncompressed size {total} exceeds limit {DEFAULT_MAX_UNCOMPRESSED_BYTES}")

    dest_real = dest_root.resolve()
    for info in entries:
        if info.is_dir():
            continue

        # Zip-slip guard runs before the extension allowlist so that a path-
        # traversal entry with a disallowed extension is still rejected loudly
        # rather than silently dropped (defence-in-depth: fail closed).
        target = (dest_root / info.filename).resolve()
        try:
            target.relative_to(dest_real)
        except ValueError:
            raise FormsFetchError(f"ZIP entry escapes destination: {info.filename!r}")

        # Drop disallowed extensions silently.
        ext = os.path.splitext(info.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)


async def fetch_and_install(url: str, *, max_size_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES) -> dict:
    """End-to-end: download, validate, extract, atomic-promote, update cache."""
    zip_bytes, served_filename = await _download_zip(url, max_size_bytes=max_size_bytes)

    state_dir = Path(settings.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Stage into a temp dir SIBLING of forms_library_dir() so the atomic
    # rename works (rename across filesystems is not atomic).
    final_dir = forms_library_dir()
    with tempfile.TemporaryDirectory(prefix="forms-new-", dir=str(state_dir)) as staging:
        staging_path = Path(staging)
        _validate_and_extract(zip_bytes, staging_path)

        # Promote: rename existing forms/ aside, move new in, then rm the old one.
        backup_path = state_dir / "forms.old"
        if backup_path.exists():
            shutil.rmtree(backup_path)
        if final_dir.exists():
            final_dir.rename(backup_path)
        # tempfile.TemporaryDirectory will try to clean up; rename it out first.
        shutil.move(str(staging_path), str(final_dir))
        # Recreate a placeholder so the context manager's cleanup doesn't error.
        staging_path.mkdir(exist_ok=True)
        if backup_path.exists():
            shutil.rmtree(backup_path, ignore_errors=True)

    clear_template_cache()

    content_sha = hashlib.sha256(zip_bytes).hexdigest()
    version = _derive_version(served_filename, content_sha)
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    return {"library_version": version, "last_fetched_at": now_iso}
