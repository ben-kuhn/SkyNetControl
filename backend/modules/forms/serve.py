"""Serve a form's INPUT html (with its own JS intact) plus a collector shim,
for loading into a locked sandboxed iframe during composition.

Security: the returned HTML is ONLY ever loaded into <iframe sandbox="allow-scripts">
(no allow-same-origin → opaque origin) with a restrictive CSP. The shim's only
capability is postMessage to the parent — it cannot reach our API/cookies/network."""
import json

from backend.modules.forms.library import forms_library_dir

# Injected before </body>. Reads the form's fields on submit/Done and posts them
# to the parent. Prefill is applied on load. connect-src 'none' + opaque origin
# mean this script cannot do anything except postMessage.
COLLECTOR_SHIM = """
<script>
(function () {
  var PREFILL = __PREFILL__;
  function collect() {
    var form = document.querySelector("form");
    var vars = {};
    if (form) {
      var els = form.querySelectorAll("input[name], select[name], textarea[name]");
      els.forEach(function (el) {
        if (el.type === "checkbox" || el.type === "radio") {
          if (el.checked) vars[el.name] = el.value;
        } else {
          vars[el.name] = el.value;
        }
      });
    }
    parent.postMessage({ type: "skynet-form-vars", variables: vars }, "*");
  }
  function applyPrefill() {
    var form = document.querySelector("form");
    if (!form) return;
    var els = form.querySelectorAll("[name]");
    els.forEach(function (el) {
      var lower = el.name.toLowerCase();
      if (Object.prototype.hasOwnProperty.call(PREFILL, lower)) {
        el.value = PREFILL[lower];
      }
    });
  }
  window.addEventListener("message", function (e) {
    if (e.data && e.data.type === "skynet-collect") collect();
  });
  document.addEventListener("submit", function (e) { e.preventDefault(); collect(); }, true);
  if (document.readyState !== "loading") applyPrefill();
  else document.addEventListener("DOMContentLoaded", applyPrefill);
})();
</script>
"""


def _resolve(rel_path: str):
    base = forms_library_dir().resolve()
    target = (base / rel_path).resolve()
    if base != target and base not in target.parents:
        raise ValueError("path escapes the forms library")
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    return target


def render_input_form(rel_path: str, prefill: dict | None = None) -> str:
    target = _resolve(rel_path)
    html = target.read_text(errors="replace")
    shim = COLLECTOR_SHIM.replace("__PREFILL__", json.dumps(prefill or {}).replace("</", "<\\/"))
    lower = html.lower()
    idx = lower.rfind("</body>")
    if idx != -1:
        return html[:idx] + shim + html[idx:]
    return html + shim
