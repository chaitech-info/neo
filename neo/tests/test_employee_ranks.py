"""İşçilərin vəzifə səviyyəsi (app/employees.py::seniority_rank) və qruplu siyahı (JSON və səhifə eyni sırada)."""
import json

import pytest
from fastapi.testclient import TestClient

from app import employees, leaders, web
from app.db.models import EmployeeSearch
from tests.test_company_db import _apify, factory, fake_ai  # noqa: F401 — pytest fixture-ləri


@pytest.mark.parametrize("title, rank", [
    # 1 — top rəhbərlik
    ("CEO", 1), ("Co-Founder & CEO", 1), ("Owner", 1), ("Managing Director", 1), ("General Manager", 1),
    ("Baş direktor", 1), ("Təsisçi", 1), ("İdarə Heyətinin Sədri", 1), ("Генеральный директор", 1), ("Основатель", 1),
    # 2 — C-level / VP
    ("CFO", 2), ("Chief Marketing Officer", 2), ("VP of Sales", 2), ("Vice President", 2),
    ("Direktor müavini", 2), ("Заместитель", 2), ("Заместитель генерального директора", 2), ("Deputy Director", 2),
    # 3 — direktorlar / departament rəhbərləri
    ("Sales Director", 3), ("Head of HR", 3), ("Maliyyə direktoru", 3), ("Şöbə müdiri", 3), ("Rəhbər", 3),
    ("Директор по продажам", 3), ("Руководитель отдела", 3), ("Начальник склада", 3), ("DİREKTOR", 3),
    # 4 — menecerlər
    ("Office Manager", 4), ("Team Lead", 4), ("Supervisor", 4), ("Menecer", 4), ("Менеджер по закупкам", 4),
    ("Deputy Head of Sales", 4),
    # 5 — mütəxəssislər
    ("Software Engineer", 5), ("Mühasib", 5), ("HR Specialist", 5), ("Аналитик", 5),
    # 6 — junior / köməkçi
    ("Intern", 6), ("Assistant to CEO", 6), ("Executive Assistant", 6), ("Junior Developer", 6),
    ("Təcrübəçi", 6), ("Стажёр", 6),
    # 7 — vəzifə yoxdur
    ("", 7), ("   ", 7), (None, 7),
])
def test_seniority_rank(title, rank):
    assert employees.seniority_rank(title) == rank


def test_deputy_drops_one_level():
    assert employees.seniority_rank("Sədr müavini") == employees.seniority_rank("Sədr") + 1
    assert employees.seniority_rank("Deputy CEO") == 2


def test_contact_fit_marks_buyers_not_assistants():
    assert employees.contact_fit("HR Manager") and employees.contact_fit("Satınalma üzrə mütəxəssis")
    assert employees.contact_fit("Office Manager") and employees.contact_fit("Менеджер по закупкам")
    assert not employees.contact_fit("HR Assistant") and not employees.contact_fit("Software Engineer")
    assert not employees.contact_fit(None)


def test_ranked_sorts_by_level_then_name():
    items = [{"name": "Zaur", "title": "Engineer"}, {"name": "Aysel", "title": "Engineer"},
             {"name": "Rauf", "title": "CEO"}, {"name": "Nərmin", "title": None}, {"name": "Elvin Məmmədov", "title": "CFO"}]
    out = employees.ranked(items)
    assert [p["name"] for p in out] == ["Rauf", "Elvin Məmmədov", "Aysel", "Zaur", "Nərmin"]
    assert out[1]["initials"] == "EM" and out[0]["rank"] == 1


def test_view_groups_and_collapses_long_lower_groups():
    items = [{"name": f"Mütəxəssis {i:02}", "title": "Engineer"} for i in range(13)] + [{"name": "Rauf", "title": "CEO"}]
    v = employees.view(items)
    assert [g["label"] for g in v["groups"]] == ["Rəhbərlik", "Mütəxəssislər"]  # boş qruplar yoxdur
    specialists = v["groups"][1]
    assert len(specialists["shown"]) == employees.COLLAPSE_AFTER and len(specialists["more"]) == 3
    assert v["summary"] == "Cəmi 14 · 1 rəhbər · 13 mütəxəssis"


def test_employees_json_is_sorted_and_carries_same_html(factory, monkeypatch):  # noqa: F811
    monkeypatch.setenv("APIFY_TOKEN", "t")
    items = [
        {"fullName": "Zaur Ə.", "headline": "Intern", "linkedinUrl": "https://www.linkedin.com/in/zaur"},
        {"fullName": "Aysel Q.", "headline": "HR Manager", "linkedinUrl": "https://www.linkedin.com/in/aysel"},
        {"fullName": "Rauf Əliyev", "headline": "Baş direktor", "linkedinUrl": "https://www.linkedin.com/in/rauf"},
    ]
    monkeypatch.setattr(leaders, "_http", _apify(items, []))
    client = TestClient(web.app)
    run = client.post("/tools/company/employees", data={"company": "Sahil MMC"}).json()["run"]
    data = client.post("/tools/company/employees/poll", data={"company": "Sahil MMC", "run": run}).json()
    assert [p["name"] for p in data["items"]] == ["Rauf Əliyev", "Aysel Q.", "Zaur Ə."]
    assert [p["rank"] for p in data["items"]] == [1, 4, 6] and data["items"][1]["fit"] is True
    html = data["html"]
    assert html.index("Rəhbərlik") < html.index("Menecerlər") < html.index("Junior")
    assert "əlaqə üçün uyğun" in html and "Cəmi 3" in html

    cached = client.post("/tools/company/employees", data={"company": "Sahil MMC"}).json()
    assert cached["from_cache"] is True and cached["html"] == html


def test_analysis_page_renders_groups_from_cache(factory, fake_ai):  # noqa: F811
    with factory() as s:
        s.add(EmployeeSearch(company="Sahil MMC", company_key=employees.company_key("Sahil MMC"), items=json.dumps([
            {"name": "Aysel Q.", "title": "HR Manager", "linkedin_url": "https://www.linkedin.com/in/aysel"},
            {"name": "Rauf Əliyev", "title": "CEO", "linkedin_url": "https://www.linkedin.com/in/rauf"}])))
        s.commit()
    page = TestClient(web.app).post("/tools/company", data={"name": "Sahil MMC"}).text
    assert page.index("Rauf Əliyev") < page.index("Aysel Q.")
    assert 'class="emp-gh"' in page and "Rəhbərlik" in page and "əlaqə üçün uyğun" in page
