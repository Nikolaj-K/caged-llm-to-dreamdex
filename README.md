# Caged LLM to DreamDEX

This is a minimal experimental protocol client. It is not safe. Use only newly
created disposable wallets with trivial funds. Any private key entered into an
LLM conversation is permanently compromised, even after a temporary file is
deleted. The private relay can decrypt the one key inside an execution link and
is not trustless.

## Boundary

This repository knows only the versioned semantic protocol. It derives public
addresses, reads public relay endpoints, encrypts one action, and prints an
execution URL. DreamDEX addresses, ABIs, pricing, transaction construction,
signing, broadcasting, receipts, storage, and the website live in the private
`caged-llm-dreamdex-relay` sibling.

Version 1 is fixed to Somnia mainnet (chain 5031), market `SOMI:USDso`, and
three actions: fund, IOC-style buy/sell, and withdraw-all-with-revocation.

## Setup

Use Python 3.11 or newer:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp relay.json.example relay.json
```

Fill `relay.json` with public values from the relay's capabilities endpoint.
Private keys are accepted only through files. Follow `AGENTS.md` before using
them.

## Commands

```text
python caged_llm_to_dreamdex.py status ...
python caged_llm_to_dreamdex.py fund-link ...
python caged_llm_to_dreamdex.py trade-link sell --somi AMOUNT ...
python caged_llm_to_dreamdex.py trade-link buy --usdso AMOUNT ...
python caged_llm_to_dreamdex.py withdraw-link ...
python caged_llm_to_dreamdex.py result INTENT_ID
```

Run `--help` on the script or a subcommand for exact address/key-file options.
Opening a generated `/run#p=...` link executes the described action. The client
must never open that link itself. Status and result URLs contain no secret.
