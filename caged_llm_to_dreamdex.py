#!/usr/bin/env python3
"""Tiny public protocol client for the private DreamDEX relay."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_utils import to_checksum_address
from nacl.public import PublicKey, SealedBox

PROTOCOL = 1
CHAIN_ID = 5031
MARKET = "SOMI:USDso"
LIFETIMES = {"fund": 900, "trade": 180, "withdraw": 900, "transfer": 900}
SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
INTENT_RE = re.compile(r"^[0-9a-f]{32}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-f]{64}$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
DNS_HOST_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
MAX_DECIMAL_PLACES = 18


class ClientError(ValueError):
    pass


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64_decode(value: str) -> bytes:
    if not isinstance(value, str) or not B64URL_RE.fullmatch(value):
        raise ClientError("relay public key is not canonical base64url")
    try:
        encoded = value.encode("ascii")
        raw = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True,
        )
    except (UnicodeEncodeError, ValueError) as exc:
        raise ClientError("relay public key is not canonical base64url") from exc
    if b64url_encode(raw) != value:
        raise ClientError("relay public key is not canonical base64url")
    return raw


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def canonical_decimal(value: str, *, positive: bool) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        raise ClientError("amounts must be ordinary nonnegative decimal strings")
    if "." in value and len(value.split(".", 1)[1]) > MAX_DECIMAL_PLACES:
        raise ClientError("decimals may have at most 18 fractional decimal places")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ClientError("invalid decimal amount") from exc
    if not number.is_finite() or (positive and number <= 0) or (not positive and number < 0):
        raise ClientError("decimal amount is outside the permitted range")
    canonical = format(number, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    if canonical != value:
        raise ClientError(f"decimal must be canonical; use {canonical}")
    return canonical


def read_private_key(path_value: str) -> str:
    path = Path(path_value)
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise ClientError(f"cannot read private-key file: {path}") from exc
    if not PRIVATE_KEY_RE.fullmatch(value):
        raise ClientError("private-key file must contain canonical 0x plus 64 lowercase hex characters")
    try:
        Account.from_key(value)
    except Exception as exc:
        raise ClientError("private-key file does not contain a valid EVM private key") from exc
    return value


def derive_address(private_key: str) -> str:
    return Account.from_key(private_key).address


def generate_private_key(randbelow=secrets.randbelow) -> str:
    scalar = randbelow(SECP256K1_ORDER - 1) + 1
    if not 1 <= scalar < SECP256K1_ORDER:
        raise ClientError("secure randomness returned an invalid private scalar")
    return "0x" + scalar.to_bytes(32, "big").hex()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def generate_wallets(output_dir: str | None = None) -> tuple[str, str, Path, Path]:
    workspace = Path(__file__).resolve().parent.parent
    forbidden = (workspace / "caged-llm-to-dreamdex", workspace / "caged-llm-dreamdex-relay",
                 workspace / "handoff", workspace / "tmp")
    if output_dir:
        directory = Path(output_dir).expanduser().resolve()
        if any(_inside(directory, root.resolve()) for root in forbidden):
            raise ClientError("session wallet directory cannot be inside a repository or project archive")
        if directory.exists():
            raise ClientError(f"session wallet directory already exists: {directory}")
        directory.mkdir(mode=0o700, parents=True)
    else:
        directory = Path(tempfile.mkdtemp(prefix="caged-dreamdex-wallets-")).resolve()
    if any(_inside(directory, root.resolve()) for root in forbidden):
        directory.rmdir()
        raise ClientError("session wallet directory cannot be inside a repository or project archive")
    directory.chmod(0o700)
    owner_key = generate_private_key()
    operator_key = generate_private_key()
    if derive_address(owner_key) == derive_address(operator_key):
        directory.rmdir()
        raise ClientError("generated owner and operator wallets unexpectedly collided")
    paths = (directory / "owner.key", directory / "operator.key")
    try:
        for path, key in zip(paths, (owner_key, operator_key), strict=True):
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(descriptor, (key + "\n").encode("ascii"))
            finally:
                os.close(descriptor)
            path.chmod(0o600)
    except Exception:
        for path in paths:
            path.unlink(missing_ok=True)
        directory.rmdir()
        raise
    return derive_address(owner_key), derive_address(operator_key), *paths


def checked_address(value: str) -> str:
    if not ADDRESS_RE.fullmatch(value):
        raise ClientError("address must be 0x plus 40 hex characters")
    return to_checksum_address(value)


def checked_pair(owner: str, operator: str) -> tuple[str, str]:
    normalized_owner = checked_address(owner)
    normalized_operator = checked_address(operator)
    if normalized_owner == normalized_operator:
        raise ClientError("owner and operator must be distinct addresses")
    return normalized_owner, normalized_operator


def validate_relay_origin(value: Any, *, allow_insecure_local: bool = False) -> str:
    if not isinstance(value, str):
        raise ClientError("relay base URL must be one exact origin")
    try:
        value.encode("ascii")
        parsed = urlsplit(value)
        port = parsed.port
    except (UnicodeEncodeError, ValueError) as exc:
        raise ClientError("relay base URL must be one exact origin") from exc
    host = parsed.hostname or ""
    common = bool(
        parsed.netloc and host and DNS_HOST_RE.fullmatch(host)
        and parsed.username is None and parsed.password is None
        and parsed.path == "" and not parsed.query and not parsed.fragment
    )
    secure = common and parsed.scheme == "https" and port in {None, 443}
    local = bool(
        common and allow_insecure_local and parsed.scheme == "http"
        and host.lower() in {"127.0.0.1", "localhost"} and port is not None
    )
    if not (secure or local):
        raise ClientError(
            "relay base URL must be https://HOST with no path, query, fragment, "
            "credentials, or non-default port; insecure localhost requires "
            "--allow-insecure-local-relay"
        )
    return value


def role_address(address: str | None, key_file: str | None, role: str) -> tuple[str, str | None]:
    if bool(address) == bool(key_file):
        raise ClientError(f"provide exactly one {role} address or {role} key file")
    if key_file:
        key = read_private_key(key_file)
        return derive_address(key), key
    return checked_address(address or ""), None


def load_config(args: argparse.Namespace) -> dict[str, Any]:
    explicit = args.relay_config or os.environ.get("CAGED_LLM_RELAY_CONFIG")
    path = Path(explicit) if explicit else Path(__file__).with_name("relay.json")
    config: dict[str, Any] = {}
    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ClientError(f"cannot load relay config: {path}") from exc
        if set(config) != {"base_url", "protocol", "key_id", "public_key_b64"}:
            raise ClientError("relay config has unknown or missing fields")
    config.update({k: v for k, v in {
        "base_url": args.relay_base_url,
        "key_id": args.key_id,
        "public_key_b64": args.public_key_b64,
    }.items() if v is not None})
    config.setdefault("protocol", PROTOCOL)
    if config.get("protocol") != PROTOCOL:
        raise ClientError("relay and client protocol do not match")
    if "base_url" not in config:
        raise ClientError("relay base URL is not configured")
    allow_local = bool(getattr(args, "allow_insecure_local_relay", False))
    config["base_url"] = validate_relay_origin(
        config["base_url"], allow_insecure_local=allow_local,
    )
    config["allow_insecure_local_relay"] = allow_local
    return config


def make_action(
    operation: str,
    owner: str,
    operator: str,
    signer_role: str,
    signer_key: str,
    parameters: dict[str, str],
    *,
    now: int | None = None,
    intent_id: str | None = None,
) -> dict[str, Any]:
    if operation not in LIFETIMES:
        raise ClientError("unsupported operation")
    owner, operator = checked_pair(owner, operator)
    expected_role = "operator" if operation == "trade" else "owner"
    if operation == "transfer":
        expected_role = signer_role
    if expected_role not in {"owner", "operator"} or signer_role != expected_role:
        raise ClientError("wrong signer role for operation")
    if derive_address(signer_key) != (operator if signer_role == "operator" else owner):
        raise ClientError("signer key does not match declared signer address")
    if operation == "fund":
        if set(parameters) != {"operator_gas_policy"} or parameters["operator_gas_policy"] not in {"manual", "top_up_to_target"}:
            raise ClientError("fund requires operator_gas_policy manual or top_up_to_target")
    elif operation == "withdraw":
        if parameters:
            raise ClientError("withdraw parameters must be empty")
    elif operation == "trade":
        if set(parameters) != {"side", "input_asset", "input_amount", "max_slippage_bps"}:
            raise ClientError("trade parameters have unknown or missing fields")
        side = parameters["side"]
        if (side, parameters["input_asset"]) not in {("sell", "SOMI"), ("buy", "USDso")}:
            raise ClientError("sell uses SOMI; buy uses USDso")
        parameters["input_amount"] = canonical_decimal(parameters["input_amount"], positive=True)
        parameters["max_slippage_bps"] = canonical_decimal(parameters["max_slippage_bps"], positive=False)
    else:
        mode = parameters.get("amount_mode")
        expected = {"asset", "recipient", "amount_mode"} | ({"amount"} if mode == "exact" else set())
        if set(parameters) != expected or mode not in {"exact", "max"}:
            raise ClientError("transfer requires an exact amount or max mode")
        asset = parameters["asset"]
        if (signer_role, asset) not in {("owner", "SOMI"), ("owner", "USDso"), ("operator", "SOMI")}:
            raise ClientError("unsupported signer and transfer asset combination")
        recipient = checked_address(parameters["recipient"])
        if int(recipient, 16) == 0:
            raise ClientError("transfer recipient cannot be the zero address")
        source = owner if signer_role == "owner" else operator
        if recipient == source:
            raise ClientError("transfer recipient cannot equal the selected source wallet")
        parameters["recipient"] = recipient
        if mode == "exact":
            parameters["amount"] = canonical_decimal(parameters["amount"], positive=True)
    created = int(time.time()) if now is None else now
    identity = secrets.token_hex(16) if intent_id is None else intent_id
    if not INTENT_RE.fullmatch(identity):
        raise ClientError("intent ID must be 32 lowercase hex characters")
    return {
        "v": PROTOCOL,
        "intent_id": identity,
        "created_at": created,
        "expires_at": created + LIFETIMES[operation],
        "chain_id": CHAIN_ID,
        "market": MARKET,
        "operation": operation,
        "owner": owner,
        "operator": operator,
        "signer": {"role": signer_role, "private_key": signer_key},
        "parameters": parameters,
    }


def execution_url(action: dict[str, Any], config: dict[str, Any]) -> str:
    key_id = str(config.get("key_id", ""))
    if not KEY_ID_RE.fullmatch(key_id):
        raise ClientError("relay key ID is invalid or missing")
    raw_key = b64_decode(str(config.get("public_key_b64", "")))
    if len(raw_key) != PublicKey.SIZE:
        raise ClientError("relay public encryption key must be 32 bytes")
    base_url = validate_relay_origin(
        config.get("base_url"),
        allow_insecure_local=bool(config.get("allow_insecure_local_relay", False)),
    )
    ciphertext = SealedBox(PublicKey(raw_key)).encrypt(canonical_json(action))
    package = f"v1.{key_id}.{b64url_encode(ciphertext)}"
    return f"{base_url}/run#p={package}"


def fetch_text(url: str) -> tuple[bool, str]:
    try:
        request = urllib.request.Request(url, headers={"Accept": "text/plain"})
        with urllib.request.urlopen(request, timeout=8) as response:
            return True, response.read(64_000).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return False, f"GET unavailable ({type(exc).__name__})"


def print_link(action: dict[str, Any], config: dict[str, Any], summary: str) -> None:
    print(f"ACTION={summary}")
    print(f"INTENT_ID={action['intent_id']}")
    print(f"EXPIRES_AT={action['expires_at']}")
    print(f"EXECUTION_URL={execution_url(action, config)}")
    print("OPENING_THIS_LINK_EXECUTES=true")


def add_identity(parser: argparse.ArgumentParser, role: str, *, require_key: bool = False) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    if not require_key:
        group.add_argument(f"--{role}-address")
    group.add_argument(f"--{role}-key-file")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relay-config")
    parser.add_argument("--relay-base-url")
    parser.add_argument("--key-id")
    parser.add_argument("--public-key-b64")
    parser.add_argument(
        "--allow-insecure-local-relay", action="store_true",
        help="allow only http://127.0.0.1:PORT or http://localhost:PORT for local development",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    wallets = commands.add_parser("generate-wallets")
    wallets.add_argument("--output-dir")
    status = commands.add_parser("status")
    add_identity(status, "owner")
    add_identity(status, "operator")
    fund = commands.add_parser("fund-link")
    add_identity(fund, "owner", require_key=True)
    add_identity(fund, "operator")
    fund.add_argument("--operator-gas-policy", choices=("manual", "top_up_to_target"), required=True)
    trade = commands.add_parser("trade-link")
    add_identity(trade, "owner")
    add_identity(trade, "operator", require_key=True)
    sides = trade.add_subparsers(dest="side", required=True)
    sell = sides.add_parser("sell")
    sell.add_argument("--somi", required=True)
    sell.add_argument("--max-slippage-bps", required=True)
    buy = sides.add_parser("buy")
    buy.add_argument("--usdso", required=True)
    buy.add_argument("--max-slippage-bps", required=True)
    withdraw = commands.add_parser("withdraw-link")
    add_identity(withdraw, "owner", require_key=True)
    add_identity(withdraw, "operator")
    transfer = commands.add_parser("transfer-link")
    add_identity(transfer, "owner")
    add_identity(transfer, "operator")
    transfer.add_argument("--from", dest="from_role", choices=("owner", "operator"), required=True)
    transfer.add_argument("--asset", choices=("SOMI", "USDso"), required=True)
    transfer.add_argument("--to", required=True)
    amount = transfer.add_mutually_exclusive_group(required=True)
    amount.add_argument("--amount")
    amount.add_argument("--all", action="store_true")
    result = commands.add_parser("result")
    result.add_argument("intent_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "generate-wallets":
        owner, operator, owner_path, operator_path = generate_wallets(args.output_dir)
        print(f"OWNER={owner}\nOPERATOR={operator}\nOWNER_KEY_FILE={owner_path}\nOPERATOR_KEY_FILE={operator_path}")
        return 0
    config = load_config(args)
    if args.command == "result":
        if not INTENT_RE.fullmatch(args.intent_id):
            raise ClientError("intent ID must be 32 lowercase hex characters")
        url = f"{config['base_url']}/v1/result/{args.intent_id}.txt"
        ok, body = fetch_text(url)
        print(body if ok else f"{body}; open this public URL with a browsing/read tool:\n{url}")
        return 0
    owner, owner_key = role_address(getattr(args, "owner_address", None), args.owner_key_file, "owner")
    operator, operator_key = role_address(getattr(args, "operator_address", None), args.operator_key_file, "operator")
    owner, operator = checked_pair(owner, operator)
    if args.command == "status":
        url = f"{config['base_url']}/v1/status/{owner}/{operator}.txt"
        print(f"OWNER={owner}\nOPERATOR={operator}\nSTATUS_URL={url}")
        ok, body = fetch_text(url)
        print(body if ok else f"{body}; open STATUS_URL with a browsing/read tool")
        return 0
    if args.command == "fund-link":
        parameters = {"operator_gas_policy": args.operator_gas_policy}
        action = make_action("fund", owner, operator, "owner", owner_key or "", parameters)
        top_up = "top up operator to 1 SOMI if needed" if args.operator_gas_policy == "top_up_to_target" else "require manual operator gas funding"
        print_link(
            action,
            config,
            f"Reach the 95 SOMI vault and operator-permission targets; {top_up}. "
            "For a wholly fresh setup, start the owner above 99 SOMI; this is onboarding guidance, "
            "not a continuing balance floor",
        )
    elif args.command == "withdraw-link":
        action = make_action("withdraw", owner, operator, "owner", owner_key or "", {})
        print_link(action, config, f"Withdraw all SOMI and USDso and revoke operator permissions for {MARKET}")
    elif args.command == "transfer-link":
        signer_key = owner_key if args.from_role == "owner" else operator_key
        if signer_key is None:
            raise ClientError(f"the {args.from_role} signer must be provided with a key file")
        parameters = {"asset": args.asset, "recipient": args.to,
                      "amount_mode": "max" if args.all else "exact"}
        if not args.all:
            parameters["amount"] = args.amount
        action = make_action("transfer", owner, operator, args.from_role, signer_key or "", parameters)
        transfer_text = (
            f"all available {args.asset} at signing, after reserving native gas where applicable"
            if args.all else f"{args.amount} {args.asset}"
        )
        print_link(action, config, f"Transfer {transfer_text} from the {args.from_role} wallet to {parameters['recipient']}")
    else:
        asset, amount = ("SOMI", args.somi) if args.side == "sell" else ("USDso", args.usdso)
        parameters = {"side": args.side, "input_asset": asset, "input_amount": amount, "max_slippage_bps": args.max_slippage_bps}
        action = make_action("trade", owner, operator, "operator", operator_key or "", parameters)
        verb = "Sell" if args.side == "sell" else "Spend at most"
        print_link(action, config, f"{verb} {amount} {asset} using a market-style IOC trade on {MARKET}; max slippage {args.max_slippage_bps} bps")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
