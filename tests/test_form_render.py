from pathlib import Path


def test_render_substitutes_into_template(tmp_path, monkeypatch):
    from backend.modules.forms import library, render
    from backend.config import settings

    # Make forms_library_dir point at tmp_path.
    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    (forms_dir / "Test_Check_in.html").write_text(
        "<html><body>Callsign: {callsign}, Name: {name}</body></html>"
    )

    html = render.render_form_view("Test_Check_in.html", {"callsign": "KU0HN", "name": "Ben"})
    assert html is not None
    assert "KU0HN" in html
    assert "Ben" in html


def test_render_substitutes_var_prefix_placeholders(tmp_path, monkeypatch):
    """The real Winlink templates put visible-text placeholders in
    ``{var VarName}`` form (with the ``var`` prefix and a space), while
    `<input value="...">` attributes use the bare ``{VarName}`` form.
    Both shapes must substitute, and missing vars must produce empty
    strings — not the literal token."""
    from backend.modules.forms import library, render
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    (forms_dir / "Mixed.html").write_text(
        "<html><body>"
        "From: {var MsgSender} "
        "To: {var MsgTo} "
        "<input value=\"{Callsign}\"> "
        "Missing: '{var DoesNotExist}'"
        "</body></html>"
    )

    html = render.render_form_view(
        "Mixed.html",
        {"msgsender": "W9GM", "msgto": "W0NE", "callsign": "W9GM"},
    )
    assert html is not None
    assert "W9GM" in html
    assert "W0NE" in html
    assert "{var" not in html
    assert "Missing: ''" in html


def test_render_sanitizes_script_tags(tmp_path, monkeypatch):
    from backend.modules.forms import library, render
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    (forms_dir / "Bad.html").write_text(
        "<html><body><script>alert(1)</script>Hi {name}</body></html>"
    )

    html = render.render_form_view("Bad.html", {"name": "Ben"})
    assert html is not None
    assert "<script>" not in html
    assert "alert(1)" not in html
    assert "Ben" in html


def test_render_strips_event_handlers_and_javascript_urls(tmp_path, monkeypatch):
    from backend.modules.forms import library, render
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    (forms_dir / "Bad.html").write_text(
        '<html><body><a href="javascript:alert(1)" onclick="alert(2)">click {x}</a></body></html>'
    )

    html = render.render_form_view("Bad.html", {"x": "test"})
    assert html is not None
    assert "javascript:" not in html
    assert "onclick" not in html


def test_render_missing_template_returns_kv_fallback(tmp_path, monkeypatch):
    from backend.modules.forms import library, render
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    # forms/ dir does not exist.
    html = render.render_form_view("Nonexistent.html", {"callsign": "KU0HN", "name": "Ben"})
    assert html is not None  # KV fallback rendered
    assert "KU0HN" in html
    assert "Ben" in html
    # Variable names appear as labels in the table.
    assert "callsign" in html.lower()


def test_render_no_template_no_variables_returns_none(tmp_path, monkeypatch):
    from backend.modules.forms import library, render
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    html = render.render_form_view("Nonexistent.html", {})
    assert html is None


def test_find_template_case_insensitive(tmp_path, monkeypatch):
    from backend.modules.forms import library
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "MyTemplate.html").write_text("<html/>")

    found = library.find_template("mytemplate.html")
    assert found is not None
    assert found.name == "MyTemplate.html"


def test_find_template_walks_nested_dirs(tmp_path, monkeypatch):
    from backend.modules.forms import library
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    nested = tmp_path / "forms" / "Standard Forms" / "Generic"
    nested.mkdir(parents=True)
    (nested / "Buried.html").write_text("<html/>")

    found = library.find_template("Buried.html")
    assert found is not None
    assert "Generic" in str(found)


def test_render_template_path_includes_csp_meta(tmp_path, monkeypatch):
    """A template that doesn't include CSP gets the meta injected during render."""
    from backend.modules.forms import library, render
    from backend.config import settings

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    library.clear_template_cache()
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    (forms_dir / "NoCSP.html").write_text("<html><body>Hello {name}</body></html>")

    html = render.render_form_view("NoCSP.html", {"name": "Ben"})
    assert html is not None
    assert "Content-Security-Policy" in html
    assert "default-src 'none'" in html
    assert "Ben" in html
