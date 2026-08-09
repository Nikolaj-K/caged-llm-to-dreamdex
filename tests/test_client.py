from __future__ import annotations

import builtins
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_account import Account
import nacl._sodium
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

    assert set(values) == {
        label, f"{label}_KEY_FILE", f"{label}_KEY_VALIDATED",
        f"{label}_ADDRESS_MATCH_CONFIRMED",
    }
    assert values[f"{label}_KEY_VALIDATED"] == "true"
    assert values[f"{label}_ADDRESS_MATCH_CONFIRMED"] == "true"
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


def test_builtin_evm_crypto_matches_independent_vectors() -> None:
    assert client.keccak_256(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )
    for scalar in (1, 2, 3, 0x123456789ABCDEF, client.SECP256K1_ORDER - 1):
        key = "0x" + scalar.to_bytes(32, "big").hex()
        assert client.derive_address(key) == Account.from_key(key).address


def test_private_key_input_is_normalized_before_packaging() -> None:
    bare_upper = "A" * 64
    assert client.checked_private_key(bare_upper) == "0x" + "a" * 64
    with pytest.raises(client.ClientError, match="valid EVM private key"):
        client.checked_private_key("0" * 64)


def test_native_libsodium_fallback_preserves_sealed_box_wire_format(
    monkeypatch: pytest.MonkeyPatch, relay_key: PrivateKey,
) -> None:
    original_import = builtins.__import__

    def import_without_pynacl(name, *args, **kwargs):
        if name == "nacl.public":
            raise ImportError("simulated missing PyNaCl wrapper")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_pynacl)
    monkeypatch.setattr(
        client.ctypes.util, "find_library", lambda name: nacl._sodium.__file__,
    )
    plaintext = b"validated action fixture"
    ciphertext = client._sealed_box_encrypt(bytes(relay_key.public_key), plaintext)
    assert SealedBox(relay_key).decrypt(ciphertext) == plaintext


def test_wallet_generation_runs_without_site_packages(tmp_path: Path) -> None:
    output_dir = tmp_path / "no-site-wallet"
    completed = subprocess.run(
        [
            sys.executable, "-S", str(ROOT / "caged_llm_to_dreamdex.py"),
            "generate-wallet", "--output-dir", str(output_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    values = dict(line.split("=", 1) for line in completed.stdout.splitlines())
    assert values["OWNER_KEY_VALIDATED"] == "true"
    assert values["OWNER_ADDRESS_MATCH_CONFIRMED"] == "true"
    assert completed.stderr == ""


def test_only_optional_sealed_box_wrapper_is_a_runtime_requirement() -> None:
    runtime = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    development = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert runtime.strip() == "PyNaCl>=1.5,<2"
    assert "eth-account" in development and "eth-utils" in development


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
    output = dict(line.split("=", 1) for line in stdout.splitlines())
    assert output["SIGNER_ROLE"] == signer_role
    assert output["SIGNER_ADDRESS"] == action[signer_role]
    assert output["SIGNER_KEY_VALIDATED"] == "true"
    assert output["ACTION_PACKAGE_VALIDATED"] == "true"
    assert "OPENING_THIS_LINK_EXECUTES=true" in stdout


def test_execution_url_refuses_a_hand_built_malformed_or_inconsistent_action(
    wallets: dict[str, object], relay_key: PrivateKey,
) -> None:
    owner = str(wallets["owner"])
    action = client.make_action(
        "fund", owner, owner, "owner", str(wallets["owner_key"]),
        {"operator_gas_policy": "top_up_to_target"}, now=1_000,
        intent_id="ab" * 16,
    )
    config = {
        "base_url": "https://relay.example.invalid", "key_id": "test-relay",
        "public_key_b64": client.b64url_encode(bytes(relay_key.public_key)),
    }
    malformed = json.loads(json.dumps(action))
    malformed["signer"]["private_key"] = "0xnot-a-key"
    with pytest.raises(client.ClientError, match="selected signer private key"):
        client.execution_url(malformed, config)

    inconsistent = json.loads(json.dumps(action))
    inconsistent["signer"]["private_key"] = str(wallets["operator_key"])
    with pytest.raises(client.ClientError, match="does not match"):
        client.execution_url(inconsistent, config)


def test_fund_link_prints_click_time_funding_precondition(
    wallets: dict[str, object], relay_key: PrivateKey, capsys,
) -> None:
    stdout, _ = invoke(capsys, relay_key, [
        "fund-link", "--owner-key-file", str(wallets["owner_path"]),
    ])
    assert "At click time" in stdout
    assert "missing setup value and worst-case gas" in stdout


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
    assert "[failed reading] The relay status GET was unavailable" in stdout
    assert "Some hosted web reads are not expected to work" in stdout
    assert "Do not infer or say that a displayed wallet is unfunded" in stdout
    assert "inspect the printed official explorer" in stdout
    assert "Always show each explorer link" in stdout
    assert "state the practical consequence briefly" in stdout
    assert "Preserve any user report that it is funded" not in stdout
    assert "do not ask about funding again" not in stdout
    assert "Do not announce failed reads" not in stdout

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
        "When explorer evidence is actually needed, prioritize native SOMI",
        "Explorer evidence is wallet-level only",
            "Some hosted web reads are not expected to work",
            "`[failed reading]`",
        "`Owner DreamDEX vault: SOMI`",
        "do not ask them to copy the values unless a value",
    ):
        assert phrase in agents
    assert "It is not Solana" in readme


def test_agent_instructions_suggest_forward_motion_without_bypassing_checks() -> None:
    agents = " ".join((ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    prompt = " ".join(
        readme.split("## Start this in a new ChatGPT/Codex window", 1)[1]
        .replace(">", " ")
        .split()
    )

    for phrase in (
        "Some hosted web reads are not expected to work",
        "Make one client read attempt",
        "at most one attempt at the exact relay URL",
        "Do not then try web search, source search, mounted-file search",
        "user-experience suggestion",
        "not permission to bypass validation or weaken a security boundary",
        "checked-in client validation and execution page's fresh live preflight remain mandatory",
        "Do not inspect source chunks",
        "manually derive the wallets",
        "search mounted directories",
    ):
        assert phrase in agents

    for phrase in (
        "Expect that hosted ChatGPT may be unable to reach",
        "Make one status attempt and at most one direct attempt",
        "do not search the web, inspect mounted files",
        "conversation-flow suggestion, not a security bypass",
        "checked-in client validation and the execution page's fresh live preflight remain mandatory",
        "If I accept the suggestion",
    ):
        assert phrase in prompt


def test_agent_bootstraps_and_reuses_public_runtime_files_early() -> None:
    agents = " ".join((ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    prompt = " ".join(
        readme.split("## Start this in a new ChatGPT/Codex window", 1)[1]
        .replace(">", " ")
        .split()
    )

    for phrase in (
        "Bootstrap the reusable client workspace near the start of the session",
        "The only public runtime files needed for the normal flow are `caged_llm_to_dreamdex.py` and `relay.json`",
        "reuse it for wallet generation or validation, status reads, every action link, and result checks",
        "does not validate a balance, authorize an action, or transmit a private key",
        "Do not fetch either runtime file again for a later action",
    ):
        assert phrase in agents

    for phrase in (
        "Near the start of the session, silently prepare the public runtime files",
        "obtain the exact checked-in `caged_llm_to_dreamdex.py` and `relay.json` once",
        "reuse that same copy for wallet validation, status, action links, and result checks",
        "does not authorize anything, transmit a key, or replace client validation",
        "Fetch `requirements.txt` only if the checked-in client produces the specific dependency error",
    ):
        assert phrase in prompt


def test_onboarding_uses_one_paragraph_per_concern_without_operator_repetition() -> None:
    agents = " ".join((ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    prompt = " ".join(
        readme.split("## Start this in a new ChatGPT/Codex window", 1)[1]
        .replace(">", " ")
        .split()
    )
    assert "one concern per paragraph and no paraphrased repetition" in agents
    assert "one optional `Operator` paragraph that serves as both explanation and offer" in agents
    assert "Mention the optional `Operator` only once" in agents
    assert prompt.count("`Operator` trade-signing key is possible but entirely optional") == 1
    assert "Do not repeat this offer in a second paragraph" in prompt
    assert prompt.index("First explain the required `Owner`") < prompt.index(
        "Next use one paragraph—and only one"
    ) < prompt.index("In its own paragraph") < prompt.index(
        "End with one separate question"
    )


def test_action_preparation_is_one_shot_and_runtime_light() -> None:
    agents = " ".join((ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    prompt = " ".join(
        readme.split("## Start this in a new ChatGPT/Codex window", 1)[1]
        .replace(">", " ")
        .split()
    )
    for phrase in (
        "Prepare actions in one focused attempt",
        "materialize the exact connector-returned",
        "not permission to rewrite or reconstruct it",
        "do not probe or install packages before this first run",
        "make at most one ordinary `requirements.txt` install attempt",
        "stop promptly",
        "[blocked executing]",
        "Do not show placeholder commands",
        "When the user asks to move quickly",
        "Setup always targets 95",
        "Once the user says to use 95",
    ):
        assert phrase in agents
    for phrase in (
        "Run the client before probing or installing anything",
        "materialize its exact",
        "run that unchanged copy and do not author a substitute",
        "neither PyNaCl nor system `libsodium`",
        "one short `[blocked executing]` paragraph",
        "If I say not to think too long",
        "After I accept 95",
    ):
        assert phrase in prompt
    assert "Install its dependencies and start" not in prompt
