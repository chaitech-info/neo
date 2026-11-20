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
# Vercel funksiyası 60 s-də kəsilir (vercel.json maxDuration) — agent ondan əvvəl dayandırılır ki,
# istifadəçi ümumi "Xəta" yox, aydın "gecikdi" mesajı görsün.
TIMEOUT_SECONDS = 50.0

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


def with_package(body: dict, package_id: Any, package_name: Any = None) -> dict:
    """İstifadəçinin seçdiyi paket: agentə təklifi YALNIZ bu paketlə qurmaq göstərişi (müştəri sorğusunun
    sonuna). Agent API-də ayrıca "paket" sahəsi yoxdur — göstəriş mətnlə gedir. Boş seçim → dəyişmir."""
    if package_id in (None, ""):
        return body
    package_id = validate_package_id(package_id if isinstance(package_id, str) else "")
    name = package_name.strip()[:120] if isinstance(package_name, str) and package_name.strip() else package_id
    note = (f"\n\n[Seçilmiş paket: «{name}» (id: {package_id}). Təklifi YALNIZ bu paketlə hazırla, "
            f"başqa paket əlavə etmə və ya əvəz etmə.]")
    room = MAX_MESSAGE_LENGTH - len(note)  # agentin mesaj limiti aşılmasın
    if "messages" in body:
        body["messages"][0] = {**body["messages"][0], "content": body["messages"][0]["content"][:room] + note}
    else:
        body["message"] = body["message"][:room] + note
    return body


NO_NAME_NOTE = ("\n\n[Təklifi hazırlayan işçinin və ya əlaqədar şəxsin adını soruşma — ad lazım deyil. "
                "Ad verilməyibsə adsız davam et. Müştəri şirkətinin adı sorğunun mətnində yazılıbsa "
                "(məs. «STP üçün …» — şirkət STP-dir) onu götür; yazılmayıbsa soruşma, şirkət adı olmadan davam et.]")
# Agentin "şirkətin adı nədir?" tipli sualı — ad sorğuda yazılıbsa Neo onu özü cavablayır (offer_routes)
COMPANY_QUESTION_RE = re.compile(
    r"[şs]irk[əe]t\w*\s+ad|m[üu][şs]t[əe]ri\w*\s+(?:ad|kim)|hans[ıi]\s+[şs]irk[əe]t|kim\s+[üu][çc][üu]n|"
    r"company(?:'s)?\s+name|which\s+company|name\s+of\s+the\s+(?:company|client)|назван\w+\s+компан|какая\s+компан", re.I)
COMPANY_SKIP_ANSWER = "Şirkət adı lazım deyil — şirkət adı olmadan davam et və təklifi hazırla."
# Agentin "adınız nədir?" tipli sualı (az / en / ru) — belə suala Neo özü cavab verir (offer_routes)
NAME_QUESTION_RE = re.compile(
    r"\bad[ıi]n[ıi]z\b|\bad[ıi]n[ıi]z[ıi]\b|ad[ıi]n\s+n[əe]dir|kimin\s+ad[ıi]ndan|[əe]laq[əe]dar\s+[şs][əe]xs\w*\s+(?:kim|ad)|"
    r"your\s+name|contact\s+person'?s?\s+name|ваше\s+имя|как\s+вас\s+зовут", re.I)
NAME_ANSWER = "Ad lazım deyil — adsız davam et və təklifi hazırla."


def without_name_question(body: dict) -> dict:
    """Agentə göstəriş (sorğunun sonuna): təklif üçün işçinin / əlaqədar şəxsin adını soruşmasın."""
    room = MAX_MESSAGE_LENGTH - len(NO_NAME_NOTE)
    if "messages" in body:
        first = body["messages"][0]
        if NO_NAME_NOTE not in first["content"]:
            body["messages"][0] = {**first, "content": first["content"][:room] + NO_NAME_NOTE}
    elif NO_NAME_NOTE not in body["message"]:
        body["message"] = body["message"][:room] + NO_NAME_NOTE
    return body


def auto_answer(body: dict, question: str, customer: str = "") -> dict | None:
    """Agent göstərişə baxmayaraq işçinin adını və ya müştəri şirkətinin adını soruşubsa — söhbətə Neo-nun
    cavabı əlavə olunmuş yeni sorğu: şirkət adı sorğu mətnindən tapılıbsa (`customer`) o, yoxsa "lazım deyil".
    Başqa sualdırsa (və ya söhbət artıq uzundursa) None — sual istifadəçiyə göstərilir."""
    question = question or ""
    extra = {}
    if COMPANY_QUESTION_RE.search(question):     # "şirkətin adı" ad sualından əvvəl yoxlanılır ("adı nədir")
        answer = f"Müştəri şirkəti: {customer}." if customer else COMPANY_SKIP_ANSWER
        if customer and not body.get("customer_name"):
            extra["customer_name"] = customer[:200]
    elif NAME_QUESTION_RE.search(question):
        answer = NAME_ANSWER
    else:
        return None
    messages = list(body.get("messages") or [{"role": "user", "content": body["message"]}])
    if len(messages) + 2 > MAX_CONVERSATION_TURNS:
        return None
    messages += [{"role": "assistant", "content": question}, {"role": "user", "content": answer}]
    return {**{k: v for k, v in body.items() if k != "message"}, **extra, "messages": messages}


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
