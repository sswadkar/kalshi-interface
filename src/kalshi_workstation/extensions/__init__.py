"""Optional extensions, loaded at startup.

Every module or package in this directory that defines `register(app)` is
imported by `create_app` and called once. An extension may add routes, put its
service on `app.state`, append to `app.state.background_jobs`, and add navigation
links to `app.state.extra_pages`. The core application has no knowledge of any
particular extension; with this directory empty, it runs unchanged.
"""
import importlib
import logging
import pkgutil

logger = logging.getLogger(__name__)


def load_extensions(app, package_path=None, package_name=__name__):
    """Register every extension found; returns their names."""
    loaded = []
    for info in pkgutil.iter_modules(package_path or __path__, package_name + "."):
        module = importlib.import_module(info.name)
        register = getattr(module, "register", None)
        if callable(register):
            register(app)
            loaded.append(info.name.rsplit(".", 1)[-1])
    return loaded
