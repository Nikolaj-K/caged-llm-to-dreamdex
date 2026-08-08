# Caged LLM to DreamDEX

This intentionally minimal public client lets an operating LLM optionally
generate two disposable session wallets, derive addresses, read a private
relay’s public status/results, and create encrypted execution links. It is
experimental and unsafe. Use only fresh wallets with trivial funds.

The product scope is fixed:

- Network: Somnia mainnet
- Market: `SOMI:USDso`
- Reads: public status, result, capabilities, and readiness
- Writes: fund/setup, bounded market-style IOC buy/sell, withdraw-all with
  permission revocation, and constrained wallet transfers

Opening a generated execution link automatically executes the exact action
described immediately before the link. There is no confirmation button. The
operating LLM must never open, prefetch, preview, or invoke the link for the
user. The deterministic operating procedure is in `AGENTS.md`.

Each action encrypts only its selected signer key. The relay backend can decrypt
it. Plaintext briefly exists in backend process memory while the relay validates
and prepares the action and, when writes are enabled, signs the transaction. It
is not intentionally written to files, databases, logs, HTML, or API results.
Generated keys live in `0600` temporary files the LLM/tool environment can read;
they are not printed by default and may disappear with that workspace. Treat all
wallets as disposable; process memory cannot be promised to be securely erased.

Transfers are limited to owner SOMI/USDso or operator SOMI, sent to one explicit
EVM address by exact or maximum amount. `max` means all available at signing,
after reserving native gas where applicable; exact mode remains the exact amount.
The client supports no limit orders,
arbitrary contracts/calldata, other markets, strategies, seed phrases, custody,
or autonomous trading. See `AGENTS.md` and `--help` for the operating flow.
