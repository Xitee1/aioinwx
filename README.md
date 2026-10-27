# aioinwx

![Status: Work in Progress](https://img.shields.io/badge/status-WIP-orange)
![AI-generated](https://img.shields.io/badge/code-AI--generated-blueviolet)

> [!WARNING]
> **Work in Progress:** The API of this library may still change.

> [!NOTE]
> **AI-generated:** The code, tests and documentation of this project were created with AI
> (Claude Code, Anthropic).

Asynchronous Python client for the [INWX](https://www.inwx.de/) DomRobot JSON-RPC API, built
on `aiohttp`. It covers the read-only calls needed to monitor an account: login with optional
2FA, account balance, domain list and renewal prices.

Used by the Home Assistant integration [ha-INWX](https://github.com/Xitee1/ha-INWX).

## Installation

```bash
pip install aioinwx
```

## Usage

```python
import asyncio

from aioinwx import InwxClient


async def main() -> None:
    async with InwxClient("username", "password", shared_secret=None) as client:
        await client.login()
        try:
            balance = await client.get_balance()
            print(f"Available: {balance.available}")
            for domain in await client.get_domains():
                price = await client.get_renewal_price(domain)
                print(domain.name, domain.renewal_date, price.gross, price.currency)
        finally:
            await client.logout()


asyncio.run(main())
```

`shared_secret` is the Base32 key from the INWX 2FA setup. It is only required if the account
uses two-factor authentication. The client generates the one-time codes itself and never uses
the same code twice: a second login within the same 30-second window waits for the next code.

### Session

Without `session`, the client creates its own `aiohttp.ClientSession` and closes it in
`close()` or at the end of the `async with` block. A passed session is not closed.

The API keeps the login in a session cookie. The client sends this cookie itself, so a passed
session should not store cookies (`aiohttp.DummyCookieJar()`) and must not be shared with a
client of another account.

## API

| Method | API call | Result |
|--------|----------|--------|
| `login()` | `account.login`, `account.unlock` | `InwxAccount` |
| `logout()` | `account.logout` | `None` |
| `get_balance()` | `accounting.accountBalance` | `InwxBalance` |
| `get_domains()` | `domain.list` (all pages) | `list[InwxDomain]` |
| `get_renewal_price(domain)` | `domain.getalldomainprices` | `InwxPrice` |

Timestamps are timezone-aware `datetime` objects. The API returns them as objects like
`{"scalar": "2027-08-21T20:28:56+00:00", "xmlrpc_type": "datetime", "timestamp": 1818880136}`.

## Exceptions

| Exception | Meaning |
|-----------|---------|
| `InwxError` | Base class of all errors. |
| `InwxConnectionError` | The API could not be reached or answered with an HTTP error. |
| `InwxAuthError` | The credentials were rejected. |
| `InwxTwoFactorRequiredError` | The account uses 2FA, but no shared secret was given. |
| `InwxTwoFactorError` | The 2FA code was rejected. |
| `InwxInvalidSharedSecretError` | The shared secret is not valid Base32. |
| `InwxApiError` | The API answered with an error code or an invalid response. |

The 2FA errors are subclasses of `InwxAuthError`.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```
