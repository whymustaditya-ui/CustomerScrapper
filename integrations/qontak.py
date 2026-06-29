"""Mekari Qontak push — gated behind config/.env credentials.

If credentials are absent, every push is a SAFE NO-OP that logs the exact payload
it *would* send. Excel/CSV export works fully without any of this. Drop real keys
into config/.env to activate. Push only after Bro approves a batch in the UI.

Qontak's API shape varies by product/version; the field mapping below targets the
CRM contacts endpoint. Endpoint paths are kept in one place so they're easy to
correct against current Qontak docs when Bro goes live.
"""

from __future__ import annotations

import json
import logging
import os

import requests
from dotenv import load_dotenv

logger = logging.getLogger("qontak")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [qontak] %(message)s")

_ENV_PATH = os.path.join("config", ".env")
_CONTACTS_ENDPOINT = "/api/v1/contacts"  # adjust to current Qontak CRM docs at go-live


def _load_env() -> dict:
    load_dotenv(_ENV_PATH)
    return {
        "client_id": os.getenv("QONTAK_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("QONTAK_CLIENT_SECRET", "").strip(),
        "username": os.getenv("QONTAK_USERNAME", "").strip(),
        "password": os.getenv("QONTAK_PASSWORD", "").strip(),
        "base_url": os.getenv("QONTAK_BASE_URL", "").strip(),
        "auth_url": os.getenv("QONTAK_AUTH_URL", "").strip(),
        "contact_list_id": os.getenv("QONTAK_CONTACT_LIST_ID", "").strip(),
    }


def is_configured() -> bool:
    """True only if the minimum credentials are present."""
    env = _load_env()
    return all([env["client_id"], env["client_secret"], env["base_url"], env["auth_url"]])


def map_contact(row: dict) -> dict:
    """Map an internal lead row to a Qontak contact payload."""
    return {
        "name": row.get("name", ""),
        "phone": row.get("phone_normalized") or row.get("phone", ""),
        "company": row.get("name", ""),
        "address": row.get("address", ""),
        "custom_fields": {
            "industry": row.get("industry", ""),
            "store_size": row.get("store_size", ""),
            "store_size_score": row.get("store_size_score", ""),
            "kelurahan": row.get("kelurahan", ""),
            "kota": row.get("kota", ""),
            "website": row.get("website_canonical", "") or row.get("website", ""),
            "source": "GMaps",
        },
    }


def _get_token(env: dict) -> str | None:
    try:
        resp = requests.post(
            env["auth_url"],
            data={
                "grant_type": "password",
                "client_id": env["client_id"],
                "client_secret": env["client_secret"],
                "username": env["username"],
                "password": env["password"],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as e:
        logger.error("Token request failed: %s", e)
        return None


def push_contacts(rows: list[dict]) -> dict:
    """Push a batch of leads to Qontak.

    Returns a result summary dict: {mode, sent, failed, logged, messages[]}.
    When unconfigured, mode='dry-run' and payloads are only logged.
    """
    env = _load_env()
    payloads = [map_contact(r) for r in rows]

    if not is_configured():
        for p in payloads:
            logger.info("DRY-RUN would push: %s", json.dumps(p, ensure_ascii=False))
        return {
            "mode": "dry-run",
            "sent": 0,
            "failed": 0,
            "logged": len(payloads),
            "messages": ["No Qontak credentials in config/.env — logged payloads only."],
        }

    token = _get_token(env)
    if not token:
        return {
            "mode": "live", "sent": 0, "failed": len(payloads), "logged": 0,
            "messages": ["Auth failed — check credentials in config/.env."],
        }

    url = env["base_url"].rstrip("/") + _CONTACTS_ENDPOINT
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    sent, failed, messages = 0, 0, []
    for p in payloads:
        body = dict(p)
        if env["contact_list_id"]:
            body["contact_list_id"] = env["contact_list_id"]
        try:
            r = requests.post(url, headers=headers, json=body, timeout=30)
            if r.status_code in (200, 201):
                sent += 1
            else:
                failed += 1
                messages.append(f"{p['name']}: HTTP {r.status_code} {r.text[:160]}")
        except Exception as e:
            failed += 1
            messages.append(f"{p['name']}: {e}")

    return {"mode": "live", "sent": sent, "failed": failed, "logged": 0, "messages": messages}
