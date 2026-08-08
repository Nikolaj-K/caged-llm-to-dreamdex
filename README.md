# Caged LLM to DreamDEX

This deliberately small public client lets an operating LLM create disposable
owner/operator wallets, read their public DreamDEX state, and generate encrypted
links for exact actions on Somnia mainnet. The live relay is
[`https://somnia.run`](https://somnia.run), and its execution page is
[`https://somnia.run/tx`](https://somnia.run/tx).

Somnia mainnet is the only network in this protocol: it is EVM chain ID `5031`
and uses Ethereum-style `0x...` addresses. It is not Solana.

The demo supports initial SOMI funding/setup, bounded market-style IOC buys and
sells on `SOMI:USDso`, selective withdrawal of vault SOMI, USDso, or both, and
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

After setup confirms, the normal guided demo proposes selling a calculated,
lot-aligned SOMI amount expected to receive about 3 USDso. This is explicitly a
SOMI **sell**; the `buy` command below does the opposite and spends USDso to buy
SOMI. After the sell confirms, offer to return vault USDso, remaining vault
SOMI, or both to the owner. Withdrawing one asset keeps trading permissions;
the default both-assets cleanup revokes them.

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

Withdraw only USDso or only SOMI while keeping trading permissions:

```sh
python caged_llm_to_dreamdex.py withdraw-link \
  --owner-key-file /temporary/path/owner.key \
  --operator-address 0xFULL_OPERATOR_ADDRESS \
  --assets USDso

python caged_llm_to_dreamdex.py withdraw-link \
  --owner-key-file /temporary/path/owner.key \
  --operator-address 0xFULL_OPERATOR_ADDRESS \
  --assets SOMI
```

Withdraw both assets and revoke operator permissions:

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

`status` and `result` use public GET requests. If a status GET is unavailable,
the client prints the exact relay URL plus direct official Somnia explorer URLs
for both wallets. Open those URLs directly; never use web search to find them or
investigate proxy, workflow, environment, RPC, or mirror workarounds. The
explorer fallback can establish wallet SOMI—and visible wallet tokens such as
USDso—but not internal DreamDEX vault balances, permissions, orders, or book
state. A hosted tool may reject a parameterized status URL even while the relay
is healthy. Never infer that a wallet is unfunded, and never contradict or
repeat a funding request to a user who has already said it is funded. Link
generation is offline and never sends the encrypted package to the relay.

Never replace direct reads with web search: search results cannot provide fresh
wallet state. Keep failed read mechanics out of ordinary conversation. Ask the
user to copy a specific status or balance row only when that value is genuinely
needed to define or verify the next action; otherwise continue without
interrupting the flow.

An agent-side status GET is useful but is not a universal prerequisite for link
generation. The execution page always performs a fresh chain read and preflight
before broadcasting. If the hosted environment cannot read status, the agent
may still prepare target-state setup, withdrawal of all selected assets,
exact/`max` transfers, and exact-input trades; the page will execute or show
`NOT READY` without broadcasting. Fresh status remains necessary when the agent
must calculate an action parameter from balances or the order book, or verify a
preceding action before preparing a dependent one.

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
> holding gas to pay for transactions). Before asking me to paste keys or
> offering to generate them, give the Somnia Librarian warning that keys used
> here must be considered compromised on both the LLM side and the relay-service
> backend side. Then ask whether I want to provide existing disposable keys or
> have you generate up to two missing wallets. Keep the roles I provide and guide me to
> a state with two distinct keys and a sensibly funded `Owner`. Clearly show me
> which address to fund when fresh status says funding is needed; if a status
> read fails, call the balance unknown rather than unfunded and preserve my
> report that it is funded. Arrange `Operator` gas automatically when needed, and
> use ordinary operational language rather than implementation details or
> internal parameter names. Follow safety constraints silently instead of
> telling me what you are not doing. Present one exact action link at a time and
> read the result after I click it. In every link message, ask me both to report
> the click and to say which on-chain operation I want next, while verifying the
> current result before preparing another link. After setup, list the available
> operations once and recommend selling SOMI to receive about 3 USDso. After
> that sell confirms, offer to withdraw USDso, SOMI, or both back to `Owner`.
> Do not make a failed agent-side status GET a universal blocker: for a fully
> specified action, let the execution page perform fresh preflight. Require a
> pasted status/result only when live state is needed to define or verify the
> next action. If relay status is unavailable, directly inspect the two official
> Somnia explorer address pages for wallet SOMI without narrating the retrieval
> mechanics. After setup, point me to the `Owner DreamDEX vault: SOMI` and
> `Owner DreamDEX vault: USDso` rows on the execution page, but only ask me to
> copy a row when its value is actually needed.

No license has been selected yet; that is a repository-owner publication
decision rather than a runtime requirement.
