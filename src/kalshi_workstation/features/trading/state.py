"""Current dashboard cache and activity messages; one supported application instance."""
import datetime
import json
import uuid
import numpy as np
import pandas as pd


state = {
    "markets": None,
    "positions": None,
    "messages": [],
    "last_pull": None,
    "balance": None,
    "user_info": None,
    "resting_orders": [],
}


def sanitize_json(df: pd.DataFrame):
    """Ensure all numeric values are JSON-safe."""
    clean = df.replace([np.inf, -np.inf, None], np.nan).fillna(0)
    # Convert to native Python types
    return json.loads(clean.to_json(orient="records"))


def add_message(type_, text, **details):
    state["messages"].insert(0, {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "type": type_,
        "text": text,
        "details": details
    })
    state["messages"] = state["messages"][:100]
