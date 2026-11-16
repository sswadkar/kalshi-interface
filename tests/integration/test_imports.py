import ast
import os
from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src/kalshi_workstation"


def test_application_import_needs_no_credentials_or_network(tmp_path):
    script = textwrap.dedent('''
        import socket
        import requests
        from cryptography.hazmat.primitives import serialization
        def forbidden(*args, **kwargs):
            raise AssertionError("Import performed authenticated or network work")
        socket.socket.connect = forbidden
        requests.sessions.Session.request = forbidden
        serialization.load_pem_private_key = forbidden
        from kalshi_workstation.app import app
        import server
        assert server.app is app
        from kalshi_workstation.core.exchange.kalshi import get_credentials
        assert len(app.openapi()["paths"]) > 0
        assert get_credentials.cache_info().currsize == 0
        assert app.state.liquidity_provider.settings["enabled"] is False
    ''')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT / "src"), str(ROOT))),
               KALSHI_ENV_FILE=str(tmp_path / "absent.env"), KALSHI_DATA_DIR=str(tmp_path))
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_core_does_not_import_features_and_features_use_execution_boundary():
    violations = []
    for path in PACKAGE.rglob("*.py"):
        relative = path.relative_to(PACKAGE)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if relative.parts[0] == "core" and ".features" in module:
                    violations.append(f"{relative}: core imports a feature")
                if relative.parts[0] == "features" and module.endswith("exchange.kalshi"):
                    forbidden = {"kalshi_post", "kalshi_delete"} & {a.name for a in node.names}
                    if forbidden:
                        violations.append(f"{relative}: direct mutation import {forbidden}")
    assert not violations, violations
