"""Deterministic port of PAT's form message builder (scanAndBuild + buildXML).

Pure over (template, variables, context). <DateTime> and friends come from the
context (stamped once at compose, reused on retry) — never the clock here — so
preview == send == retry byte-for-byte."""
import re
from dataclasses import dataclass, field

from backend.integrations.winlink.b2f import B2FAttachment
from backend.modules.forms.library import forms_library_dir

XML_FILE_VERSION = "1.0"


class FormBuildError(Exception):
    """Template missing or unparseable."""


@dataclass
class BuildContext:
    callsign: str
    datetime_stamp: str  # "YYYY/MM/DD HH:MM" — stamped once, reused on retry
    grid: str = ""
    app_version: str = "SkyNetControl 0.1.0"


@dataclass
class ComposedForm:
    to: str
    cc: str
    subject: str
    body: str
    attachment: B2FAttachment
    display_form: str = ""
    reply_template: str = ""
    variables: dict = field(default_factory=dict)


# Recognized insertion-tag names (lowercased). Public for callers/tests that
# want to know which bare <Tags> the builder resolves.
INSERTION_TAGS = {
    "msgsender", "callsign", "senders_callsign",
    "datetime", "udtg", "date", "time",
    "gridsquare", "grid_square", "gps", "position", "latlon",
}


# Insertion tags → resolver over (context). Ported from PAT's insertion_tags set;
# unresolved tags render blank (matching PAT with unconfigured station data).
def _insertion_value(tag: str, ctx: BuildContext, variables: dict) -> str:
    t = tag.lower()
    if t in ("msgsender", "callsign", "senders_callsign"):
        return ctx.callsign
    if t in ("datetime", "udtg", "date", "time"):
        return ctx.datetime_stamp
    if t in ("gridsquare", "grid_square", "gps", "position", "latlon"):
        return ctx.grid
    return ""  # unknown/unsourced tag → blank


_VAR_RE = re.compile(r"<Var\s+([A-Za-z0-9_]+)\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<([A-Za-z0-9_]+)\s*>")  # bare insertion tags
_PROMPT_RE = re.compile(r"\{(Ask|Select)[^}]*\}", re.IGNORECASE)
_FORM_RE = re.compile(r"^\s*Form\s*:\s*(.+)$", re.IGNORECASE)
_REPLY_RE = re.compile(r"^\s*ReplyTemplate\s*:\s*(.+)$", re.IGNORECASE)


def _read_template(template_path: str) -> str:
    base = forms_library_dir().resolve()
    target = (base / template_path).resolve()
    if base != target and base not in target.parents:
        raise FormBuildError("template path escapes the forms library")
    if not target.is_file():
        raise FormBuildError(f"template not found: {template_path}")
    return target.read_text(errors="replace")


def _substitute(text: str, variables: dict, ctx: BuildContext) -> str:
    # <Var Name> first (case-insensitive lookup), then bare insertion tags.
    lower_vars = {k.lower(): v for k, v in variables.items()}

    def var_sub(m):
        return str(lower_vars.get(m.group(1).lower(), ""))

    text = _VAR_RE.sub(var_sub, text)

    def tag_sub(m):
        return _insertion_value(m.group(1), ctx, variables)

    return _TAG_RE.sub(tag_sub, text)


def _parse(template_text: str):
    """Split control lines from the Msg body. Returns (control: dict, defs: dict,
    body_template: str, display_form: str, reply_template: str)."""
    control = {}
    defs = {}
    display_form = ""
    reply_template = ""
    lines = template_text.splitlines()
    body_lines = []
    in_body = False
    for line in lines:
        if in_body:
            body_lines.append(line)
            continue
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("msg:"):
            in_body = True
            continue
        fm = _FORM_RE.match(line)
        if fm:
            display_form = fm.group(1).split(",")[0].strip()
            continue
        rm = _REPLY_RE.match(line)
        if rm:
            reply_template = rm.group(1).strip()
            continue
        if low.startswith("def:"):
            body = stripped[4:].strip()
            if "=" in body:
                k, _, v = body.partition("=")
                k = k.strip()
                if re.fullmatch(r"[A-Za-z0-9_]+", k):
                    defs[k] = v.strip()
            continue
        for key in ("to", "cc", "subject", "subj"):
            if low.startswith(key + ":"):
                control["subject" if key == "subj" else key] = stripped[len(key) + 1:].strip()
                break
    return control, defs, "\n".join(body_lines), display_form, reply_template


def build_form_message(template_path: str, variables: dict, context: BuildContext) -> ComposedForm:
    text = _read_template(template_path)
    control, defs, body_template, display_form, reply_template = _parse(text)
    # Def: defaults fill gaps; explicit non-empty variables win.
    merged = dict(defs)
    merged.update(variables)
    for k, v in defs.items():
        if not merged.get(k):
            merged[k] = v

    to = _substitute(control.get("to", ""), merged, context)
    cc = _substitute(control.get("cc", ""), merged, context)
    subject = _substitute(control.get("subject", ""), merged, context)
    body = _substitute(body_template, merged, context)

    xml = build_form_xml(display_form, merged, context)
    attachment = B2FAttachment(
        filename=_xml_name(display_form),
        content_type="application/xml",
        data=xml.encode("utf-8"),
    )
    return ComposedForm(
        to=to, cc=cc, subject=subject, body=body, attachment=attachment,
        display_form=display_form, reply_template=reply_template, variables=merged,
    )


def _xml_name(display_form: str) -> str:
    stem = (display_form.rsplit(".", 1)[0] if "." in display_form else display_form) or "Form"
    name = f"RMS_Express_Form_{stem}.xml"
    return name[:255]


def build_form_xml(display_form: str, variables: dict, context: BuildContext) -> str:
    import xml.sax.saxutils as su

    parts = [
        "<?xml version=\"1.0\"?>",
        "<RMS_Express_Form>",
        "  <form_parameters>",
        f"    <xml_file_version>{XML_FILE_VERSION}</xml_file_version>",
        f"    <rms_express_version>{su.escape(context.app_version)}</rms_express_version>",
        f"    <submission_datetime>{su.escape(context.datetime_stamp)}</submission_datetime>",
        f"    <senders_callsign>{su.escape(context.callsign)}</senders_callsign>",
        f"    <grid_square>{su.escape(context.grid)}</grid_square>",
        f"    <display_form>{su.escape(display_form)}</display_form>",
        "  </form_parameters>",
        "  <variables>",
    ]
    for key in sorted(variables.keys()):
        if not re.fullmatch(r"[A-Za-z0-9_]+", key):
            continue  # skip non-conforming variable names (can't be valid XML element names)
        parts.append(f"    <{key}>{su.escape(str(variables[key]))}</{key}>")
    parts.append("  </variables>")
    parts.append("</RMS_Express_Form>")
    return "\n".join(parts) + "\n"


def find_unfilled_prompts(template_path: str, variables: dict) -> list[dict]:
    text = _read_template(template_path)
    prompts = []
    for i, m in enumerate(_PROMPT_RE.finditer(text)):
        kind = m.group(1).lower()
        prompts.append({"index": i, "kind": kind, "raw": m.group(0)})
    return prompts


def clear_template_cache_if_any() -> None:
    """No builder-level cache; hook kept for test symmetry."""
    return None
