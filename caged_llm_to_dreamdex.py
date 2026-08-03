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
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_utils import to_checksum_address
from nacl.public import PublicKey, SealedBox

PROTOCOL = 1
CHAIN_ID = 5031
MARKET = "SOMI:USDso"
LIFETIMES = {"fund": 900, "trade": 180, "withdraw": 900}
INTENT_RE = re.compile(r"^[0-9a-f]{32}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-f]{64}$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ClientError(ValueError):
    pass


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ClientError("relay public key is not valid base64") from exc


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def canonical_decimal(value: str, *, positive: bool) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        raise ClientError("amounts must be ordinary nonnegative decimal strings")
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


def checked_address(value: str) -> str:
    if not ADDRESS_RE.fullmatch(value):
        raise ClientError("address must be 0x plus 40 hex characters")
    return to_checksum_address(value)


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
        raise ClientError("relay protocol must be 1")
    if "base_url" not in config:
        raise ClientError("relay base URL is not configured")
    config["base_url"] = str(config["base_url"]).rstrip("/")
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
    expected_role = "operator" if operation == "trade" else "owner"
    if signer_role != expected_role:
        raise ClientError("wrong signer role for operation")
    if derive_address(signer_key) != (operator if signer_role == "operator" else owner):
        raise ClientError("signer key does not match declared signer address")
    if operation in {"fund", "withdraw"}:
        if parameters:
            raise ClientError(f"{operation} parameters must be empty")
    else:
        if set(parameters) != {"side", "input_asset", "input_amount", "max_slippage_bps"}:
            raise ClientError("trade parameters have unknown or missing fields")
        side = parameters["side"]
        if (side, parameters["input_asset"]) not in {("sell", "SOMI"), ("buy", "USDso")}:
            raise ClientError("sell uses SOMI; buy uses USDso")
        parameters["input_amount"] = canonical_decimal(parameters["input_amount"], positive=True)
        parameters["max_slippage_bps"] = canonical_decimal(parameters["max_slippage_bps"], positive=False)
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
    ciphertext = SealedBox(PublicKey(raw_key)).encrypt(canonical_json(action))
    package = f"v1.{key_id}.{b64url_encode(ciphertext)}"
    return f"{config['base_url']}/run#p={package}"


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
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    add_identity(status, "owner")
    add_identity(status, "operator")
    fund = commands.add_parser("fund-link")
    add_identity(fund, "owner", require_key=True)
    add_identity(fund, "operator")
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
    result = commands.add_parser("result")
    result.add_argument("intent_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    if args.command == "status":
        url = f"{config['base_url']}/v1/status/{owner}/{operator}.txt"
        print(f"OWNER={owner}\nOPERATOR={operator}\nSTATUS_URL={url}")
        ok, body = fetch_text(url)
        print(body if ok else f"{body}; open STATUS_URL with a browsing/read tool")
        return 0
    if args.command == "fund-link":
        action = make_action("fund", owner, operator, "owner", owner_key or "", {})
        print_link(action, config, f"Fund owner DreamDEX vault to 95 SOMI and enable operator permissions for {MARKET}")
    elif args.command == "withdraw-link":
        action = make_action("withdraw", owner, operator, "owner", owner_key or "", {})
        print_link(action, config, f"Withdraw all SOMI and USDso and revoke operator permissions for {MARKET}")
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
