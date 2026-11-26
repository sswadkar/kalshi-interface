"""Configuration changes become active only after they are saved."""
from copy import deepcopy

from kalshi_workstation.core import persistence
from kalshi_workstation.features.arbitrage.service import Arbitrage


def reject_replacement(monkeypatch):
    def fail(*args):
        raise PermissionError("Synthetic destination is read-only")
    monkeypatch.setattr(persistence.os, "replace", fail)


def test_first_run_creates_configuration_and_reloads_it(client):
    """A new user needs neither a checked-in JSON file nor someone else's account."""
    arbitrage = client.app.state.arbitrage
    assert not arbitrage.config_path.exists()

    group = client.post("/api/arb/groups", json={"name": "Synthetic comparison"}).json()["group"]
    assert group["payout_verified"] is False
    assert client.put("/api/arb/settings", json={"threshold": 7}).status_code == 200

    restarted = Arbitrage(arbitrage.mode, config_path=arbitrage.config_path)
    assert restarted.groups == [group]
    assert restarted.settings["threshold"] == 7
    assert restarted.settings["auto_arb"] is False


def test_failed_save_cannot_enable_automation(client, monkeypatch):
    arbitrage = client.app.state.arbitrage
    arbitrage.save()
    previous_file, before = arbitrage.config_path.read_bytes(), deepcopy(arbitrage.settings)
    reject_replacement(monkeypatch)

    response = client.put("/api/arb/settings", json={"auto_arb": True})

    assert response.status_code == 503
    assert response.json()["code"] == "configuration_save_failed"
    assert "previous saved configuration is unchanged" in response.json()["error"]
    assert arbitrage.settings == before
    assert arbitrage.config_path.read_bytes() == previous_file


def test_failed_nested_edit_leaves_active_configuration_unchanged(client, monkeypatch):
    arbitrage = client.app.state.arbitrage
    group = client.post("/api/arb/groups", json={"name": "Original"}).json()["group"]
    before = deepcopy(arbitrage.groups)
    reject_replacement(monkeypatch)

    assert client.put(f"/api/arb/groups/{group['id']}", json={"name": "Not saved"}).status_code == 503
    assert client.put(f"/api/arb/groups/{group['id']}", json={"payout_verified": True}).status_code == 503
    assert arbitrage.groups == before
