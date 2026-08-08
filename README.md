# Caged LLM to DreamDEX

This deliberately small public client lets an operating LLM create disposable
owner/operator wallets, read their public DreamDEX state, and generate encrypted
links for exact actions on Somnia mainnet. The live relay is
[`https://somnia.run`](https://somnia.run), and its execution page is
[`https://somnia.run/tx`](https://somnia.run/tx).

The demo supports initial SOMI funding/setup, bounded market-style IOC buys and
sells on `SOMI:USDso`, full vault withdrawal with permission revocation, and
constrained owner/operator wallet transfers. It does not support limit orders,
arbitrary contracts or calldata, other markets, seed phrases, custody,
strategies, or autonomous trading.

Chrome is the currently supported execution browser. Opening a generated link
there executes the described action immediately. There is no confirmation
button on `/tx`: the human click is the authorization and execution event. The
operating LLM must present the link but must never open, preview, prefetch, or
invoke it.

## Requirements and installation

Python 3.12 is the supported baseline. From a fresh checkout:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

The three runtime dependencies are `eth-account`, `eth-utils`, and `PyNaCl`.
For local tests, also install `requirements-dev.txt`.

## Quick start

Generate a fresh disposable owner/operator pair immediately:

```sh
python caged_llm_to_dreamdex.py generate-wallets
```

The command prints two public addresses and two temporary key-file paths. It
does not print either private key. Key files have mode `0600`; reveal one only
when explicitly requested, and never paste a private key into ordinary chat or
put it in a command argument or URL.

For a wholly fresh setup, roughly 99 owner SOMI is a useful recommendation: the
standard target is 95 SOMI in the DreamDEX vault, with room for an operator
top-up and gas. It is not a hard owner-balance cutoff; actual planned value and
worst-case gas determine feasibility. The standard generated-wallet flow uses
`top_up_to_target`, bringing an unfunded operator to about 1 SOMI from the owner.
Explicit `manual` mode remains available whenever the operator has a positive
SOMI balance for gas; about 1 SOMI is recommended rather than required.

One concise notice is enough: these are disposable mainnet demo wallets, so use
only the small amount intended for the experiment.

## Commands

Use the full addresses and key-file paths printed by `generate-wallets`.

Read fresh public status:

```sh
python caged_llm_to_dreamdex.py status \
  --owner-address 0xFULL_OWNER_ADDRESS \
  --operator-address 0xFULL_OPERATOR_ADDRESS
```

Create the standard initial setup link (`top_up_to_target` is the default):

```sh
python caged_llm_to_dreamdex.py fund-link \
  --owner-key-file /temporary/path/owner.key \
  --operator-address 0xFULL_OPERATOR_ADDRESS
```

Sell SOMI or buy with USDso using the operator:

```sh
python caged_llm_to_dreamdex.py trade-link \
  --owner-address 0xFULL_OWNER_ADDRESS \
  --operator-key-file /temporary/path/operator.key \
  sell --somi 1 --max-slippage-bps 100

python caged_llm_to_dreamdex.py trade-link \
  --owner-address 0xFULL_OWNER_ADDRESS \
  --operator-key-file /temporary/path/operator.key \
  buy --usdso 1 --max-slippage-bps 100
```

Withdraw the entire vault and revoke operator permissions:

```sh
python caged_llm_to_dreamdex.py withdraw-link \
  --owner-key-file /temporary/path/owner.key \
  --operator-address 0xFULL_OPERATOR_ADDRESS
```

Transfer an exact owner amount or all available operator SOMI:

```sh
python caged_llm_to_dreamdex.py transfer-link \
  --owner-key-file /temporary/path/owner.key \
  --operator-address 0xFULL_OPERATOR_ADDRESS \
  --from owner --asset USDso --to 0xFULL_RECIPIENT_ADDRESS --amount 1

python caged_llm_to_dreamdex.py transfer-link \
  --owner-address 0xFULL_OWNER_ADDRESS \
  --operator-key-file /temporary/path/operator.key \
  --from operator --asset SOMI --to 0xFULL_RECIPIENT_ADDRESS --all
```

Read the durable result after clicking a link:

```sh
python caged_llm_to_dreamdex.py result 0123456789abcdef0123456789abcdef
```

`status` and `result` use public GET requests. If a GET is unavailable, the
client prints the exact public read URL. Open that URL directly with an
open/fetch/read tool; never use web search to find it or investigate proxy,
workflow, environment, RPC, or mirror workarounds. If the one direct fallback
also fails, give the user a short Markdown link to the exact URL and ask them to
paste back the result. Link generation is offline and never sends the encrypted
package to the relay.

## Public/private boundary

This repository contains only the public protocol client and public relay
configuration. `relay.json` contains an X25519 **public** encryption key and key
ID because the client needs them to encrypt one selected signer key into the
action package. The public key cannot decrypt that package.

The private relay backend owns decryption, validation, status reads, DreamDEX contract
integration, signing, durable results, and deployment operations. Each action
contains exactly the selected owner or operator key, never both. The relay
backend can decrypt it in process memory; it is not intentionally returned in
HTML or API results or written to application storage. Treat generated wallets as
disposable because the operating LLM/tool environment can read their temporary
files and the files may disappear with the workspace.

Protocol version 1 is fixed to chain ID 5031, market `SOMI:USDso`, and the
operations `fund`, `trade`, `withdraw`, and `transfer`.

## Start this in a new ChatGPT/Codex window

Paste this prompt into a fresh window opened on the repository:

> Read this repository and follow AGENTS.md. Install its dependencies and start
> a fresh DreamDEX demo session. On their first mention, call them the `Owner`
> (wallet holding funds to deploy to DEX vault) and the `Operator` (wallet
> holding gas to pay for transactions). Ask whether I want to provide
> existing disposable keys or have you
> generate up to two missing wallets. Keep the roles I provide and guide me to
> a state with two distinct keys and a sensibly funded `Owner`. Clearly show me
> which address to fund, arrange `Operator` gas automatically when needed, and
> use ordinary operational language rather than internal parameter names. Never
> open an execution URL yourself. Present one exact action link at a time and
> read the result after I click it.

No license has been selected yet; that is a repository-owner publication
decision rather than a runtime requirement.
