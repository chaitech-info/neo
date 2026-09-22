from app.models import RawLead
from app.normalizer import Normalizer


class FakeRepo:
    def __init__(self):
        self.seen = set()
        self.saved = []

    def exists(self, key: str) -> bool:
        return key in self.seen

    def save(self, key: str, lead: RawLead) -> None:
        self.seen.add(key)
        self.saved.append(lead)


def _lead(url: str) -> RawLead:
    return RawLead(source="marja", source_url=url, title="T", raw_text="x")


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
