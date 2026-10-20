"""Master data loader — customers, ports, and domain map."""

import json
from functools import lru_cache
from pathlib import Path

from ugr_booking.config import DATA_PATH
from ugr_booking.normalize import fold

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> list | dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Customer lookup
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _customers() -> list[dict]:
    return _load_json(DATA_PATH / "master_data" / "customers.json")


@lru_cache(maxsize=1)
def _customer_index() -> dict[str, str]:
    """Map folded name/alias → customer_id."""
    index: dict[str, str] = {}
    for c in _customers():
        index[fold(c["name"])] = c["customer_id"]
        for alias in c.get("aliases", []):
            index[fold(alias)] = c["customer_id"]
    return index


def resolve_customer(raw: str | None) -> str | None:
    """Return customer_id for *raw* name/alias, or None if not found.

    No fuzzy matching — exact fold-match only.
    """
    if not raw:
        return None
    return _customer_index().get(fold(raw))


def get_customer_by_id(customer_id: str) -> dict | None:
    for c in _customers():
        if c["customer_id"] == customer_id:
            return c
    return None


# ---------------------------------------------------------------------------
# Port lookup
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _ports() -> list[dict]:
    return _load_json(DATA_PATH / "master_data" / "ports.json")


@lru_cache(maxsize=1)
def _port_index() -> dict[str, str]:
    """Map folded code/name/alias → port_code."""
    index: dict[str, str] = {}
    for p in _ports():
        code = p["port_code"]
        index[fold(code)] = code
        index[fold(p["name"])] = code
        for alias in p.get("aliases", []):
            index[fold(alias)] = code
    return index


def resolve_port(raw: str | None) -> str | None:
    """Return LOCODE for *raw* port string, or None if not found."""
    if not raw:
        return None
    return _port_index().get(fold(raw))


def get_port_by_code(port_code: str) -> dict | None:
    for p in _ports():
        if p["port_code"] == port_code:
            return p
    return None


# ---------------------------------------------------------------------------
# Domain map  (sender domain → suggested customer_id)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _domain_map() -> dict[str, str]:
    path = Path(__file__).parent.parent.parent / "config" / "domain_map.json"
    if not path.exists():
        return {}
    return _load_json(path)


def suggest_customer_from_domain(sender: str | None) -> str | None:
    """Return a *suggested* customer_id based on sender domain only.

    This is a hint, not a resolution — domain alone never sets customer_id.
    """
    if not sender:
        return None
    if "@" in sender:
        domain = sender.split("@")[-1].strip().lower().rstrip(">")
    else:
        return None
    return _domain_map().get(domain)
