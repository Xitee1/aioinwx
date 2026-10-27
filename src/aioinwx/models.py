"""Data models of the INWX client."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self


def parse_datetime(value: Any) -> datetime | None:
    """Parse a dateTime value of the JSON-RPC API.

    The API returns dateTime values as object, for example
    ``{"scalar": "2027-08-21T20:28:56+00:00", "xmlrpc_type": "datetime",
    "timestamp": 1818880136}``. Missing values are ``None``.
    """
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("scalar"), str):
        raise ValueError(f"Invalid dateTime value: {value!r}")
    parsed = datetime.fromisoformat(value["scalar"])
    if parsed.tzinfo is None:
        raise ValueError(f"dateTime value without offset: {value!r}")
    return parsed


def _parse_bool(value: Any) -> bool | None:
    """Parse an optional boolean value."""
    if value is None or isinstance(value, bool):
        return value
    raise ValueError(f"Invalid boolean value: {value!r}")


def _parse_nameservers(value: Any) -> tuple[str, ...] | None:
    """Parse the optional list of nameservers."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Invalid nameserver list: {value!r}")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class InwxAccount:
    """Account returned by the login."""

    customer_id: int
    customer_no: int | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Self:
        """Create the account from the result of account.login."""
        customer_no = data.get("customerNo")
        return cls(
            customer_id=int(data["customerId"]),
            customer_no=int(customer_no) if customer_no is not None else None,
        )


@dataclass(frozen=True, slots=True)
class InwxBalance:
    """Account balance."""

    available: float
    locked: float
    credit_limit: float

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Self:
        """Create the balance from the result of accounting.accountBalance."""
        return cls(
            available=float(data["available"]),
            locked=float(data["locked"]),
            credit_limit=float(data["creditLimit"]),
        )


@dataclass(frozen=True, slots=True)
class InwxDomain:
    """Domain of the account."""

    ro_id: int
    name: str
    name_ace: str
    status: str | None
    renewal_mode: str | None
    period: str | None
    renewal_date: datetime | None
    expiration_date: datetime | None
    transfer_lock: bool | None
    nameservers: tuple[str, ...] | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Self:
        """Create the domain from an entry of domain.list."""
        return cls(
            ro_id=int(data["roId"]),
            name=str(data["domain"]),
            name_ace=str(data["domain-ace"]),
            status=data.get("status"),
            renewal_mode=data.get("renewalMode"),
            period=data.get("period"),
            renewal_date=parse_datetime(data.get("reDate")),
            expiration_date=parse_datetime(data.get("exDate")),
            transfer_lock=_parse_bool(data.get("transferLock")),
            nameservers=_parse_nameservers(data.get("ns")),
        )


@dataclass(frozen=True, slots=True)
class InwxPrice:
    """Renewal price of a domain."""

    net: float
    gross: float
    currency: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Self:
        """Create the price from an entry of domain.getalldomainprices."""
        return cls(
            net=float(data["renewalPrice"]),
            gross=float(data["renewalPriceWithVat"]),
            currency=str(data["currency"]),
        )
