from __future__ import annotations

import base64
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from nacl.public import PrivateKey, SealedBox

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("public_client", ROOT / "caged_llm_to_dreamdex.py")
client = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(client)
FIXTURE = json.loads((ROOT / "tests/fixtures/protocol_v1.json").read_text())
OWNER_KEY = FIXTURE["owner_private_key"]
OPERATOR_KEY = FIXTURE["operator_private_key"]


def relay_config() -> dict[str, object]:
    private = PrivateKey(bytes.fromhex(FIXTURE["relay_private_key_hex"]))
    return {
        "base_url": "https://relay.test",
        "protocol": 1,
        "key_id": FIXTURE["key_id"],
        "public_key_b64": base64.urlsafe_b64encode(bytes(private.public_key)).rstrip(b"=").decode(),
    }


def make(operation: str, role: str, key: str, parameters: dict[str, str]):
    return client.make_action(
        operation, FIXTURE["owner"], FIXTURE["operator"], role, key, parameters,
        now=1_000, intent_id="ab" * 16,
    )


def test_address_derivation_and_roles_include_exactly_one_key():
    assert client.derive_address(OWNER_KEY) == FIXTURE["owner"]
    assert client.derive_address(OPERATOR_KEY) == FIXTURE["operator"]
    fund = make("fund", "owner", OWNER_KEY, {})
    withdraw = make("withdraw", "owner", OWNER_KEY, {})
    trade = make("trade", "operator", OPERATOR_KEY, {
        "side": "sell", "input_asset": "SOMI", "input_amount": "1",
        "max_slippage_bps": "25",
    })
    assert fund["signer"] == withdraw["signer"] == {"role": "owner", "private_key": OWNER_KEY}
    assert trade["signer"] == {"role": "operator", "private_key": OPERATOR_KEY}
    assert OPERATOR_KEY not in json.dumps(fund)
    assert OWNER_KEY not in json.dumps(trade)


@pytest.mark.parametrize("value", ["01", "1.0", "1e2", "-1", "nan", ""])
def test_noncanonical_decimals_rejected(value: str):
    with pytest.raises(client.ClientError):
        client.canonical_decimal(value, positive=True)


def test_trade_schema_and_signer_checks():
    with pytest.raises(client.ClientError):
        make("trade", "operator", OPERATOR_KEY, {
            "side": "buy", "input_asset": "SOMI", "input_amount": "1",
            "max_slippage_bps": "1",
        })
    with pytest.raises(client.ClientError):
        make("fund", "owner", OPERATOR_KEY, {})


def test_url_fragment_and_fixture_decryption():
    action = make("fund", "owner", OWNER_KEY, {})
    url = client.execution_url(action, relay_config())
    assert url.startswith("https://relay.test/run#p=v1.test-only-v1.")
    assert OWNER_KEY not in url
    ciphertext = re.fullmatch(r".+#p=v1\.test-only-v1\.([A-Za-z0-9_-]+)", url).group(1)
    raw = base64.urlsafe_b64decode(ciphertext + "=" * (-len(ciphertext) % 4))
    plaintext = SealedBox(PrivateKey(bytes.fromhex(FIXTURE["relay_private_key_hex"]))).decrypt(raw)
    assert json.loads(plaintext) == action
    assert plaintext == client.canonical_json(action)


def test_config_loading_and_overrides(tmp_path: Path):
    path = tmp_path / "relay.json"
    path.write_text(json.dumps(relay_config()))
    args = SimpleNamespace(relay_config=str(path), relay_base_url="http://localhost:9",
                           key_id=None, public_key_b64=None)
    loaded = client.load_config(args)
    assert loaded["base_url"] == "http://localhost:9"
    assert loaded["key_id"] == "test-only-v1"


def test_status_and_result_get_fallback_urls(monkeypatch, capsys):
    monkeypatch.setattr(client, "fetch_text", lambda _url: (False, "GET unavailable (test)"))
    common = ["--relay-base-url", "https://relay.test"]
    assert client.main(common + ["status", "--owner-address", FIXTURE["owner"],
                                  "--operator-address", FIXTURE["operator"]]) == 0
    output = capsys.readouterr().out
    assert f"https://relay.test/v1/status/{FIXTURE['owner']}/{FIXTURE['operator']}.txt" in output
    assert client.main(common + ["result", "ab" * 16]) == 0
    assert "https://relay.test/v1/result/" + "ab" * 16 + ".txt" in capsys.readouterr().out


def test_link_output_never_prints_raw_key(tmp_path: Path, capsys):
    key_file = tmp_path / "owner.key"
    key_file.write_text(OWNER_KEY)
    args = ["--relay-base-url", "https://relay.test", "--key-id", "test-only-v1",
            "--public-key-b64", relay_config()["public_key_b64"], "fund-link",
            "--owner-key-file", str(key_file), "--operator-address", FIXTURE["operator"]]
    assert client.main(args) == 0
    output = capsys.readouterr().out
    assert "OPENING_THIS_LINK_EXECUTES=true" in output
    assert OWNER_KEY not in output
