"""Local launcher for `python server.py`; also exposes `app` for Uvicorn."""
from pathlib import Path
import sys

# Keep the original checkout command working before an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from kalshi_workstation.app import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn
    # Trading routes have no operator authentication: default to this machine only.
    uvicorn.run(app, host="127.0.0.1", port=8000)
