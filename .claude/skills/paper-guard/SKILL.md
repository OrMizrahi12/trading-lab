---
name: paper-guard
description: Safety check for Interactive Brokers (IB) code. Use whenever code, config, notebooks, or .env files connect to Interactive Brokers (ib_insync, ib_async, ibapi, TWS, IB Gateway, `IB().connect`, port 7497/7496/4002/4001, account IDs, `placeOrder`), or when writing or reviewing anything that places orders through IB. Verifies it uses a paper (simulated, fake-money) port and account, never a live (real-money) one. This repo is paper trading only.
---

# paper-guard

This repo is paper trading only (see `AGENTS.md`). Paper trading means practising with simulated money. Live trading means real money. This skill checks that IB code can only touch the paper side.

## Port reference

A port is the numbered "door" on your computer that IB's software listens on. The program (TWS = Trader Workstation, IB Gateway = a lighter headless version) decides which account you reach based on this number.

| Program | Paper (allowed) | Live (forbidden) |
|---|---|---|
| TWS | 7497 | 7496 |
| IB Gateway | 4002 | 4001 |

## Checklist

Check every file touched or created, including `.py`, notebooks, `.env`, `.toml`, `.yaml`, and `.json`.

1. **Port**: every connect call uses 7497 or 4002. Flag 7496 or 4001 as a failure. Flag any other port as unknown.
2. **Port is explicit**: the port is written out or read from config that resolves to a paper port. A missing port relies on library defaults, so flag it. A port taken from user input or an unchecked env var with no paper assertion is also a flag.
3. **Account ID**: any account ID (hardcoded, in config, or passed to an order call) starts with `DU`, IB's prefix for paper accounts. Anything else is treated as possibly live and fails. Never commit real account IDs or credentials.
4. **Orders**: no order is placed (`placeOrder`, `reqGlobalCancel`, `order`-style helpers, or similar) unless the connection and account above both pass checks 1 and 3. Orders on a live account always fail.
5. **Guard in code**: new connect code should assert the paper port and a `DU` account before any order, and fail loudly if either is wrong.
6. **Host**: connections go to `127.0.0.1` / `localhost`. Flag remote hosts, since they may reach a different machine's live session.

## Report format

Report in this shape, in plain English:

```
paper-guard: PASS | FAIL | NEEDS REVIEW

Files checked: <list>

Findings:
- [PASS|FAIL|FLAG] <file>:<line> <what was found, e.g. "connects on port 7497 (TWS paper)">

Required fixes: <only if FAIL or FLAG; the exact change>
```

- **PASS**: all checks clear.
- **FAIL**: any live port, non-`DU` account, or order path that could reach a live account. Do not proceed; propose the fix first.
- **NEEDS REVIEW**: unknown port, missing port, or values that can't be determined statically (for example, they come from env vars that aren't visible). Say what you couldn't verify.

If a request would require a live port or live account, refuse that part and explain it breaks the repo's paper-only rule.
