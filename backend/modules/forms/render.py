"""Render Winlink form templates to read-only sanitized HTML.

Winlink Express templates use ``{variable_name}`` curly-brace tokens as
substitution placeholders. For example, a check-in template contains markup
like ``value="{MsgSender}"`` and ``value="{Latitude}"``. The Winlink
Express client substitutes these tokens with actual field values before
displaying the form. This module replicates that substitution server-side
so that submitted form data can be rendered as a read-only view without
running any JavaScript.

Discovery note (2026-06-20): the real ``Standard_Forms.zip`` distributed
at downloads.winlink.org was inspected. Both the initial and viewer
check-in templates (``General Forms/Winlink_Check_In_Initial.html`` and
``Winlink_Check_In_Viewer.html``) use both placeholder shapes: bare
``{VarName}`` tokens inside ``<input value="...">`` attributes, and
``{var VarName}`` tokens (with the literal ``var`` prefix) in visible
body text. We substitute both. Templates that compute display values
purely via JavaScript (e.g. the map/GPS functions) degrade gracefully
to the key-value fallback when the substituted fields produce empty
strings.
"""
from __future__ import annotations

import re
from html import escape

import bleach

from backend.modules.forms.library import find_template


_ALLOWED_TAGS = [
    "div", "span", "p",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "td", "th",
    "dl", "dt", "dd",
    "ul", "ol", "li",
    "br", "hr",
    "b", "i", "u", "strong", "em",
    "label", "input",
    "html", "body", "head", "title", "meta", "style",
]
_ALLOWED_ATTRS = {
    "*": ["class", "id"],
    "div": ["style"],
    "span": ["style"],
    "td": ["style"],
    "th": ["style"],
    "table": ["style"],
    "input": ["type", "name", "value", "readonly", "disabled", "style"],
    "label": ["for"],
    "meta": ["charset", "name", "content", "http-equiv"],
}

# Two placeholder shapes occur in real Winlink templates: bare
# ``{VarName}`` inside ``<input value="...">`` attributes, and the
# ``{var VarName}`` form (with the literal ``var`` prefix and whitespace)
# in visible body text. Accept either — lookup is case-insensitive on the
# variable name regardless.
_PLACEHOLDER_RE = re.compile(r"\{(?:var\s+)?([A-Za-z0-9_]+)\}", re.IGNORECASE)

# Regex to strip <script>...</script> blocks (including content) before bleach.
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)

_CSP_META = (
    '<meta http-equiv="Content-Security-Policy" '
    'content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:">'
)


def _substitute(template_html: str, variables: dict[str, str]) -> str:
    """Replace ``{var_name}`` tokens with the corresponding variable value.

    Missing variables produce empty strings (not the literal token).
    Values are not HTML-escaped here — sanitization runs on the full
    rendered document afterward.
    """
    lookup = {k.lower(): v for k, v in variables.items()}

    def replace(match: re.Match) -> str:
        name = match.group(1).lower()
        return lookup.get(name, "")

    return _PLACEHOLDER_RE.sub(replace, template_html)


def _render_kv_fallback(variables: dict[str, str]) -> str:
    """Build a minimal HTML table from variable name/value pairs.

    Used when the template isn't on disk. Variable names + values are
    HTML-escaped before composition; the result is also run through the
    sanitizer for defense-in-depth.
    """
    rows = "".join(
        f"<tr><td>{escape(name)}</td><td>{escape(value)}</td></tr>"
        for name, value in variables.items()
    )
    return (
        "<!DOCTYPE html>"
        "<html><head>"
        '<meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:">'
        "<style>body { font-family: sans-serif; padding: 1em; } "
        "table { border-collapse: collapse; } "
        "td { padding: 4px 8px; border-bottom: 1px solid #ddd; } "
        "td:first-child { font-weight: 600; color: #555; }</style>"
        "</head><body>"
        "<table><tbody>" + rows + "</tbody></table>"
        "</body></html>"
    )


def _sanitize(html: str) -> str:
    """Pass the rendered HTML through bleach with the allowlist above.

    Script blocks are stripped (tag + inner content) before bleach runs,
    because bleach with ``strip=True`` removes the tag but leaves the text
    content of ``<script>`` elements in the output.
    """
    # Strip <script> blocks including their content first.
    html = _SCRIPT_RE.sub("", html)
    cleaned = bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        strip=True,
        strip_comments=True,
    )
    if "Content-Security-Policy" not in cleaned:
        cleaned = _CSP_META + cleaned
    return cleaned


def render_form_view(template_filename: str, variables: dict[str, str]) -> str | None:
    """Render a Winlink form to read-only sanitized HTML.

    Returns None only when there is nothing to show (no template AND no
    variables). When the named template is missing from disk OR when
    rendering it fails, returns a sanitized key-value HTML table built
    from ``variables``.
    """
    path = find_template(template_filename) if template_filename else None

    if path is not None:
        try:
            template_html = path.read_text(errors="replace")
            substituted = _substitute(template_html, variables)
            return _sanitize(substituted)
        except OSError:
            # File disappeared between index build and read — fall through.
            pass

    if not variables:
        return None
    return _sanitize(_render_kv_fallback(variables))
