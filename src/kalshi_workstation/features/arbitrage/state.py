"""Arbitrage groups, configuration, and activity log."""
import datetime
import json
import uuid
from kalshi_workstation.config import DATA_DIR


ARB_GROUPS_FILE = DATA_DIR / "arbitrage_groups.json"


arb_state = {
    "groups": [],           # List of {id, name, markets: [{ticker, yes_bid, yes_ask, no_bid, no_ask, ...}]}
    "settings": {
        "threshold": 5,     # Cents, minimum profit for opportunity
        "buffer": 1,        # Cents to add to ask price for execution
        "skip_confirmation": False,
        "auto_arb": False,  # Auto-execute arbitrage when threshold met
        "auto_arb_quantity": 10  # Quantity to use for auto-arb trades
    },
    "messages": [],         # Activity log for arbitrage page
    "last_pull": None
}


auto_arb_cooldown = {}  # {group_id: timestamp}


def load_arb_groups():
    """Load arbitrage groups from JSON file on startup."""
    if ARB_GROUPS_FILE.exists():
        try:
            with open(ARB_GROUPS_FILE, "r") as f:
                data = json.load(f)
                arb_state["groups"] = data.get("groups", [])
                # Merge loaded settings with defaults to handle missing keys
                loaded_settings = data.get("settings", {})
                for key, value in loaded_settings.items():
                    arb_state["settings"][key] = value
        except Exception as e:
            print(f"Error loading arbitrage groups: {e}")


def save_arb_groups():
    """Save arbitrage groups to JSON file."""
    try:
        with open(ARB_GROUPS_FILE, "w") as f:
            json.dump({
                "groups": arb_state["groups"],
                "settings": arb_state["settings"]
            }, f, indent=2)
    except Exception as e:
        print(f"Error saving arbitrage groups: {e}")


def add_arb_message(type_, text, **details):
    """Add a message to the arbitrage activity log."""
    arb_state["messages"].insert(0, {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "type": type_,
        "text": text,
        "details": details
    })
    arb_state["messages"] = arb_state["messages"][:100]
