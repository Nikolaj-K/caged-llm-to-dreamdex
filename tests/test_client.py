from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_account import Account
from nacl.public import PrivateKey, PublicKey, SealedBox

import caged_llm_to_dreamdex as client

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def wallets(tmp_path: Path) -> dict[str, object]:
    owner, owner_path = client.generate_wallet(str(tmp_path / "owner"))
    operator, operator_path = client.generate_wallet(
        str(tmp_path / "operator"), role="operator",
    )
    return {
        "owner": owner,
        "operator": operator,
        "owner_path": owner_path,
        "operator_path": operator_path,
        "owner_key": owner_path.read_text(encoding="ascii").strip(),
        "operator_key": operator_path.read_text(encoding="ascii").strip(),
    }


@pytest.fixture
def relay_key() -> PrivateKey:
    return PrivateKey.generate()


def relay_arguments(relay_key: PrivateKey) -> list[str]:
    public_key = client.b64url_encode(bytes(relay_key.public_key))
    return [
        "--relay-base-url",
        "https://relay.example.invalid",
        "--key-id",
        "test-relay",
        f"--public-key-b64={public_key}",
    ]


def invoke(capsys, relay_key: PrivateKey, arguments: list[str]) -> tuple[str, str]:
    assert client.main(relay_arguments(relay_key) + arguments) == 0
    captured = capsys.readouterr()
    return captured.out, captured.err


@pytest.mark.parametrize("role", ["owner", "operator"])
def test_generate_wallet_creates_one_private_file_without_printing_key(
    tmp_path: Path,
    capsys,
    role: str,
) -> None:
    directory = tmp_path / f"generated-{role}"
    assert client.main([
        "generate-wallet", "--role", role, "--output-dir", str(directory),
    ]) == 0
    captured = capsys.readouterr()
    values = dict(line.split("=", 1) for line in captured.out.splitlines())
    label = role.upper()
    key_path = Path(values[f"{label}_KEY_FILE"])
    private_key = key_path.read_text(encoding="ascii").strip()

    assert set(values) == {label, f"{label}_KEY_FILE"}
    assert client.derive_address(private_key) == values[label]
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert private_key not in captured.out + captured.err
    assert captured.err == ""


def test_generate_wallet_refuses_to_overwrite_existing_directory(
    tmp_path: Path,
    capsys,
) -> None:
    directory = tmp_path / "existing"
    directory.mkdir()
    sentinel = directory / "owner.key"
    sentinel.write_text("leave-this-file-alone\n", encoding="ascii")

    with pytest.raises(client.ClientError, match="already exists"):
        client.main(["generate-wallet", "--output-dir", str(directory)])

    assert sentinel.read_text(encoding="ascii") == "leave-this-file-alone\n"
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_default_wallet_directory_is_temporary_and_cleanable() -> None:
    owner, owner_path = client.generate_wallet()
    directory = owner_path.parent
    try:
        assert not directory.is_relative_to(Path(client.__file__).resolve().parent)
        assert client.derive_address(owner_path.read_text().strip()) == owner
    finally:
        owner_path.unlink(missing_ok=True)
        directory.rmdir()


def test_default_relay_configuration_is_canonical_and_public_only() -> None:
    payload = json.loads(Path(client.__file__).with_name("relay.json").read_text())
    assert set(payload) == {"base_url", "protocol", "key_id", "public_key_b64"}
    assert payload["base_url"] == "https://somnia.run"
    assert payload["protocol"] == client.PROTOCOL == 1
    assert payload["key_id"] == "20260808-e5db"
    assert len(client.b64_decode(payload["public_key_b64"])) == PublicKey.SIZE
    lowered = json.dumps(payload, sort_keys=True).lower()
    assert "private" not in lowered and "secret" not in lowered and "ticket" not in lowered


def test_config_parsing_accepts_exact_shape_and_rejects_extra_fields(
    tmp_path: Path,
    relay_key: PrivateKey,
) -> None:
    path = tmp_path / "relay.json"
    payload = {
        "base_url": "https://relay.example.invalid",
        "protocol": 1,
        "key_id": "temporary",
        "public_key_b64": client.b64url_encode(bytes(relay_key.public_key)),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    args = SimpleNamespace(
        relay_config=str(path),
        relay_base_url=None,
        key_id=None,
        public_key_b64=None,
        allow_insecure_local_relay=False,
    )
    assert client.load_config(args) == (
        payload | {"allow_insecure_local_relay": False}
    )

    path.write_text(json.dumps(payload | {"private_key": "forbidden"}), encoding="utf-8")
    with pytest.raises(client.ClientError, match="unknown or missing fields"):
        client.load_config(args)


@pytest.mark.parametrize(
    "origin",
    [
        "https://relay.example",
        "https://relay.example:443",
    ],
)
def test_https_relay_origins_are_accepted(origin: str) -> None:
    assert client.validate_relay_origin(origin) == origin


@pytest.mark.parametrize(
    "origin",
    [
        "http://relay.example",
        "https://relay.example/path",
        "https://relay.example/",
        "https://relay.example:8443",
        "https://user:pass@relay.example",
        "https://relay.example?query=1",
        "https://relay.example#fragment",
    ],
)
def test_noncanonical_relay_origins_are_rejected(origin: str) -> None:
    with pytest.raises(client.ClientError):
        client.validate_relay_origin(origin)


@pytest.mark.parametrize("origin", ["http://localhost:8080", "http://127.0.0.1:8080"])
def test_insecure_localhost_requires_explicit_development_flag(origin: str) -> None:
    with pytest.raises(client.ClientError):
        client.validate_relay_origin(origin)
    assert client.validate_relay_origin(origin, allow_insecure_local=True) == origin


@pytest.mark.parametrize("value", ["0", "1", "1.25", "999999999999999999.1"])
def test_canonical_decimals_are_accepted(value: str) -> None:
    assert client.canonical_decimal(value, positive=False) == value


@pytest.mark.parametrize(
    "value",
    ["", "01", "1.0", "1e2", "+1", "-1", ".1", "1.", "nan", "1.0000000000000000001"],
)
def test_noncanonical_decimals_are_rejected(value: str) -> None:
    with pytest.raises(client.ClientError):
        client.canonical_decimal(value, positive=True)


@pytest.mark.parametrize(
    "case",
    [
        "fund-manual",
        "fund-top-up-default",
        "direct-fund",
        "sell",
        "buy",
        "direct-sell",
        "direct-buy",
        "withdraw-both",
        "withdraw-somi",
        "withdraw-usdso",
        "owner-somi-exact",
        "owner-somi-max",
        "owner-usdso-exact",
        "owner-usdso-max",
        "operator-somi-exact",
        "operator-somi-max",
        "direct-owner-usdso-max",
    ],
)
def test_every_link_form_encrypts_exactly_one_correct_signer_key(
    case: str,
    wallets: dict[str, object],
    relay_key: PrivateKey,
    capsys,
) -> None:
    owner = str(wallets["owner"])
    operator = str(wallets["operator"])
    owner_path = str(wallets["owner_path"])
    operator_path = str(wallets["operator_path"])
    recipient = Account.create().address

    direct = case.startswith("direct-")
    normalized_case = case.removeprefix("direct-")

    if case == "fund-manual":
        command = [
            "fund-link", "--owner-key-file", owner_path,
            "--operator-address", operator, "--operator-gas-policy", "manual",
        ]
        operation, signer_role = "fund", "owner"
        expected_parameters = {"operator_gas_policy": "manual"}
    elif case == "fund-top-up-default":
        command = [
            "fund-link", "--owner-key-file", owner_path,
            "--operator-address", operator,
        ]
        operation, signer_role = "fund", "owner"
        expected_parameters = {"operator_gas_policy": "top_up_to_target"}
    elif case == "direct-fund":
        command = ["fund-link", "--owner-key-file", owner_path]
        operation, signer_role = "fund", "owner"
        expected_parameters = {"operator_gas_policy": "top_up_to_target"}
    elif normalized_case in {"sell", "buy"}:
        amount_flag = "--somi" if normalized_case == "sell" else "--usdso"
        asset = "SOMI" if normalized_case == "sell" else "USDso"
        identity = (["--owner-key-file", owner_path] if direct else
                    ["--owner-address", owner, "--operator-key-file", operator_path])
        command = [
            "trade-link", *identity, normalized_case,
            amount_flag, "1.25", "--max-slippage-bps", "100",
        ]
        operation, signer_role = "trade", "owner" if direct else "operator"
        expected_parameters = {
            "side": normalized_case,
            "input_asset": asset,
            "input_amount": "1.25",
            "max_slippage_bps": "100",
        }
    elif case.startswith("withdraw-"):
        asset = case.split("-", 1)[1]
        asset_arg = {"both": "both", "somi": "SOMI", "usdso": "USDso"}[asset]
        command = [
            "withdraw-link", "--owner-key-file", owner_path,
            "--operator-address", operator, "--assets", asset_arg,
        ]
        operation, signer_role = "withdraw", "owner"
        expected_parameters = {
            "assets": ["SOMI", "USDso"] if asset == "both" else [asset_arg],
        }
    elif case == "direct-owner-usdso-max":
        signer_role = "owner"
        command = [
            "transfer-link", "--owner-key-file", owner_path, "--from", "owner",
            "--asset", "USDso", "--to", recipient, "--all",
        ]
        operation = "transfer"
        expected_parameters = {
            "asset": "USDso", "recipient": recipient, "amount_mode": "max",
        }
    else:
        signer_role = "operator" if case.startswith("operator-") else "owner"
        asset = "USDso" if "usdso" in case else "SOMI"
        maximum = case.endswith("-max")
        identity = (
            ["--owner-address", owner, "--operator-key-file", operator_path]
            if signer_role == "operator"
            else ["--owner-key-file", owner_path, "--operator-address", operator]
        )
        amount = ["--all"] if maximum else ["--amount", "1.25"]
        command = [
            "transfer-link", *identity, "--from", signer_role,
            "--asset", asset, "--to", recipient, *amount,
        ]
        operation = "transfer"
        expected_parameters = {
            "asset": asset,
            "recipient": recipient,
            "amount_mode": "max" if maximum else "exact",
        }
        if not maximum:
            expected_parameters["amount"] = "1.25"

    stdout, stderr = invoke(capsys, relay_key, command)
    execution_lines = [
        line for line in stdout.splitlines() if line.startswith("EXECUTION_URL=")
    ]
    assert len(execution_lines) == 1
    execution_url = execution_lines[0].split("=", 1)[1]
    assert execution_url.startswith("https://relay.example.invalid/tx#p=v1.test-relay.")
    package = execution_url.split("#p=", 1)[1]
    assert stdout.count(package) == 1
    assert package not in stderr

    encoded = package.rsplit(".", 1)[1]
    plaintext = SealedBox(relay_key).decrypt(client.b64_decode(encoded))
    action = json.loads(plaintext)
    assert plaintext == client.canonical_json(action)
    assert set(action) == {
        "v", "intent_id", "created_at", "expires_at", "chain_id", "market",
        "operation", "owner", "operator", "signer", "parameters",
    }
    assert action["v"] == client.PROTOCOL == 1
    assert action["chain_id"] == client.CHAIN_ID == 5031
    assert action["market"] == client.MARKET == "SOMI:USDso"
    assert action["operation"] == operation
    assert action["owner"] == owner
    assert action["operator"] == (owner if direct else operator)
    assert action["signer"]["role"] == signer_role
    assert action["parameters"] == expected_parameters
    assert action["expires_at"] - action["created_at"] == client.LIFETIMES[operation]

    selected_key = str(wallets[f"{signer_role}_key"])
    other_role = "operator" if signer_role == "owner" else "owner"
    other_key = str(wallets[f"{other_role}_key"])
    assert action["signer"]["private_key"] == selected_key
    assert plaintext.count(selected_key.encode("ascii")) == 1
    assert other_key.encode("ascii") not in plaintext
    assert selected_key not in stdout + stderr
    assert other_key not in stdout + stderr
    assert "OPENING_THIS_LINK_EXECUTES=true" in stdout


def test_status_and_result_fallback_urls_are_read_only_and_offline(
    wallets: dict[str, object],
    relay_key: PrivateKey,
    monkeypatch,
    capsys,
) -> None:
    requested: list[str] = []

    def offline_get(url: str) -> tuple[bool, str]:
        requested.append(url)
        return False, "GET unavailable (test)"

    monkeypatch.setattr(client, "fetch_text", offline_get)
    owner, operator = str(wallets["owner"]), str(wallets["operator"])
    stdout, stderr = invoke(
        capsys,
        relay_key,
        ["status", "--owner-address", owner, "--operator-address", operator],
    )
    assert requested == [
        f"https://relay.example.invalid/v1/status/{owner}/{operator}.txt"
    ]
    assert "STATUS_URL=" in stdout and stderr == ""
    assert "direct open/fetch/read tool" in stdout
    assert "official explorer URL(s) for wallet-level SOMI" in stdout
    assert f"OWNER_EXPLORER_URL=https://explorer.somnia.network/address/{owner}" in stdout
    assert f"OPERATOR_EXPLORER_URL=https://explorer.somnia.network/address/{operator}" in stdout
    assert "BALANCE_STATUS=UNKNOWN" in stdout
    assert "Do not infer or say that a displayed wallet is unfunded" in stdout
    assert "Preserve any user report that it is funded" in stdout
    assert "do not ask about funding again" in stdout
    assert "silently inspect the printed" in stdout
    assert "Always show each explorer link" in stdout
    assert "Do not announce failed reads" in stdout

    intent_id = "ab" * 16
    stdout, stderr = invoke(capsys, relay_key, ["result", intent_id])
    assert "direct open/fetch/read tool; do not search for it" in stdout
    assert requested[-1] == (
        f"https://relay.example.invalid/v1/result/{intent_id}.txt"
    )
    assert requested[-1] in stdout and stderr == ""
    assert all("/tx" not in url for url in requested)


def test_status_defaults_to_direct_owner_mode_without_operator_output(
    wallets: dict[str, object], relay_key: PrivateKey, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(client, "fetch_text", lambda _url: (True, "trading_mode=direct_owner\n"))
    owner = str(wallets["owner"])
    stdout, stderr = invoke(capsys, relay_key, ["status", "--owner-address", owner])
    assert f"/v1/status/{owner}/{owner}.txt" in stdout
    assert f"OWNER_EXPLORER_URL={client.EXPLORER_BASE_URL}/address/{owner}" in stdout
    assert "OPERATOR=" not in stdout and "OPERATOR_EXPLORER_URL=" not in stdout
    assert "trading_mode=direct_owner" in stdout and stderr == ""


def test_agent_instructions_pin_somnia_identity_and_fallback_voice() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "**Somnia mainnet**",
        "EVM chain ID `5031`",
        "It is not\nSolana",
        "**Somnia/DreamDEX status**",
        "I need the user to paste",
        "Prioritize the native SOMI balance",
        "Explorer evidence is wallet-level only",
        "Keep the retrieval mechanics out of ordinary conversation",
        "`Owner DreamDEX vault: SOMI`",
        "do not ask them to copy the values unless a value",
    ):
        assert phrase in agents
    assert "It is not Solana" in readme
