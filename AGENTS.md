# Operating protocol for an LLM

This repository is the complete public client. Read it before handling keys.
Use the strongest available reasoning mode for transaction work, but never
treat reasoning strength as a correctness or security control.

Before asking for keys, tell the user all of the following:

- This is a minimal experimental implementation and is not safe.
- Only newly created disposable wallets with trivial funds may be used.
- Both private keys entered into the conversation must be treated as
  permanently compromised.
- Every execution link contains one private key encrypted to the relay; the
  relay holds the decryption key and can recover it.
- The relay is trusted infrastructure, not a trustless system.

Then follow this procedure exactly:

1. Ask for exactly two newly generated disposable EVM private keys: owner and
   operator. Never ask for a seed phrase.
2. Put them into separate temporary files with mode `0600`, without echoing
   either key in a command, log, or response. Deleting a file does not undo its
   disclosure in the conversation.
3. Derive and display both public addresses with the client.
4. Run `status` and clearly report owner wallet SOMI/USDso, operator wallet
   SOMI/USDso, owner vault SOMI/USDso for `SOMI:USDso`, manual-vault mode,
   operator place/cancel permissions, owner open orders, best bid/ask, and the
   observed block.
5. Setup requires strictly more than 99 SOMI in the owner wallet and strictly
   more than 4 SOMI in the operator wallet. The operator must already be funded;
   setup never transfers owner SOMI to the operator.
6. As soon as those conditions hold, generate the `fund-link`. Immediately
   before presenting it, restate the exact action and label it clearly:
   `OPENING THIS LINK EXECUTES`.
7. Never open, invoke, or preview an execution URL with an execution-capable
   browser. Present it to the user. A normal preview is inert because the
   ciphertext is in the URL fragment and the initial GET does nothing.
8. Before every trade or withdrawal link, restate its exact action. Only fund,
   market-style IOC buy/sell on `SOMI:USDso`, and withdraw-all plus permission
   revocation are supported. Refuse generic transfers, arbitrary recipients or
   calls, other markets, limit/GTC/post-only orders, and strategies.
9. After the user opens a link, query both `result` and fresh `status`. Never
   claim success from page text or receipt status alone.
10. Delete both temporary key files after withdrawal/cleanup or when the user
    stops. Do not claim that deletion makes disclosed keys safe.

When an ordinary status/result GET cannot run in the code environment, use its
printed public `.txt` URL with the available browsing/read tool. Never send a
private key to status or result endpoints.
