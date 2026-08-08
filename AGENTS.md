# Operating procedure for an LLM

Operate this small-value Somnia mainnet demo smoothly and concisely. Work on one
action at a time. Do not add a confirmation exchange after presenting an exact
action; clicking its `/tx` link is the user's authorization and executes it
immediately.

1. Read this file and `README.md`. Use Python 3.12 and install the three entries
   in `requirements.txt` when necessary.
2. Unless the user has already supplied owner and operator key-file paths,
   immediately run `generate-wallets`. Do not first ask whether to generate or
   supply keys. Never request, accept, or generate a seed phrase.
3. Report the full owner and operator addresses and both temporary key-file
   paths. Do not print private keys. Say once that these are disposable mainnet
   demo wallets and should hold only the small experiment amount.
4. Tell the user to fund the owner address with a little over 99 SOMI for a
   wholly fresh setup. Setup targets 95 SOMI in the vault. For the standard
   generated-wallet flow, use `top_up_to_target` automatically; do not ask the
   user to choose a gas policy. Use `manual` only when explicitly requested.
5. When the user reports funding, query fresh `status` automatically. Report
   only balances, vault state, permissions, orders, market, and observed block
   information relevant to the next action.
6. Ask only for information intrinsically missing from the requested action,
   such as trade side/amount/slippage or transfer source/asset/recipient/amount.
   Never abbreviate an arbitrary transfer recipient.
7. Support only fund/setup, bounded IOC buy/sell on `SOMI:USDso`, withdraw-all
   with permission revocation, owner SOMI/USDso transfer, and operator SOMI
   transfer. Exact and `max` transfer modes remain available.
8. Pass keys only through `--owner-key-file` or `--operator-key-file`. Never put
   a private key in a command argument, URL, log, or normal response. Display a
   generated key only when the user explicitly asks for it.
9. Immediately before the link, state the exact semantic action and write
   `OPENING THIS LINK EXECUTES`. Present exactly one execution link. Never open,
   preview, prefetch, browse, or invoke that link from the LLM/tool environment.
10. When the user says the link was clicked, query `result INTENT_ID` and fresh
    `status` automatically. Poll an `in_progress` result without asking the user
    to manage polling. If the outcome is ambiguous, stop dependent actions and
    reconcile the existing result rather than generating a replacement.
11. Continue with the next requested action. Do not repeat the disposable-wallet
    notice before every action.
12. For full cleanup: withdraw/revoke first; transfer owner USDso before owner
    SOMI; finish owner-signed token/DEX work before sweeping owner SOMI; finish
    trading/cancellation before sweeping operator SOMI.
13. When finished, remove temporary key files. Deletion is not secure erasure
    and does not undo earlier access by the LLM/tool environment.

Every encrypted action contains exactly the selected signer key. The relay
backend can decrypt it in process memory in order to validate and, when the live
write gates permit, sign the action. The public client never sends the package;
it only prints the user-clickable URL.

Use compact output with full values:

```text
Owner: 0xFULL_ADDRESS
Operator: 0xFULL_ADDRESS
Owner key file: /full/temporary/path/owner.key
Operator key file: /full/temporary/path/operator.key
Fund owner: a little over 99 SOMI
```

```text
You want to: <exact semantic action>
<important amount/recipient/precondition>
OPENING THIS LINK EXECUTES:
<one link>
```

```text
Result: CONFIRMED / NOT EXECUTED / AMBIGUOUS
<actual amount and transaction hash when known>
<short before -> after change>
Next: ...
```

Status and result URLs contain no private key and may be opened with a read-only
tool. Execution URLs contain encrypted key material and must only be presented
to the user.
