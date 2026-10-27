"""Tests for the INWX client."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
import pyotp
import pytest

from aioinwx import (
    API_URL,
    InwxAccount,
    InwxApiError,
    InwxAuthError,
    InwxBalance,
    InwxClient,
    InwxConnectionError,
    InwxDomain,
    InwxInvalidSharedSecretError,
    InwxPrice,
    InwxTwoFactorError,
    InwxTwoFactorRequiredError,
    normalize_shared_secret,
)

from .conftest import FakeApi

ClientFactory = Callable[..., InwxClient]

SHARED_SECRET = "JBSWY3DPEHPK3PXP"

# Response layouts as returned by the live API (values replaced).
LOGIN = {
    "accountId": 1,
    "customerId": 12345,
    "customerNo": 67890,
    "dyndns": 0,
    "required2fa": 0,
    "tfa": "0",
}
LOGIN_TFA = {**LOGIN, "tfa": "GOOGLE-AUTH"}
BALANCE = {"total": 29.3, "available": 14.03, "locked": 0, "creditLimit": 0}


def date_time(scalar: str, timestamp: int) -> dict[str, Any]:
    """Return a dateTime value as returned by the API."""
    return {"scalar": scalar, "xmlrpc_type": "datetime", "timestamp": timestamp}


DOMAIN_ENTRY = {
    "roId": 1,
    "domain": "example.de",
    "domain-ace": "example.de",
    "period": "1Y",
    "status": "OK",
    "renewalMode": "AUTORENEW",
    "transferLock": True,
    "ns": ["ns.example.com", "ns2.example.com"],
    "crDate": date_time("2026-08-22T20:28:56+00:00", 1787430536),
    "upDate": date_time("2026-08-22T20:44:56+00:00", 1787431496),
    "reDate": date_time("2027-08-21T20:28:56+00:00", 1818880136),
    "exDate": date_time("2027-08-22T20:28:56+00:00", 1818966536),
}
DOMAIN = InwxDomain(
    ro_id=1,
    name="example.de",
    name_ace="example.de",
    status="OK",
    renewal_mode="AUTORENEW",
    period="1Y",
    renewal_date=datetime(2027, 8, 21, 20, 28, 56, tzinfo=UTC),
    expiration_date=datetime(2027, 8, 22, 20, 28, 56, tzinfo=UTC),
    transfer_lock=True,
    nameservers=("ns.example.com", "ns2.example.com"),
)
PRICE_ENTRY = {
    "premium": 0,
    "currency": "EUR",
    "period": "1Y",
    "promo": 0,
    "promoPrice": 0,
    "promoType": "",
    "regPrice": 5.02,
    "updatePrice": 0,
    "transferPrice": 3.91,
    "tradePrice": None,
    "renewalPrice": 3.91,
    "restorePrice": 23.91,
    "promoPriceWithVat": 0,
    "regPriceWithVat": 5.973799999999999,
    "updatePriceWithVat": 0,
    "transferPriceWithVat": 4.6529,
    "tradePriceWithVat": 0,
    "renewalPriceWithVat": 4.6529,
    "restorePriceWithVat": 28.4529,
}


def test_default_url() -> None:
    """Test the URL of the live API."""
    assert API_URL == "https://api.domrobot.com/jsonrpc/"


def test_normalize_shared_secret() -> None:
    """Test the normalization of the shared secret."""
    assert normalize_shared_secret("jbsw y3dp\tehpk 3pxp") == SHARED_SECRET


async def test_login_keeps_session_cookie(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test the login and the use of the session cookie."""
    fake_api.respond({"code": 1000, "resData": LOGIN}, cookies={"domrobot": "abc"})
    fake_api.ok(BALANCE)
    client = make_client()

    account = await client.login()
    balance = await client.get_balance()

    assert account == InwxAccount(customer_id=12345, customer_no=67890)
    assert balance == InwxBalance(available=14.03, locked=0.0, credit_limit=0.0)
    assert fake_api.requests[0].method == "account.login"
    assert fake_api.requests[0].params == {"user": "user", "pass": "pass"}
    assert fake_api.requests[0].cookies == {}
    assert fake_api.requests[1].method == "accounting.accountBalance"
    assert fake_api.requests[1].cookies == {"domrobot": "abc"}


async def test_logout_drops_session_cookie(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test that the logout drops the session cookie."""
    fake_api.respond({"code": 1000, "resData": LOGIN}, cookies={"domrobot": "abc"})
    fake_api.ok()
    fake_api.ok(BALANCE)
    client = make_client()

    await client.login()
    await client.logout()
    await client.get_balance()

    assert fake_api.requests[1].method == "account.logout"
    assert fake_api.requests[1].cookies == {"domrobot": "abc"}
    assert fake_api.requests[2].cookies == {}


async def test_login_with_two_factor(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test the login with 2FA."""
    fake_api.ok(LOGIN_TFA)
    fake_api.ok()
    client = make_client("jbsw y3dp ehpk 3pxp")

    with patch("aioinwx.client.time.time", return_value=1792929605.0):
        await client.login()

    assert fake_api.requests[1].method == "account.unlock"
    assert fake_api.requests[1].params == {
        "tan": pyotp.TOTP(SHARED_SECRET).at(datetime.fromtimestamp(1792929600, UTC))
    }


async def test_totp_code_is_not_reused(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test that a second login in the same time step waits for a new code."""
    for _ in range(2):
        fake_api.ok(LOGIN_TFA)
        fake_api.ok()
    client = make_client(SHARED_SECRET)
    now = [1792929605.0]

    async def fake_sleep(seconds: float) -> None:
        now[0] += seconds

    with (
        patch("aioinwx.client.time.time", side_effect=lambda: now[0]),
        patch("aioinwx.client.asyncio.sleep", AsyncMock(side_effect=fake_sleep)) as sleep,
    ):
        await client.login()
        await client.login()

    sleep.assert_awaited_once()
    first_tan = fake_api.requests[1].params["tan"]
    second_tan = fake_api.requests[3].params["tan"]
    assert first_tan != second_tan
    assert second_tan == pyotp.TOTP(SHARED_SECRET).at(datetime.fromtimestamp(1792929630, UTC))


async def test_two_factor_without_secret(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test the login of a 2FA account without shared secret."""
    fake_api.ok(LOGIN_TFA)

    with pytest.raises(InwxTwoFactorRequiredError):
        await make_client().login()


@pytest.mark.parametrize("code", [2200, 2400])
async def test_two_factor_rejected(
    fake_api: FakeApi, make_client: ClientFactory, code: int
) -> None:
    """Test a rejected 2FA code."""
    fake_api.ok(LOGIN_TFA)
    fake_api.respond({"code": code, "msg": "Rejected"})

    with pytest.raises(InwxTwoFactorError):
        await make_client(SHARED_SECRET).login()


async def test_invalid_shared_secret(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test a shared secret that is not Base32."""
    fake_api.ok(LOGIN_TFA)

    with pytest.raises(InwxInvalidSharedSecretError):
        await make_client("not-base32!").login()


async def test_invalid_credentials(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test rejected credentials."""
    fake_api.respond({"code": 2200, "msg": "Authentication error"})

    with pytest.raises(InwxAuthError):
        await make_client().login()


async def test_login_without_customer_id(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test a login response without customer ID."""
    fake_api.ok({"tfa": "0"})

    with pytest.raises(InwxApiError, match="Invalid login response"):
        await make_client().login()


async def test_api_error(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test an error code of the API."""
    fake_api.respond({"code": 2400, "msg": "Command failed"})

    with pytest.raises(InwxApiError, match="2400: Command failed") as err:
        await make_client().get_balance()

    assert err.value.code == 2400
    assert err.value.message == "Command failed"


@pytest.mark.parametrize(
    "response",
    [
        web.json_response(["unexpected"]),
        web.json_response({"msg": "no code"}),
        web.Response(text="no json"),
    ],
)
async def test_invalid_response(
    fake_api: FakeApi, make_client: ClientFactory, response: web.Response
) -> None:
    """Test responses that are no valid result."""
    fake_api.respond_raw(response)

    with pytest.raises(InwxApiError, match="Invalid response"):
        await make_client().get_balance()


async def test_http_error(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test an HTTP error status."""
    fake_api.respond_raw(web.Response(status=500))

    with pytest.raises(InwxConnectionError):
        await make_client().get_balance()


async def test_connection_error() -> None:
    """Test a failed request."""
    async with InwxClient("user", "pass", url="http://127.0.0.1:9/jsonrpc/") as client:
        with pytest.raises(InwxConnectionError):
            await client.get_balance()


async def test_invalid_balance(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test a balance response without available balance."""
    fake_api.ok({"total": 1})

    with pytest.raises(InwxApiError, match="Invalid account balance"):
        await make_client().get_balance()


async def test_get_domains(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test reading the domain list."""
    fake_api.ok({"count": 1, "domain": [DOMAIN_ENTRY]})

    assert await make_client().get_domains() == [DOMAIN]
    assert fake_api.requests[0].params == {"page": 1, "pagelimit": 100}


async def test_get_domains_with_pages(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test reading the domain list over several pages."""
    second = {
        "roId": 2,
        "domain": "müller.de",
        "domain-ace": "xn--mller-kva.de",
        "renewalMode": "AUTOEXPIRE",
    }
    fake_api.ok({"count": 2, "domain": [DOMAIN_ENTRY]})
    fake_api.ok({"count": 2, "domain": [second]})

    domains = await make_client().get_domains()

    assert [request.params["page"] for request in fake_api.requests] == [1, 2]
    assert domains[0] == DOMAIN
    assert domains[1].name == "müller.de"
    assert domains[1].name_ace == "xn--mller-kva.de"
    assert domains[1].renewal_date is None
    assert domains[1].expiration_date is None
    assert domains[1].transfer_lock is None
    assert domains[1].nameservers is None


async def test_get_domains_empty(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test an account without domains."""
    fake_api.ok({"count": 0})

    assert await make_client().get_domains() == []


@pytest.mark.parametrize(
    "res_data",
    [
        {"count": 1, "domain": {"roId": 1}},
        {"domain": [DOMAIN_ENTRY]},
        {"count": 1, "domain": [{**DOMAIN_ENTRY, "roId": None}]},
        {"count": 1, "domain": [{**DOMAIN_ENTRY, "reDate": "2027-08-21T20:28:56+00:00"}]},
        {"count": 1, "domain": [{**DOMAIN_ENTRY, "exDate": {"timestamp": 1818966536}}]},
        {"count": 1, "domain": [{**DOMAIN_ENTRY, "exDate": date_time("2027-08-22", 0)}]},
        {"count": 1, "domain": [{**DOMAIN_ENTRY, "exDate": date_time("invalid", 0)}]},
        {"count": 1, "domain": [{**DOMAIN_ENTRY, "transferLock": 1}]},
        {"count": 1, "domain": [{**DOMAIN_ENTRY, "ns": "ns.example.com"}]},
        {"count": 1, "domain": [{**DOMAIN_ENTRY, "ns": [None]}]},
    ],
)
async def test_get_domains_invalid(
    fake_api: FakeApi, make_client: ClientFactory, res_data: dict[str, Any]
) -> None:
    """Test domain lists that cannot be read."""
    fake_api.ok(res_data)

    with pytest.raises(InwxApiError, match="Invalid domain"):
        await make_client().get_domains()


async def test_get_renewal_price(fake_api: FakeApi, make_client: ClientFactory) -> None:
    """Test reading the renewal price of a domain."""
    fake_api.ok({"domain": {"example.de": PRICE_ENTRY}})

    price = await make_client().get_renewal_price(DOMAIN)

    assert price == InwxPrice(net=3.91, gross=4.6529, currency="EUR")
    assert fake_api.requests[0].method == "domain.getalldomainprices"
    assert fake_api.requests[0].params == {"domain": ["example.de"], "period": "1Y"}


async def test_get_renewal_price_without_period(
    fake_api: FakeApi, make_client: ClientFactory
) -> None:
    """Test reading the price of a domain without period."""
    fake_api.ok({"domain": {"xn--mller-kva.de": PRICE_ENTRY}})
    domain = InwxDomain(
        ro_id=2,
        name="müller.de",
        name_ace="xn--mller-kva.de",
        status=None,
        renewal_mode=None,
        period=None,
        renewal_date=None,
        expiration_date=None,
        transfer_lock=None,
        nameservers=None,
    )

    await make_client().get_renewal_price(domain)

    assert fake_api.requests[0].params == {"domain": ["xn--mller-kva.de"]}


@pytest.mark.parametrize(
    ("res_data", "match"),
    [
        ({}, "No price"),
        ({"domain": []}, "No price"),
        ({"domain": {"other.de": PRICE_ENTRY}}, "No price"),
        ({"domain": {"example.de": {"renewalPrice": 1}}}, "Invalid price"),
    ],
)
async def test_get_renewal_price_invalid(
    fake_api: FakeApi, make_client: ClientFactory, res_data: dict[str, Any], match: str
) -> None:
    """Test price responses without usable price."""
    fake_api.ok(res_data)

    with pytest.raises(InwxApiError, match=match):
        await make_client().get_renewal_price(DOMAIN)


async def test_passed_session_is_not_closed(fake_api: FakeApi) -> None:
    """Test that a passed session stays open."""
    fake_api.ok(BALANCE)
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        async with InwxClient("user", "pass", session=session, url=fake_api.url) as client:
            await client.get_balance()

        assert not session.closed


async def test_own_session_is_closed(fake_api: FakeApi) -> None:
    """Test that the session created by the client is closed."""
    fake_api.ok(BALANCE)
    async with InwxClient("user", "pass", url=fake_api.url) as client:
        await client.get_balance()
        session = client._session
        assert session is not None

    assert session.closed
