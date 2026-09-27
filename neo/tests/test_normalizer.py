from app.models import RawLead
from app.normalizer import Normalizer


class FakeRepo:
    def __init__(self):
        self.seen = set()
        self.urls = set()
        self.saved = []

    def exists(self, key: str) -> bool:
        return key in self.seen

    def url_exists(self, url: str) -> bool:
        return url in self.urls

    def save(self, key: str, lead: RawLead) -> None:
        self.seen.add(key)
        self.urls.add(lead.source_url)
        self.saved.append(lead)


def _lead(url: str, source: str = "marja") -> RawLead:
    return RawLead(source=source, source_url=url, title="T", raw_text="x")


def test_same_employer_from_two_sources_is_one_lead():
    repo = FakeRepo()
    normalizer = Normalizer(repo)

    first = normalizer.process([_lead("https://vumes.az", source="linkedin")])
    second = normalizer.process([_lead("https://vumes.az", source="boss")])

    assert len(first) == 1
    assert second == []


def test_normalizer_drops_duplicates_within_batch():
    repo = FakeRepo()
    normalizer = Normalizer(repo)

    leads = [_lead("https://marja.az/1"), _lead("https://marja.az/1"), _lead("https://marja.az/2")]
    fresh = normalizer.process(leads)

    assert len(fresh) == 2
    assert {lead.source_url for lead in fresh} == {"https://marja.az/1", "https://marja.az/2"}


def test_normalizer_drops_leads_already_in_repo():
    repo = FakeRepo()
    repo.seen.add(_lead("https://marja.az/1").dedupe_key())
    normalizer = Normalizer(repo)

    fresh = normalizer.process([_lead("https://marja.az/1")])

    assert fresh == []
