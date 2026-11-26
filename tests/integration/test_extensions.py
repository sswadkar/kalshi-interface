"""Extensions are discovered by convention; the core app knows none by name."""
from fastapi import FastAPI

from kalshi_workstation.extensions import load_extensions


def test_modules_with_register_are_loaded(tmp_path, monkeypatch):
    package = tmp_path / "synthetic_extensions"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "adds_page.py").write_text(
        "def register(app):\n"
        "    app.state.extra_pages.append({'path': '/synthetic', 'label': 'Synthetic'})\n")
    (package / "helper.py").write_text("VALUE = 1\n")  # no register(): ignored
    monkeypatch.syspath_prepend(str(tmp_path))

    app = FastAPI()
    app.state.extra_pages = []
    loaded = load_extensions(app, [str(package)], "synthetic_extensions")

    assert loaded == ["adds_page"]
    assert app.state.extra_pages == [{"path": "/synthetic", "label": "Synthetic"}]


def test_app_runs_without_extensions(tmp_path):
    from kalshi_workstation.app import create_app
    app = create_app(data_dir=tmp_path, with_extensions=False)
    assert app.state.extensions == []
    assert app.state.extra_pages == []
