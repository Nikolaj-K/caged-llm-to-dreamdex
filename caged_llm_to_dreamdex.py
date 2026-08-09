#!/usr/bin/env python3
"""Tiny public protocol client for the private DreamDEX relay."""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.util
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

PROTOCOL = 1
CHAIN_ID = 5031
MARKET = "SOMI:USDso"
LIFETIMES = {"fund": 900, "trade": 180, "withdraw": 900, "transfer": 900}
SECP256K1_FIELD = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
SECP256K1_GENERATOR = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)
INTENT_RE = re.compile(r"^[0-9a-f]{32}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
PRIVATE_KEY_INPUT_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]{64}$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
DNS_HOST_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
MAX_DECIMAL_PLACES = 18
WITHDRAW_FIELDS = {"assets"}
EXPLORER_BASE_URL = "https://explorer.somnia.network"

_MASK_64 = (1 << 64) - 1
_KECCAK_ROTATIONS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
_KECCAK_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)


class ClientError(ValueError):
    pass


def _rotate_left_64(value: int, count: int) -> int:
    if count == 0:
        return value & _MASK_64
    return ((value << count) | (value >> (64 - count))) & _MASK_64


def _keccak_f1600(state: list[int]) -> None:
    for round_constant in _KECCAK_ROUND_CONSTANTS:
        columns = [
            state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
            for x in range(5)
        ]
        corrections = [
            columns[(x - 1) % 5] ^ _rotate_left_64(columns[(x + 1) % 5], 1)
            for x in range(5)
        ]
        for y in range(5):
            for x in range(5):
                state[x + 5 * y] ^= corrections[x]

        rotated = [0] * 25
        for y in range(5):
            for x in range(5):
                rotated[y + 5 * ((2 * x + 3 * y) % 5)] = _rotate_left_64(
                    state[x + 5 * y], _KECCAK_ROTATIONS[x][y]
                )
        for y in range(5):
            offset = 5 * y
            for x in range(5):
                state[offset + x] = (
                    rotated[offset + x]
                    ^ ((~rotated[offset + (x + 1) % 5]) & rotated[offset + (x + 2) % 5])
                ) & _MASK_64
        state[0] ^= round_constant


def keccak_256(data: bytes) -> bytes:
    """Return legacy Keccak-256, as used by EVM addresses (not NIST SHA3-256)."""
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    padded.extend(b"\x00" * ((rate - 1 - len(padded)) % rate))
    padded.append(0x80)
    state = [0] * 25
    for start in range(0, len(padded), rate):
        block = padded[start:start + rate]
        for lane in range(rate // 8):
            state[lane] ^= int.from_bytes(block[lane * 8:(lane + 1) * 8], "little")
        _keccak_f1600(state)
    return b"".join(lane.to_bytes(8, "little") for lane in state)[:32]


def _point_add(
    first: tuple[int, int] | None, second: tuple[int, int] | None,
) -> tuple[int, int] | None:
    if first is None:
        return second
    if second is None:
        return first
    x1, y1 = first
    x2, y2 = second
    if x1 == x2 and (y1 + y2) % SECP256K1_FIELD == 0:
        return None
    if first == second:
        slope = (3 * x1 * x1) * pow(2 * y1, -1, SECP256K1_FIELD)
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, SECP256K1_FIELD)
    slope %= SECP256K1_FIELD
    x3 = (slope * slope - x1 - x2) % SECP256K1_FIELD
    return x3, (slope * (x1 - x3) - y1) % SECP256K1_FIELD


def _scalar_multiply(scalar: int) -> tuple[int, int]:
    result: tuple[int, int] | None = None
    addend: tuple[int, int] | None = SECP256K1_GENERATOR
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    if result is None:
        raise ClientError("private key is outside the secp256k1 scalar range")
    return result


def _checksum_address(lower_hex: str) -> str:
    digest = keccak_256(lower_hex.encode("ascii")).hex()
    return "0x" + "".join(
        character.upper() if character in "abcdef" and int(digest[index], 16) >= 8 else character
        for index, character in enumerate(lower_hex)
    )


def _sealed_box_encrypt(public_key: bytes, plaintext: bytes) -> bytes:
    if len(public_key) != 32:
        raise ClientError("relay public encryption key must be 32 bytes")
    try:
        from nacl.public import PublicKey, SealedBox
    except ImportError:
        PublicKey = SealedBox = None
    if PublicKey is not None and SealedBox is not None:
        return bytes(SealedBox(PublicKey(public_key)).encrypt(plaintext))

    candidates = [ctypes.util.find_library("sodium"), "libsodium.so.23", "libsodium.so"]
    library = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            library = ctypes.CDLL(candidate)
            break
        except OSError:
            continue
    if library is None:
        raise ClientError(
            "sealed-box encryption requires PyNaCl or the system libsodium library"
        )
    library.sodium_init.restype = ctypes.c_int
    if library.sodium_init() < 0:
        raise ClientError("system libsodium initialization failed")
    library.crypto_box_publickeybytes.restype = ctypes.c_size_t
    library.crypto_box_sealbytes.restype = ctypes.c_size_t
    if library.crypto_box_publickeybytes() != 32:
        raise ClientError("system libsodium has an incompatible public-key size")
    output = ctypes.create_string_buffer(len(plaintext) + library.crypto_box_sealbytes())
    message = ctypes.create_string_buffer(plaintext, len(plaintext))
    recipient = ctypes.create_string_buffer(public_key, len(public_key))
    library.crypto_box_seal.argtypes = (
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulonglong, ctypes.c_void_p,
    )
    library.crypto_box_seal.restype = ctypes.c_int
    if library.crypto_box_seal(output, message, len(plaintext), recipient) != 0:
        raise ClientError("system libsodium sealed-box encryption failed")
    return output.raw


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
    return checked_private_key(value, source="private-key file")


def checked_private_key(value: Any, *, source: str = "private key") -> str:
    if not isinstance(value, str) or not PRIVATE_KEY_INPUT_RE.fullmatch(value):
        raise ClientError(
            f"{source} must contain 32 bytes encoded as 64 hexadecimal characters"
        )
    canonical = "0x" + value.removeprefix("0x").lower()
    scalar = int(canonical[2:], 16)
    if not 1 <= scalar < SECP256K1_ORDER:
        raise ClientError(f"{source} is not a valid EVM private key")
    return canonical


def derive_address(private_key: str) -> str:
    scalar = int(checked_private_key(private_key)[2:], 16)
    x_coordinate, y_coordinate = _scalar_multiply(scalar)
    public_key = x_coordinate.to_bytes(32, "big") + y_coordinate.to_bytes(32, "big")
    return _checksum_address(keccak_256(public_key)[-20:].hex())


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


def generate_wallet(
    output_dir: str | None = None, *, role: str = "owner",
) -> tuple[str, Path]:
    if role not in {"owner", "operator"}:
        raise ClientError("wallet role must be owner or operator")
    client_root = Path(__file__).resolve().parent
    workspace = client_root.parent
    forbidden = [client_root]
    if workspace != Path(workspace.anchor):
        forbidden.extend((
            workspace / "caged-llm-dreamdex-relay",
            workspace / "handoff",
            workspace / "tmp",
        ))
    if output_dir:
        directory = Path(output_dir).expanduser().resolve()
        if any(_inside(directory, root.resolve()) for root in forbidden):
            raise ClientError("session wallet directory cannot be inside a repository or project archive")
        if directory.exists():
            raise ClientError(f"session wallet directory already exists: {directory}")
        directory.mkdir(mode=0o700, parents=True)
    else:
        directory = Path(tempfile.mkdtemp(prefix="caged-dreamdex-wallet-")).resolve()
    if any(_inside(directory, root.resolve()) for root in forbidden):
        directory.rmdir()
        raise ClientError("session wallet directory cannot be inside a repository or project archive")
    directory.chmod(0o700)
    private_key = generate_private_key()
    path = directory / f"{role}.key"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, (private_key + "\n").encode("ascii"))
        finally:
            os.close(descriptor)
        path.chmod(0o600)
    except Exception:
        path.unlink(missing_ok=True)
        directory.rmdir()
        raise
    return derive_address(private_key), path


def checked_address(value: Any) -> str:
    if not isinstance(value, str) or not ADDRESS_RE.fullmatch(value):
        raise ClientError("address must be 0x plus 40 hex characters")
    return _checksum_address(value[2:].lower())


def checked_pair(owner: str, operator: str) -> tuple[str, str]:
    normalized_owner = checked_address(owner)
    normalized_operator = checked_address(operator)
    return normalized_owner, normalized_operator


def checked_withdraw_parameters(parameters: Any) -> tuple[tuple[str, ...], bool]:
    """Validate explicit selective-withdrawal parameters."""
    if not isinstance(parameters, dict) or set(parameters) != WITHDRAW_FIELDS:
        raise ClientError("withdraw parameters have unknown or missing fields")
    assets = parameters["assets"]
    if not isinstance(assets, list) or tuple(assets) not in {
        ("SOMI",), ("USDso",), ("SOMI", "USDso"),
    }:
        raise ClientError("withdraw assets must be SOMI, USDso, or canonical SOMI then USDso")
    return tuple(assets), len(assets) == 2


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


def optional_role_address(
    address: str | None, key_file: str | None, role: str,
) -> tuple[str | None, str | None]:
    if address and key_file:
        raise ClientError(f"provide at most one {role} address or {role} key file")
    if not address and not key_file:
        return None, None
    return role_address(address, key_file, role)


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
    signer_key = checked_private_key(signer_key, source="selected signer private key")
    delegated = owner != operator
    expected_role = "operator" if operation == "trade" and delegated else "owner"
    if operation == "transfer":
        expected_role = signer_role
        if not delegated and signer_role == "operator":
            raise ClientError("operator transfers require a distinct optional operator wallet")
    if expected_role not in {"owner", "operator"} or signer_role != expected_role:
        raise ClientError("wrong signer role for operation")
    if derive_address(signer_key) != (operator if signer_role == "operator" else owner):
        raise ClientError("signer key does not match declared signer address")
    if operation == "fund":
        if set(parameters) != {"operator_gas_policy"} or parameters["operator_gas_policy"] not in {"manual", "top_up_to_target"}:
            raise ClientError("fund requires operator_gas_policy manual or top_up_to_target")
    elif operation == "withdraw":
        checked_withdraw_parameters(parameters)
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
    if not isinstance(identity, str) or not INTENT_RE.fullmatch(identity):
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


def validate_action_for_encryption(action: Any) -> dict[str, Any]:
    fields = {
        "v", "intent_id", "created_at", "expires_at", "chain_id", "market",
        "operation", "owner", "operator", "signer", "parameters",
    }
    if not isinstance(action, dict) or set(action) != fields:
        raise ClientError("action has unknown or missing fields")
    signer = action.get("signer")
    if not isinstance(signer, dict) or set(signer) != {"role", "private_key"}:
        raise ClientError("action signer has unknown or missing fields")
    parameters = action.get("parameters")
    if not isinstance(parameters, dict):
        raise ClientError("action parameters must be an object")
    if type(action.get("created_at")) is not int or type(action.get("expires_at")) is not int:
        raise ClientError("action timestamps must be integers")
    rebuilt = make_action(
        action.get("operation"), action.get("owner"), action.get("operator"),
        signer.get("role"), signer.get("private_key"), dict(parameters),
        now=action["created_at"], intent_id=action.get("intent_id"),
    )
    if rebuilt != action:
        raise ClientError("action is noncanonical or inconsistent with client policy")
    return rebuilt


def execution_url(action: dict[str, Any], config: dict[str, Any]) -> str:
    action = validate_action_for_encryption(action)
    key_id = str(config.get("key_id", ""))
    if not KEY_ID_RE.fullmatch(key_id):
        raise ClientError("relay key ID is invalid or missing")
    raw_key = b64_decode(str(config.get("public_key_b64", "")))
    if len(raw_key) != 32:
        raise ClientError("relay public encryption key must be 32 bytes")
    base_url = validate_relay_origin(
        config.get("base_url"),
        allow_insecure_local=bool(config.get("allow_insecure_local_relay", False)),
    )
    ciphertext = _sealed_box_encrypt(raw_key, canonical_json(action))
    package = f"v1.{key_id}.{b64url_encode(ciphertext)}"
    return f"{base_url}/tx#p={package}"


def fetch_text(url: str) -> tuple[bool, str]:
    try:
        request = urllib.request.Request(url, headers={"Accept": "text/plain"})
        with urllib.request.urlopen(request, timeout=8) as response:
            return True, response.read(64_000).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return False, f"GET unavailable ({type(exc).__name__})"


def print_link(action: dict[str, Any], config: dict[str, Any], summary: str) -> None:
    url = execution_url(action, config)
    signer_role = action["signer"]["role"]
    signer_address = action["operator"] if signer_role == "operator" else action["owner"]
    print(f"ACTION={summary}")
    print(f"INTENT_ID={action['intent_id']}")
    print(f"EXPIRES_AT={action['expires_at']}")
    print(f"SIGNER_ROLE={signer_role}")
    print(f"SIGNER_ADDRESS={signer_address}")
    print("SIGNER_KEY_VALIDATED=true")
    print("ACTION_PACKAGE_VALIDATED=true")
    print(f"EXECUTION_URL={url}")
    print("OPENING_THIS_LINK_EXECUTES=true")


def add_identity(parser: argparse.ArgumentParser, role: str, *, require_key: bool = False) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    if not require_key:
        group.add_argument(f"--{role}-address")
    group.add_argument(f"--{role}-key-file")


def add_optional_identity(
    parser: argparse.ArgumentParser, role: str, *, require_key: bool = False,
) -> None:
    group = parser.add_mutually_exclusive_group()
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
    wallet = commands.add_parser("generate-wallet")
    wallet.add_argument("--role", choices=("owner", "operator"), default="owner")
    wallet.add_argument("--output-dir")
    status = commands.add_parser("status")
    add_identity(status, "owner")
    add_optional_identity(status, "operator")
    fund = commands.add_parser("fund-link")
    add_identity(fund, "owner", require_key=True)
    add_optional_identity(fund, "operator")
    fund.add_argument(
        "--operator-gas-policy",
        choices=("manual", "top_up_to_target"),
        default="top_up_to_target",
    )
    trade = commands.add_parser("trade-link")
    add_identity(trade, "owner")
    add_optional_identity(trade, "operator", require_key=True)
    sides = trade.add_subparsers(dest="side", required=True)
    sell = sides.add_parser("sell")
    sell.add_argument("--somi", required=True)
    sell.add_argument("--max-slippage-bps", required=True)
    buy = sides.add_parser("buy")
    buy.add_argument("--usdso", required=True)
    buy.add_argument("--max-slippage-bps", required=True)
    withdraw = commands.add_parser("withdraw-link")
    add_identity(withdraw, "owner", require_key=True)
    add_optional_identity(withdraw, "operator")
    withdraw.add_argument("--assets", choices=("SOMI", "USDso", "both"), default="both")
    transfer = commands.add_parser("transfer-link")
    add_identity(transfer, "owner")
    add_optional_identity(transfer, "operator")
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
    if args.command == "generate-wallet":
        address, key_path = generate_wallet(args.output_dir, role=args.role)
        role = args.role.upper()
        retained_key = read_private_key(str(key_path))
        if derive_address(retained_key) != address:
            raise ClientError("generated wallet self-check failed")
        print(
            f"{role}={address}\n{role}_KEY_FILE={key_path}\n"
            f"{role}_KEY_VALIDATED=true\n{role}_ADDRESS_MATCH_CONFIRMED=true"
        )
        return 0
    config = load_config(args)
    if args.command == "result":
        if not INTENT_RE.fullmatch(args.intent_id):
            raise ClientError("intent ID must be 32 lowercase hex characters")
        url = f"{config['base_url']}/v1/result/{args.intent_id}.txt"
        ok, body = fetch_text(url)
        print(body if ok else f"{body}; open this exact public URL once with a direct open/fetch/read tool; do not search for it:\n{url}")
        return 0
    owner, owner_key = role_address(getattr(args, "owner_address", None), args.owner_key_file, "owner")
    supplied_operator, operator_key = optional_role_address(
        getattr(args, "operator_address", None), getattr(args, "operator_key_file", None),
        "operator",
    )
    operator = supplied_operator or owner
    owner, operator = checked_pair(owner, operator)
    if supplied_operator is not None and owner == operator:
        raise ClientError("an explicitly supplied optional operator must be distinct from owner")
    delegated = owner != operator
    if args.command == "status":
        url = f"{config['base_url']}/v1/status/{owner}/{operator}.txt"
        lines = [f"OWNER={owner}", f"STATUS_URL={url}",
                 f"OWNER_EXPLORER_URL={EXPLORER_BASE_URL}/address/{owner}"]
        if delegated:
            lines.extend((f"OPERATOR={operator}",
                          f"OPERATOR_EXPLORER_URL={EXPLORER_BASE_URL}/address/{operator}"))
        print("\n".join(lines))
        ok, body = fetch_text(url)
        print(
            body if ok else
            f"{body}\nBALANCE_STATUS=UNKNOWN\n"
            "[failed reading] The relay status GET was unavailable. Some hosted web reads are "
            "not expected to work even when the relay and wallets are healthy. Do not infer "
            "or say that a displayed wallet is unfunded. Open STATUS_URL once with a direct "
            "open/fetch/read tool; if that fails, inspect the printed official explorer "
            "URL(s) for wallet-level SOMI. If you communicate a failed read, use "
            "[failed reading] and state the practical consequence briefly. Always show each "
            "explorer link immediately below its corresponding wallet address. Do not "
            "web-search for any URL"
        )
        return 0
    if args.command == "fund-link":
        parameters = {"operator_gas_policy": args.operator_gas_policy}
        action = make_action("fund", owner, operator, "owner", owner_key or "", parameters)
        if delegated:
            top_up = "top up the optional operator to 1 SOMI if needed" if args.operator_gas_policy == "top_up_to_target" else "require manual operator gas funding"
            target = f"Reach the 95 SOMI vault and operator-permission targets; {top_up}. "
        else:
            target = "Reach the 95 SOMI owner-vault target for direct-owner trading. "
        print_link(
            action,
            config,
            target +
            "For a wholly fresh setup, about 99 owner SOMI is useful guidance, not a cutoff; "
            "actual planned value and gas determine feasibility. At click time, the owner "
            "wallet must have enough SOMI for every missing setup value and worst-case gas",
        )
    elif args.command == "withdraw-link":
        assets = ["SOMI", "USDso"] if args.assets == "both" else [args.assets]
        revoke = len(assets) == 2
        parameters = {"assets": assets}
        action = make_action("withdraw", owner, operator, "owner", owner_key or "", parameters)
        asset_text = " and ".join(assets)
        permission_text = (
            f" and {'revoke' if revoke else 'keep'} optional-operator permissions"
            if delegated else ""
        )
        print_link(
            action, config,
            f"Withdraw all vault {asset_text} to the owner wallet{permission_text} "
            f"for {MARKET}",
        )
    elif args.command == "transfer-link":
        if args.from_role == "operator" and not delegated:
            raise ClientError("--from operator requires a distinct optional operator wallet")
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
        signer_role = "operator" if delegated else "owner"
        signer_key = operator_key if delegated else owner_key
        if signer_key is None:
            required = "operator" if delegated else "owner"
            raise ClientError(f"direct or delegated trading requires the selected {required} key file")
        action = make_action("trade", owner, operator, signer_role, signer_key, parameters)
        verb = "Sell" if args.side == "sell" else "Spend at most"
        print_link(action, config, f"{verb} {amount} {asset} using a market-style IOC trade on {MARKET}; max slippage {args.max_slippage_bps} bps")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
