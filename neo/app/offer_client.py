"""Qiymət təklifi agentinə proxy client (Alətlər → Qiymət təklifi).

Agent açarları (OFFER_AGENT_KEY, OFFER_AGENT_ADMIN_KEY) yalnız burada, serverdə oxunur;
brauzer heç vaxt agentə birbaşa getmir. Agentin xam xəta mətni istifadəçiyə çatmır —
hər status kodu Azərbaycan dilində qısa mesaja çevrilir (`OfferAgentError.message`).
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

DEFAULT_URL = "https://askarhuseynli96--offer-agent-api.us-east.modal.run"
BRANDS = ("seaport", "nafa")
BRAND_NAMES = {"seaport": "Seaport", "nafa": "NAFA"}
MAX_MESSAGE_LENGTH = 4000
MAX_CONVERSATION_TURNS = 20
TIMEOUT_SECONDS = 60.0

OFFER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")


class OfferAgentError(Exception):
    """İstifadəçiyə göstərilə bilən xəta: `message` Azərbaycan dilindədir, `status` brauzerə qayıdan HTTP kodu."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


_STATUS_MESSAGES = {
    401: ("Agent açarı qəbul edilmədi. Administrator açarı yoxlamalıdır.", 502),
    403: ("Agent bu əməliyyata icazə vermədi. Administrator açarı yoxlamalıdır.", 502),
    404: ("Belə brend və ya təklif tapılmadı.", 404),
    409: ("Bu brend üçün paket yüklənməyib. Alətlər → Paketlər bölməsindən paketləri yükləyin.", 409),
    422: ("Sorğu yanlışdır. Mətni və seçimləri yoxlayıb yenidən cəhd edin.", 422),
    502: ("Agent cavab verə bilmədi. Bir az sonra yenidən cəhd edin.", 502),
}


def error_for_status(status: int) -> OfferAgentError:
    message, out = _STATUS_MESSAGES.get(status, ("Agent gözlənilməz cavab verdi. Yenidən cəhd edin.", 502))
    return OfferAgentError(message, out)


def _base_url() -> str:
    return (os.environ.get("OFFER_AGENT_URL") or DEFAULT_URL).rstrip("/")


def _key(admin: bool) -> str:
    name = "OFFER_AGENT_ADMIN_KEY" if admin else "OFFER_AGENT_KEY"
    key = (os.environ.get(name) or "").strip()
    if not key:
        raise OfferAgentError(f"Qiymət təklifi agenti qurulmayıb ({name} təyin edilməyib).", 503)
    return key


def _request(method: str, path: str, *, admin: bool = False, json: Any = None, params: dict | None = None,
             client: httpx.Client | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {_key(admin)}"}
    url = _base_url() + path
    try:
        if client is not None:
            resp = client.request(method, url, headers=headers, json=json, params=params, timeout=TIMEOUT_SECONDS)
        else:
            with httpx.Client() as c:
                resp = c.request(method, url, headers=headers, json=json, params=params, timeout=TIMEOUT_SECONDS)
    except httpx.TimeoutException:
        log.warning("offer_agent_timeout", path=path)
        raise OfferAgentError("Agent cavabı gecikdi. Yenidən cəhd edin.", 504)
    except httpx.HTTPError as exc:
        log.warning("offer_agent_unreachable", path=path, error=type(exc).__name__)
        raise OfferAgentError("Agentə qoşulmaq alınmadı. Bir az sonra yenidən cəhd edin.", 502)
    if resp.status_code >= 400:
        log.warning("offer_agent_error", path=path, status=resp.status_code)
        raise error_for_status(resp.status_code)
    return resp


def _json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise OfferAgentError("Agent gözlənilməz cavab verdi. Yenidən cəhd edin.", 502)
    if not isinstance(data, dict):
        raise OfferAgentError("Agent gözlənilməz cavab verdi. Yenidən cəhd edin.", 502)
    return data


# --- Giriş yoxlaması ---------------------------------------------------------


def _text(value: Any, field: str, *, required: bool = False, max_len: int = MAX_MESSAGE_LENGTH) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"«{field}» mətn olmalıdır")
    value = value.strip()
    if required and not value:
        raise ValueError("Müştəri sorğusu boş ola bilməz")
    if len(value) > max_len:
        raise ValueError(f"«{field}» çox uzundur (maks. {max_len} simvol)")
    return value


def validate_offer_request(data: Any) -> dict:
    """Brauzerdən gələn gövdəni yoxlayıb agentə göndəriləcək gövdəyə çevirir; səhvdə ValueError (Azərbaycan dilində)."""
    if not isinstance(data, dict):
        raise ValueError("Sorğu yanlışdır")
    brand = data.get("brand")
    if brand not in BRANDS:
        raise ValueError("Brend yalnız Seaport və ya NAFA ola bilər")

    body: dict[str, Any] = {"brand": brand, "include_pdf": True}
    messages = data.get("messages")
    if messages:
        if not isinstance(messages, list) or len(messages) > MAX_CONVERSATION_TURNS:
            raise ValueError("Söhbət tarixçəsi yanlışdır")
        clean = []
        for item in messages:
            if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
                raise ValueError("Söhbət tarixçəsi yanlışdır")
            clean.append({"role": item["role"], "content": _text(item.get("content"), "mesaj", required=True)})
        if clean[0]["role"] != "user" or clean[-1]["role"] != "user":
            raise ValueError("Söhbət istifadəçi mesajı ilə başlayıb bitməlidir")
        body["messages"] = clean
    else:
        body["message"] = _text(data.get("message"), "Müştəri sorğusu", required=True)

    for field, label in (("customer_name", "Müştəri şirkəti"), ("contact_person", "Əlaqədar şəxs")):
        value = _text(data.get(field), label, max_len=200)
        if value:
            body[field] = value
    return body


def validate_package_id(package_id: str) -> str:
    if not PACKAGE_ID_RE.match(package_id or ""):
        raise ValueError("Paket id-si yanlışdır (kiçik latın hərfləri, rəqəm, - və _)")
    return package_id


def validate_brand(brand: str) -> str:
    if brand not in BRANDS:
        raise ValueError("Brend yalnız Seaport və ya NAFA ola bilər")
    return brand


# --- Agent çağırışları ---------------------------------------------------------


def create_offer(body: dict, client: httpx.Client | None = None) -> dict:
    data = _json(_request("POST", "/v1/offers", json=body, client=client))
    if data.get("status") == "needs_clarification":
        question = data.get("question")
        if not isinstance(question, str) or not question.strip():
            raise OfferAgentError("Agent gözlənilməz cavab verdi. Yenidən cəhd edin.", 502)
        return {"status": "needs_clarification", "question": question.strip()}
    if data.get("status") != "ok" or not data.get("offer_id"):
        raise OfferAgentError("Agent gözlənilməz cavab verdi. Yenidən cəhd edin.", 502)
    return data


def get_offer(offer_id: str, client: httpx.Client | None = None) -> dict:
    return _json(_request("GET", f"/v1/offers/{offer_id}", client=client))


def get_offer_pdf(offer_id: str, client: httpx.Client | None = None) -> bytes:
    return _request("GET", f"/v1/offers/{offer_id}/pdf", client=client).content


def list_packages(brand: str, client: httpx.Client | None = None) -> Any:
    return _json_any(_request("GET", f"/v1/packages/{brand}", params={"include_inactive": "true"}, client=client))


def replace_packages(brand: str, packages: list, client: httpx.Client | None = None) -> Any:
    return _json_any(_request("PUT", f"/v1/packages/{brand}", admin=True, json={"packages": packages}, client=client))


def update_package(brand: str, package_id: str, package: dict, client: httpx.Client | None = None) -> Any:
    return _json_any(_request("PUT", f"/v1/packages/{brand}/{package_id}", admin=True, json=package, client=client))


def delete_package(brand: str, package_id: str, client: httpx.Client | None = None) -> None:
    _request("DELETE", f"/v1/packages/{brand}/{package_id}", admin=True, client=client)


def _json_any(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        raise OfferAgentError("Agent gözlənilməz cavab verdi. Yenidən cəhd edin.", 502)
