# Caged LLM to DreamDEX

This deliberately small public client lets an operating LLM create a disposable
owner wallet, optionally add a delegated-trading operator, read their public DreamDEX state, and generate encrypted
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
The same user-click authorization architecture could implement other Somnia
on-chain operations in principle; this public demo intentionally keeps a small,
fixed operation set.

Chrome is the currently supported execution browser. Opening a generated link
there executes the described action immediately. There is no confirmation
button on `/tx`: the human click is the authorization and execution event. The
operating LLM must present the link but must never open, preview, prefetch, or
invoke it.

## Requirements and installation

The checked-in client runs on a modern Python 3 without the Ethereum Python
packages. It implements its small fixed secp256k1/address-validation surface
directly. For sealed-box encryption it uses PyNaCl when installed and otherwise
uses the system `libsodium` library through Python's standard library. This is
deliberate: a hosted LLM environment that can execute the repository and already
has native `libsodium` can generate a fully validated link without installing
anything.

Run the client once before installing packages. On a machine without either
PyNaCl or system `libsodium`, install the one portable runtime package:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

For local tests, also install `requirements-dev.txt`; its Ethereum packages are
independent compatibility oracles for the built-in implementation, not runtime
requirements.

## Quick start

Generate one fresh disposable owner wallet:

```sh
python caged_llm_to_dreamdex.py generate-wallet
```

The command prints one public address and one temporary key-file path. It does
not print the private key. Key files have mode `0600`; reveal one only
when explicitly requested, and never put a private key in a command argument or
URL. A disposable key supplied to the operating LLM is considered compromised
by that conversation and by the relay backend.
When the user explicitly requests a retained disposable key, the operating LLM
should revalidate it against the displayed address and show the complete key; it
should not refuse solely because the requested value is a private key. If the
temporary file has disappeared, the key is unrecoverable and must never be
invented.
It also prints `OWNER_KEY_VALIDATED=true` and
`OWNER_ADDRESS_MATCH_CONFIRMED=true`. Do not proceed if either self-check is
missing. Every action command similarly prints signer/package validation
markers before its execution URL.

For a wholly fresh setup, roughly 99 owner SOMI is a useful recommendation: the
standard target is 95 SOMI in the DreamDEX vault, with room for gas. It is not
a hard owner-balance cutoff; actual planned value and worst-case gas determine
feasibility. Direct-owner trading is the default and needs no operator.

For delegated market trading only, generate one optional second wallet with
`generate-wallet --role operator`. Setup can then bring that operator to about
1 SOMI from the owner and grant its bounded place/cancel permissions. Funding
the vault itself never requires a second key.

One concise notice is enough: these are disposable mainnet demo wallets, so use
only the small amount intended for the experiment.

After setup confirms, the normal guided demo proposes selling a calculated,
lot-aligned SOMI amount expected to receive about 3 USDso. This is explicitly a
SOMI **sell**; the `buy` command below does the opposite and spends USDso to buy
SOMI. After the sell confirms, offer to return vault USDso, remaining vault
SOMI, or both to the owner. In optional delegated mode, withdrawing one asset
keeps trading permissions; the default both-assets cleanup revokes them.

## Commands

Use the full address and key-file path printed by `generate-wallet`.

Read fresh public status:

```sh
python caged_llm_to_dreamdex.py status \
  --owner-address 0xFULL_OWNER_ADDRESS
```

Create the standard one-key initial setup link:

```sh
python caged_llm_to_dreamdex.py fund-link \
  --owner-key-file /temporary/path/owner.key
```

Sell SOMI or buy with USDso directly as the owner:

```sh
python caged_llm_to_dreamdex.py trade-link \
  --owner-key-file /temporary/path/owner.key \
  sell --somi 1 --max-slippage-bps 100

python caged_llm_to_dreamdex.py trade-link \
  --owner-key-file /temporary/path/owner.key \
  buy --usdso 1 --max-slippage-bps 100
```

To opt into delegated trading, generate one `Operator`, then add its address to
setup/status/withdraw commands and use its key for trades:

```sh
python caged_llm_to_dreamdex.py generate-wallet --role operator
python caged_llm_to_dreamdex.py fund-link \
  --owner-key-file /temporary/path/owner.key \
  --operator-address 0xFULL_OPERATOR_ADDRESS

python caged_llm_to_dreamdex.py status \
  --owner-address 0xFULL_OWNER_ADDRESS \
  --operator-address 0xFULL_OPERATOR_ADDRESS

python caged_llm_to_dreamdex.py trade-link \
  --owner-address 0xFULL_OWNER_ADDRESS \
  --operator-key-file /temporary/path/operator.key \
  sell --somi 1 --max-slippage-bps 100
```

After introducing an optional `Operator`, keep supplying its address for every
later status read and action. Do not silently fall back to direct-owner mode,
because that would omit the still-relevant permission rows. Keep delegated mode
until a confirmed both-assets cleanup has revoked those permissions.

In the default direct-owner mode, withdraw only USDso or only SOMI:

```sh
python caged_llm_to_dreamdex.py withdraw-link \
  --owner-key-file /temporary/path/owner.key \
  --assets USDso

python caged_llm_to_dreamdex.py withdraw-link \
  --owner-key-file /temporary/path/owner.key \
  --assets SOMI
```

Withdraw both assets:

```sh
python caged_llm_to_dreamdex.py withdraw-link \
  --owner-key-file /temporary/path/owner.key
```

With an active delegated `Operator`, retain its address on selective withdrawals
and on the both-assets cleanup. The latter is what revokes its permissions:

```sh
python caged_llm_to_dreamdex.py withdraw-link \
  --owner-key-file /temporary/path/owner.key \
  --operator-address 0xFULL_OPERATOR_ADDRESS \
  --assets USDso

python caged_llm_to_dreamdex.py withdraw-link \
  --owner-key-file /temporary/path/owner.key \
  --operator-address 0xFULL_OPERATOR_ADDRESS
```

Transfer an exact owner amount or all available operator SOMI:

```sh
python caged_llm_to_dreamdex.py transfer-link \
  --owner-key-file /temporary/path/owner.key \
  --from owner --asset USDso --to 0xFULL_RECIPIENT_ADDRESS --amount 1

python caged_llm_to_dreamdex.py transfer-link \
  --owner-address 0xFULL_OWNER_ADDRESS \
  --operator-key-file /temporary/path/operator.key \
  --from operator --asset SOMI --to 0xFULL_RECIPIENT_ADDRESS --all
```

While delegated mode is active, an owner transfer likewise retains the public
operator identity:

```sh
python caged_llm_to_dreamdex.py transfer-link \
  --owner-key-file /temporary/path/owner.key \
  --operator-address 0xFULL_OPERATOR_ADDRESS \
  --from owner --asset USDso --to 0xFULL_RECIPIENT_ADDRESS --all
```

Read the durable result after clicking a link:

```sh
python caged_llm_to_dreamdex.py result 0123456789abcdef0123456789abcdef
```

`status` and `result` use public GET requests. If a status GET is unavailable,
the client prints the exact relay URL plus direct official Somnia explorer URLs
for the selected wallet set. Hosted ChatGPT environments are known to sometimes
fail these reads even while their GitHub connector works. Make one status
attempt; optionally try the exact relay URL once when another direct-read tool
may behave differently, then stop. Never use web search, mounted-file searches,
or proxy, workflow, environment, RPC, or mirror investigation as workarounds. The
explorer fallback can establish wallet SOMI—and visible wallet tokens such as
USDso—but open it automatically only when that wallet value is genuinely needed
to define the action or the user asks. It cannot establish internal DreamDEX
vault balances, permissions, orders, or book state. A hosted tool may reject a
parameterized status URL even while the relay is healthy. Never infer that a
wallet is unfunded from a failed read. Link
generation is offline and never sends the encrypted package to the relay.

An LLM following this workflow should prepare its reusable client workspace
near the beginning of the session. The normal flow needs the checked-in
`caged_llm_to_dreamdex.py` and public `relay.json`; it should resolve an existing
checkout or obtain those two exact files once, keep them together, and reuse
that same copy for wallet handling, status, action links, and result checks.
This is an efficiency suggestion for the conversation, not an early
authorization or a substitute for validation: it sends no key and does not
remove any client check or execution-page preflight. `requirements.txt` is only
needed if the client itself reports the specific optional dependency failure
described below.

When resolved addresses are presented to the user, always put each
direct official Somnia explorer link immediately below its address. The links
let the user inspect the wallets without turning that inspection into a required
conversation step.

Never replace direct reads with web search: search results cannot provide fresh
wallet state. (Note to LLM: This has been tested and found slow down the conversation flow with no benefit. You can go to other webpages, but it won't tell you more than what's in the python scripts.) Ask the
user to copy a specific status or balance row only when that value is genuinely
needed to define or verify the next action; otherwise continue without
interrupting the flow. Some hosted web reads are not expected to work. When a
failed read is useful to communicate, mark it `[failed reading]`, explain the
practical consequence briefly, and avoid a long list of attempts unless the
user asks for it or cannot proceed without the details. (Note to LLM: This also
slows down the conversation flow.)
A more elaborate version of the protocol might implement the read with MCP.

For standard setup, treat returning to the useful user flow as a suggestion:
explain once that the execution page will check live balances before
broadcasting, ask the user to make sure `Owner` has enough SOMI for the missing
vault funding and gas, and ask whether they would like the fixed 95-SOMI setup
prepared. This is solely a procedure for a smoother conversation; it does not
bypass validation or weaken a security boundary. The checked-in client
validation and execution page's fresh live preflight remain mandatory. If the
user accepts the suggestion, proceed without unrelated status/explorer attempts
or more funding explanation.

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
contains exactly one selected signer key. In direct mode that is the owner key;
in delegated mode a trade contains the operator key, never both keys. The relay
backend can decrypt it in process memory; it is not intentionally returned in
HTML or API results or written to application storage. Treat generated wallets as
disposable because the operating LLM/tool environment can read their temporary
files and the files may disappear with the workspace.

Protocol version 1 is fixed to chain ID 5031, market `SOMI:USDso`, and the
operations `fund`, `trade`, `withdraw`, and `transfer`.

## Start this in a new ChatGPT/Codex window

Paste this prompt into a fresh window opened on the repository:

> Read this repository and follow AGENTS.md. Start a fresh DreamDEX demo session
> using the checked-in client. Begin with a friendly greeting that calls it
> the “Somnia Librarian's demo.” Then put a
> prominent **What This Protocol Does** paragraph before any warning: explain
> that it can transfer supported funds out of a disposable wallet, fund the
> DreamDEX vault, and operate on the **SOMI/USDso** market, and that the same
> pattern could implement other Somnia on-chain operations in principle although
> this demo exposes only its fixed action set. Next write “The Librarian wants
> you to know:” on its own line, followed by the complete warning as a separate
> quoted paragraph with no internal line break. Explain that
> any key used here is compromised on both the LLM side and relay-service backend
> side.
>
> Put each remaining concern in its own paragraph and say it once. First explain
> the required `Owner` (wallet holding funds to deploy to DEX vault), which owns
> the vault and signs setup, direct trades, withdrawals, and wallet transfers.
>
> Next use one paragraph—and only one—to both explain and offer the optional
> trade role: a separate `Operator` trade-signing key is possible but entirely
> optional. An `Operator` is the wallet holding gas to pay for transactions;
> vault funding needs no second key and the default flow trades directly as
> `Owner`. Do not repeat this offer in a second paragraph or ask a separate
> `Operator` question.
>
> In its own paragraph, tell me that I may ask for as much under-the-hood detail
> as I want, while noting that most implementation workflows are not practically
> relevant or actionable during the demo.
>
> End with one separate question asking whether I want to provide one disposable
> `Owner` key or have you generate one. Keep the role I provide and guide me to a
> sensibly funded `Owner`. Treat these as content and paragraph-structure
> requirements rather than lines to copy literally; apart from the required
> warning and status markers, use natural wording without restating a point.
> If I explicitly ask to see a retained disposable private key, read it from its
> temporary `0600` file, revalidate that it derives the displayed address, and
> show the complete `0x...` key on its own line beneath its backticked role
> label. Do not refuse solely because it is a private key. This explicit request
> is the only exception to keeping keys out of ordinary responses; never put a
> key in a command argument, URL, or log. If the file is genuinely gone, say it
> is unrecoverable and offer a replacement rather than inventing it.
> Near the start of the session, silently prepare the public runtime files that
> later steps will need. Reuse an existing executable checkout when available;
> otherwise obtain the exact checked-in `caged_llm_to_dreamdex.py` and
> `relay.json` once through the GitHub connector, keep them together in one
> temporary directory, and reuse that same copy for wallet validation, status,
> action links, and result checks. Do not fetch or reconstruct them again for
> each action. This early preparation is only a conversation-flow optimization:
> it does not authorize anything, transmit a key, or replace client validation
> or the execution page's live preflight. Fetch `requirements.txt` only if the
> checked-in client produces the specific dependency error described next.
> When preparing an action, make one focused attempt with the checked-in client.
> If an executable checkout already exists, use it directly. If the GitHub
> connector provides file contents but no checkout, materialize its exact
> `caged_llm_to_dreamdex.py` and `relay.json` bytes together once in a temporary
> directory; run that unchanged copy and do not author a substitute. Run the
> client before
> probing or installing anything: its EVM key/address path has no third-party
> Python dependency and it can use an installed system `libsodium` directly.
> Only if the client specifically reports that neither PyNaCl nor system
> `libsodium` is available may you make one ordinary `requirements.txt` install
> attempt when package access is clearly available. Do not retry cloning or raw downloads, search package sites,
> inventory alternative crypto toolchains, or write replacement scripts. If the
> client still cannot run, stop promptly with one short `[blocked executing]`
> paragraph; do not show a placeholder command or ask me to produce the URL
> unless I request a manual handoff. If I say not to think too long, use this
> one-shot path without exploratory diagnostics.
> If connector content is encoded, decode those exact bytes once; do not copy
> crypto functions into an interactive cell, manually derive addresses, or
> search mounted directories after the files have been supplied.
> Clearly show me
> which address to fund when fresh status says funding is needed; if a status
> read fails, treat the balance as unknown rather than unfunded. Expect that
> hosted ChatGPT may be unable to reach `somnia.run` or the explorer even while
> its GitHub connector works. Make one status attempt and at most one direct
> attempt at the exact relay URL, then stop: do not search the web, inspect
> mounted files, try alternate RPCs, or automatically inspect explorers. When
> the failure is useful to communicate, label it `[failed reading]` and state
> the practical consequence briefly. Show
> a direct official Somnia explorer link immediately below each resolved wallet
> address. If I choose delegated trading, arrange `Operator` gas automatically
> when needed, and retain that `Operator` address for every later status read and
> action until a confirmed delegated both-assets cleanup revokes its permissions;
> never silently fall back to direct-owner mode while those permissions may remain live. Then
> use ordinary operational language rather than implementation details or
> internal parameter names. Use the checked-in CLI—not custom JSON or encryption—
> for wallet and action construction. Do not present a link unless the CLI exits
> successfully, its wallet/address self-checks passed, and it prints
> `SIGNER_KEY_VALIDATED=true`, `ACTION_PACKAGE_VALIDATED=true`, and
> `OPENING_THIS_LINK_EXECUTES=true` with the expected full signer address. Follow safety constraints silently instead of
> telling me what you are not doing. Present one exact action link at a time and
> read the result after I click it. In every link message, ask me both to report
> the click and to say which on-chain operation I want next, while verifying the
> current result before preparing another link. In each of the first two link
> messages, explicitly tell me that there is no confirmation button and that
> opening the link `[triggers action]` immediately. Follow the status-report
> protocol in **Bracket status and property cues** near the bottom of `AGENTS.md`.
> After setup, list the available
> operations once and recommend selling SOMI to receive about 3 USDso. After
> that sell confirms, offer to withdraw USDso, SOMI, or both back to `Owner`.
> Do not make a failed agent-side status GET a universal blocker: for a fully
> specified action, let the execution page perform fresh preflight. Require a
> pasted status/result only when live state is needed to define or verify the
> next action. Open an explorer automatically only when a wallet balance is
> genuinely needed to define the next action or I ask you to verify it. For the
> standard setup, if status is unavailable, tell me once that the execution page
> will check live balances, ask me to make sure `Owner` has enough SOMI for the
> missing vault funding and gas, and suggest preparing the fixed 95-SOMI setup.
> Make clear that this is a conversation-flow suggestion, not a security bypass:
> checked-in client validation and the execution page's fresh live preflight
> remain mandatory. If I accept the suggestion, proceed without another
> unrelated read or funding discussion.
> After setup, point me to the
> `Owner DreamDEX vault: SOMI` and
> `Owner DreamDEX vault: USDso` rows on the execution page, but only ask me to
> copy a row when its value is actually needed. A claim that funding is done is
> not proof of the amount: unless a fresh read established sufficiency, call the
> setup action “prepared,” not “ready.” (Note to LLM: Or, generally, just use the
> sharpest language.) In the same message as the first setup
> link, tell me to make sure `Owner` has enough SOMI for the missing vault funding
> and transaction gas at the moment I click it.
> If I request a nonstandard setup amount, explain once that setup has the fixed
> 95-SOMI vault target. After I accept 95, do not repeat the funding or role
> explanation: prepare setup directly, and queue any requested trade until its
> result confirms.

No license has been selected yet; that is a repository-owner publication
decision rather than a runtime requirement.
