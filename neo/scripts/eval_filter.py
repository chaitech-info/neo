"""Filtr modelinin qiymətləndirməsi (Phase 1) — hər dəyişiklik rəqəmlə ölçülsün.

İki səviyyə:
- **triage** — başlıq süzgəci (app/ai_triage.py): hansı başlıqlar tam oxunmağa
  getsin. Məqsəd recall: real fürsəti buraxmamaq.
- **score** — tam mətn qiymətləndirməsi (app/ai_filter.py): pending_review və ya
  rədd. Məqsəd həm precision, həm recall.

Addımlar (data `eval_data/`-dadır, git-ə düşmür — lead mətnlərində əlaqə məlumatı var):

    python -m scripts.eval_filter collect   # bazadakı lead-lər + saytlardakı BÜTÜN başlıqlar
    python -m scripts.eval_filter label     # güclü model ilə "qızıl" etiket (keşlənir)
    python -m scripts.eval_filter export    # satış komandası üçün CSV (etiketləri yoxlasınlar)
    python -m scripts.eval_filter run --scorer v1 --model gpt-4o-mini   # ölç

Satış komandasının düzəlişləri `eval_data/human_labels.csv`-dədirsə, onlar
modelin etiketini əvəz edir (insan > model).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATA = Path(__file__).resolve().parent.parent / "eval_data"
ITEMS = DATA / "items.jsonl"
GOLD = DATA / "gold.jsonl"
HUMAN = DATA / "human_labels.csv"
RESULTS = DATA / "results"

# select_relevant işlədən scraper-lər — başlıqlar süzgəcdən ƏVVƏL tutulur.
POOL_SOURCES = ["marja", "1news", "oxu", "fed", "fed_tenders", "report", "etender", "emlak",
                "corporate", "worldbank", "bp_edp"]


def item_id(source: str, text: str) -> str:
    return hashlib.sha1(f"{source}|{text}".encode()).hexdigest()[:16]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- collect -----------------------------------------------------------------

def _lead_items() -> list[dict]:
    """Bazadakı bütün lead-lər — tam mətn (score səviyyəsi) və başlıq (triage səviyyəsi)."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.db.engine import engine
    from app.db.models import LeadRecord

    with Session(engine) as session:
        records = session.scalars(select(LeadRecord).where(LeadRecord.status != "duplicate")).all()
        return [{
            "id": item_id(r.source, r.source_url),
            "origin": "lead",
            "source": r.source,
            "kind": "tender" if r.category == "tender" else "news",
            "title": r.title,
            "text": r.raw_text,
            "url": r.source_url,
            "prod_status": r.status,          # istehsalda nə oldu (v1 + insan)
            "prod_score": r.ai_score,
            "prod_reason": r.ai_reason,
        } for r in records]


def _pool_items() -> list[dict]:
    """Saytlarda indi olan BÜTÜN başlıqlar — select_relevant tutulur, heç biri seçilmir
    (məqalə yüklənmir, AI çağırılmır). Süzgəcin buraxdıqlarını görmək üçün yeganə yol."""
    from app.scheduler import SOURCE_TASKS
    from app.scrapers.base import BaseScraper

    captured: list[dict] = []

    def capture(self, texts, fallback, kind, extra=()):
        for text in texts:
            captured.append({"source": self.source_name, "kind": kind, "title": text})
        return [False] * len(texts)

    original = BaseScraper.select_relevant
    BaseScraper.select_relevant = capture
    try:
        for key in POOL_SOURCES:
            before = len(captured)
            try:
                SOURCE_TASKS[key]().fetch_leads()
            except Exception as exc:  # bir mənbə digərlərini dayandırmasın
                print(f"  {key}: XƏTA {type(exc).__name__}: {str(exc)[:100]}")
                continue
            print(f"  {key}: {len(captured) - before} başlıq")
    finally:
        BaseScraper.select_relevant = original
    return [{"id": item_id(c["source"], c["title"]), "origin": "pool", "source": c["source"],
             "kind": c["kind"], "title": c["title"], "text": None} for c in captured]


def collect() -> None:
    old = {i["id"]: i for i in read_jsonl(ITEMS)}
    leads = _lead_items()
    print(f"bazadan {len(leads)} lead")
    pool = _pool_items()
    # Toplama bir neçə dəfə işlədilə bilər — köhnə başlıqlar qalır, data böyüyür.
    merged = dict(old)
    for item in pool + leads:          # lead (tam mətnli) eyni id-li pool-u əvəz edir
        merged[item["id"]] = item
    # Triaj ölçüsü yalnız saytlardakı İNDİKİ başlıqlar üzərindədir (qərəzsiz kəsik): bazadakı
    # lead-lər onsuz da süzgəcdən keçənlərdir. Siyahıda olan, artıq lead olan başlıq
    # `on_listing` ilə işarələnir (dublikat element yaranmır).
    listed = {(p["source"], p["title"]) for p in pool}
    lead_titles = {(i["source"], i["title"]): i for i in merged.values() if i["origin"] == "lead"}
    for key, lead in lead_titles.items():
        lead["on_listing"] = lead.get("on_listing") or key in listed
    rows = [i for i in merged.values() if i["origin"] == "lead" or (i["source"], i["title"]) not in lead_titles]
    _write_jsonl(ITEMS, rows)
    n_pool = sum(1 for r in rows if r["origin"] == "pool")
    print(f"cəmi {len(rows)} element: {len(rows) - n_pool} lead (tam mətn), {n_pool} yalnız başlıq → {ITEMS}")


# --- label -------------------------------------------------------------------

GOLD_MODEL = "gpt-5.2"
GOLD_VERSION = "g1"   # rubrika dəyişəndə artır — köhnə etiketlər yenidən soruşulur

GOLD_RUBRIC = """\
You label sales leads for an Azerbaijani B2B catering company. Main product: daily
ready meals for companies' employees (packaged/portioned lunch, bulk delivery) to
offices, factories, plants, construction sites, warehouses, camps, mines, state
bodies. Secondary: catering for corporate/state events (banquet, buffet, coffee
break, conference, festival, ceremony).

Decide if the sales team would want this item in their review queue.

RELEVANT (true) — an identifiable organization in AZERBAIJAN that is or will be a
food buyer:
- catering_tender: tender/request for catering, meals, food service, canteen
  operation, feeding of a school/hospital/army unit/camp/dormitory.
- food_supply_tender: purchase of food products/groceries by an institution.
- event_tender: tender to ORGANIZE an event (holiday, festival, forum, conference,
  competition, ceremony, team building, training with participants) — organizers
  almost always buy food. Gifts/printing/stage-only purchases are NOT this.
- employer_meal_signal: a job ad / company page says the employer provides lunch
  to staff (text starts with "[SİQNAL: ...]" or says "nahar şirkət tərəfindən").
  NOT relevant if the employer itself cooks food as its business (restaurant,
  cafe, hotel, catering company, food delivery) — they feed staff in-house.
- new_facility: a NAMED company opens/builds/starts production at a factory,
  plant, mine, industrial park facility, logistics hub, large office or branch
  network IN AZERBAIJAN (has or will have a workforce).
- expansion_hiring: a named large employer in Azerbaijan expands, creates many
  jobs, or hires roles implying on-site staff feeding (canteen/camp/facility
  manager, chef, HSE/site manager at a site).
NOT RELEVANT (false): restaurants/cafes/hotels opening or advertising; a company
advertising its OWN catering/food services (competitor); anything outside
Azerbaijan; construction/repair/road/utility WORKS tenders; equipment, furniture,
IT, vehicles, medicine purchases; politics, sports, crime, weather, prices,
statistics without a named facility; reports about PAST events; ordinary single
vacancies without a meal signal; generic investment talk with no named site.

Tier (only if relevant):
- A: direct food demand now (catering_tender, food_supply_tender, organization
  explicitly seeking a caterer, event tender that mentions food/catering).
- B: strong indirect demand (event_tender without food mention; employer_meal_signal
  for production/construction/logistics/multi-site or 2+ vacancies; new_facility
  with a named company; large-employer expansion).
- C: weak/early (small office employer_meal_signal, vague expansion, early-stage
  project news).

If only a TITLE is given (no body), answer whether the item is plausibly relevant
enough that it must be opened and read (recall matters: when a title could hide a
relevant item, say true).
Use only what is written; do not invent facts."""

GOLD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relevant", "signal", "tier", "why"],
    "properties": {
        "relevant": {"type": "boolean"},
        "signal": {"type": "string", "enum": [
            "catering_tender", "food_supply_tender", "event_tender", "employer_meal_signal",
            "new_facility", "expansion_hiring", "competitor_or_food_business", "abroad",
            "works_or_goods_tender", "past_event", "irrelevant"]},
        "tier": {"type": "string", "enum": ["A", "B", "C", "none"]},
        "why": {"type": "string"},
    },
}


def _item_prompt(item: dict) -> str:
    if item.get("text") and item["text"].strip() != item["title"].strip():
        return f"SOURCE: {item['source']} ({item['kind']})\nTITLE: {item['title']}\n\nTEXT:\n{item['text'][:4000]}"
    return f"SOURCE: {item['source']} ({item['kind']})\nTITLE ONLY: {item['title']}"


def _label_one(client, item: dict) -> dict:
    for attempt in range(4):
        try:
            resp = client.chat.completions.create(
                model=GOLD_MODEL,
                reasoning_effort="medium",
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "label", "strict": True, "schema": GOLD_SCHEMA}},
                messages=[{"role": "system", "content": GOLD_RUBRIC},
                          {"role": "user", "content": _item_prompt(item)}],
            )
            out = json.loads(resp.choices[0].message.content)
            usage = resp.usage
            return {"id": item["id"], "v": GOLD_VERSION, "model": GOLD_MODEL, **out,
                    "tokens_in": usage.prompt_tokens, "tokens_out": usage.completion_tokens}
        except Exception as exc:
            if attempt == 3:
                print(f"  etiket alınmadı {item['id']}: {type(exc).__name__}: {str(exc)[:120]}")
                return {}
            time.sleep(2 ** attempt)
    return {}


def label() -> None:
    from openai import OpenAI

    items = read_jsonl(ITEMS)
    done = {g["id"]: g for g in read_jsonl(GOLD) if g.get("v") == GOLD_VERSION}
    todo = [i for i in items if i["id"] not in done]
    print(f"{len(items)} element, {len(done)} artıq etiketli, {len(todo)} etiketlənəcək ({GOLD_MODEL})")
    client = OpenAI(max_retries=3)
    started = time.time()
    with ThreadPoolExecutor(8) as pool:
        for n, row in enumerate(pool.map(lambda it: _label_one(client, it), todo), 1):
            if row:
                done[row["id"]] = row
            if n % 100 == 0:
                _write_jsonl(GOLD, list(done.values()))
                print(f"  {n}/{len(todo)} ({time.time() - started:.0f}s)")
    _write_jsonl(GOLD, list(done.values()))
    rows = list(done.values())
    print(f"etiketli: {len(rows)} · relevant: {sum(r['relevant'] for r in rows)} · "
          f"tokens in/out: {sum(r['tokens_in'] for r in rows)}/{sum(r['tokens_out'] for r in rows)}")


# --- run ---------------------------------------------------------------------

def gold_map() -> dict[str, bool]:
    """id → relevant. İnsan etiketi (human_labels.csv: id,relevant) modeldən üstündür."""
    gold = {g["id"]: g["relevant"] for g in read_jsonl(GOLD) if g.get("v") == GOLD_VERSION}
    if HUMAN.exists():
        with HUMAN.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                value = (row.get("relevant") or "").strip().lower()
                if value in ("1", "true", "yes", "bəli", "beli", "0", "false", "no", "xeyr"):
                    gold[row["id"]] = value in ("1", "true", "yes", "bəli", "beli")
    return gold


def in_split(item_id: str, split: str) -> bool:
    """Sabit bölgü (id hash-i): eyni element həmişə eyni hissədədir."""
    if split == "all":
        return True
    is_test = int(item_id[:4], 16) % 10 < 3
    return is_test if split == "test" else not is_test


def _metrics(pairs: list[tuple[bool, bool]]) -> dict:
    tp = sum(p and g for p, g in pairs)
    fp = sum(p and not g for p, g in pairs)
    fn = sum(g and not p for p, g in pairs)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"n": len(pairs), "gold_pos": tp + fn, "pred_pos": tp + fp, "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


def _score_items(scorer: str, model: str, items: list[dict]) -> list[dict]:
    from openai import OpenAI

    from app.models import RawLead

    client = OpenAI(max_retries=5)
    if scorer == "v1":
        from app.ai_filter import AIFilter

        make = lambda: AIFilter(client=client, model=model)  # noqa: E731
    else:
        from app.ai_scoring import StructuredAIFilter

        make = lambda: StructuredAIFilter(client=client, model=model)  # noqa: E731

    def one(item: dict) -> dict:
        f = make()
        lead = RawLead(source=item["source"], source_url=item["url"], title=item["title"], raw_text=item["text"],
                       category="tender" if item["kind"] == "tender" else "other")
        if scorer == "v1":
            lead = f.score(lead)
            usage = None
        else:
            lead = f.score(lead)
            usage = f.last_usage
        return {"id": item["id"], "score": lead.ai_score, "reason": lead.ai_reason, "tier": getattr(lead, "tier", None),
                "facts": getattr(f, "last_facts", None),
                "tokens_in": getattr(usage, "prompt_tokens", 0), "tokens_out": getattr(usage, "completion_tokens", 0)}

    with ThreadPoolExecutor(4) as pool:   # 8 paralel → yeni hesabda 429 (dəqiqəlik limit)
        rows = list(pool.map(one, items))
    # Dəqiqəlik limitə düşənlər (skorsuz) ardıcıl, fasilə ilə təkrar.
    by_id = {i["id"]: i for i in items}
    for n, row in enumerate(rows):
        for attempt in range(3):
            if row["score"] is not None:
                break
            time.sleep(5 * (attempt + 1))
            row = rows[n] = one(by_id[row["id"]])
    return rows


def _triage_items(model: str, items: list[dict]) -> list[dict]:
    """select_relevant-in eynisi: açar söz (bazadan) VƏ YA AI triajı, mənfi söz bağlayır."""
    import os

    from app import ai_triage, keywords
    from app.scrapers.base import uninformative_title

    os.environ["AI_TRIAGE_MODEL"] = model
    out = []
    for kind in ("tender", "news"):
        group = [i for i in items if i["kind"] == kind]
        titles = [i["title"] for i in group]
        positive, negative = keywords.terms_for(kind) or ([], [])
        picked = ai_triage.triage(titles, kind=kind) or set()
        for n, item in enumerate(group):
            by_kw = keywords.any_match(item["title"], positive) or (kind == "tender" and uninformative_title(item["title"]))
            blocked = keywords.any_match(item["title"], negative) if negative else False
            out.append({"id": item["id"], "keyword": by_kw, "ai": n in picked,
                        "selected": (by_kw or n in picked) and not blocked})
    return out


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", choices=["score", "triage"], default="score")
    parser.add_argument("--scorer", choices=["v1", "v2", "prod"], default="v2")
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--show", type=int, default=12, help="neçə səhv nümunə göstərilsin")
    parser.add_argument("--split", choices=["all", "dev", "test"], default="all",
                        help="dev (70%%) — prompt/qayda tənzimləmək üçün; test (30%%) — yalnız yekun müqayisə")
    args = parser.parse_args()

    gold = gold_map()
    items = [i for i in read_jsonl(ITEMS) if i["id"] in gold and in_split(i["id"], args.split)]
    by_id = {i["id"]: i for i in items}
    started = time.time()

    if args.level == "triage":
        name = f"triage_{args.model}_{args.split}"
        from app import ai_triage

        ai_triage.triage(["yoxlama"], kind="news")  # AI işləmirsə triage None qaytarır — yoxla
        if ai_triage.triage(["Yeni zavod açılır"], kind="news") is None:
            sys.exit(f"DAYANDI: {name} — AI triajı işləmir (kredit/açar?). Yalnız açar sözlərlə ölçmək yanıltıcıdır.")
        items = [i for i in items if i["origin"] == "pool" or i.get("on_listing")]
        rows = _triage_items(args.model, items)
        pairs = [(r["selected"], gold[r["id"]]) for r in rows]
        m = _metrics(pairs)
        m["selected_share"] = round(sum(r["selected"] for r in rows) / len(rows), 3)
        misses = [by_id[r["id"]] for r in rows if gold[r["id"]] and not r["selected"]]
        extra = []
    else:
        scored_items = [i for i in items if i["origin"] == "lead"]
        if args.limit:
            scored_items = scored_items[:args.limit]
        if args.scorer == "prod":
            name = f"score_prod_{args.split}"
            rows = [{"id": i["id"], "score": i.get("prod_score"),
                     "pass": i.get("prod_status") in ("pending_review", "converted")} for i in scored_items]
        else:
            name = f"score_{args.scorer}_{args.model}_{args.split}"
            rows = _score_items(args.scorer, args.model, scored_items)
            for r in rows:
                r["pass"] = r["score"] is None or r["score"] >= 0.6   # skorsuz → insana gedir
        pairs = [(r["pass"], gold[r["id"]]) for r in rows]
        m = _metrics(pairs)
        m["tokens_in"] = sum(r.get("tokens_in", 0) for r in rows)
        m["tokens_out"] = sum(r.get("tokens_out", 0) for r in rows)
        m["failed"] = sum(1 for r in rows if r.get("score") is None and args.scorer != "prod")
        if m["failed"] > 0.05 * len(rows):
            # Skorsuz = "insana göndər" sayılır — çox olsa rəqəmlər yanlış olar (2026-10-05:
            # OpenAI krediti bitdi, 422-dən 422-si "keçdi" kimi göründü).
            sys.exit(f"DAYANDI: {name} — {m['failed']}/{len(rows)} element skorlanmadı (AI xətası/kredit?). Nəticə etibarsızdır.")
        misses = [by_id[r["id"]] | {"pred": r} for r in rows if gold[r["id"]] and not r["pass"]]
        extra = [by_id[r["id"]] | {"pred": r} for r in rows if r["pass"] and not gold[r["id"]]]

    m["seconds"] = round(time.time() - started)
    RESULTS.mkdir(parents=True, exist_ok=True)
    _write_jsonl(RESULTS / f"{name}.jsonl", rows)
    print(f"== {name} ==")
    print(json.dumps(m, ensure_ascii=False))
    gold_rows = {g["id"]: g for g in read_jsonl(GOLD)}
    print(f"-- buraxılan real fürsətlər (FN, {len(misses)}):")
    for it in misses[:args.show]:
        g = gold_rows.get(it["id"], {})
        pred = it.get("pred", {})
        print(f"  [{it['source']}] {it['title'][:80]} | gold {g.get('signal')}/{g.get('tier')} | "
              f"pred {pred.get('score')} {str(pred.get('reason') or '')[:70]}")
    if extra:
        print(f"-- səhv keçənlər (FP, {len(extra)}):")
        for it in extra[:args.show]:
            g = gold_rows.get(it["id"], {})
            pred = it.get("pred", {})
            print(f"  [{it['source']}] {it['title'][:80]} | gold: {g.get('why', '')[:70]} | "
                  f"pred {pred.get('score')}")


def tiers() -> None:
    """A/B/C razılığı: qızıl kateqoriya vs model (yalnız hər ikisinin relevant saydığı)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="score_v2_gpt-4.1-mini_all")
    parser.add_argument("--split", choices=["all", "dev", "test"], default="all")
    args = parser.parse_args()
    gold = {g["id"]: g for g in read_jsonl(GOLD) if g.get("v") == GOLD_VERSION}
    rows = [r for r in read_jsonl(RESULTS / f"{args.run}.jsonl") if r["id"] in gold and in_split(r["id"], args.split)]
    both = [(gold[r["id"]]["tier"], r.get("tier") or "none") for r in rows
            if gold[r["id"]]["relevant"] and (r.get("score") or 0) >= 0.6]
    labels = ["A", "B", "C", "none"]
    print(f"{args.run} [{args.split}] — sətir: qızıl, sütun: model ({len(both)} element)")
    print("      " + "".join(f"{p:>6}" for p in labels))
    for g in labels[:3]:
        print(f"{g:>6}" + "".join(f"{sum(1 for a, b in both if a == g and b == p):>6}" for p in labels))
    agree = sum(1 for a, b in both if a == b)
    print(f"razılıq: {agree}/{len(both)} = {agree / max(len(both), 1):.2f}")


def export() -> None:
    """Satış komandası üçün: model etiketi + səbəb; "relevant" sütununu düzəldib
    human_labels.csv kimi saxlasınlar."""
    gold = {g["id"]: g for g in read_jsonl(GOLD)}
    path = DATA / "for_sales_review.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "relevant", "tier", "signal", "source", "title", "model_why", "url"])
        for it in read_jsonl(ITEMS):
            g = gold.get(it["id"])
            if g:
                w.writerow([it["id"], "1" if g["relevant"] else "0", g["tier"], g["signal"], it["source"],
                            it["title"], g["why"], it.get("url") or ""])
    print(f"→ {path}")


def report() -> None:
    """Saxlanmış nəticələrdən (results/*.jsonl) hər bölgü üçün metrika — token xərcləmədən."""
    gold = gold_map()
    print(f"{'run':38} {'split':5} {'n':>4} {'P':>6} {'R':>6} {'F1':>6} {'keep':>6}")
    for path in sorted(RESULTS.glob("*_all.jsonl")):
        rows = [r for r in read_jsonl(path) if r["id"] in gold]
        for split in ("all", "dev", "test"):
            part = [r for r in rows if in_split(r["id"], split)]
            if path.name.startswith("triage"):
                pairs = [(r["selected"], gold[r["id"]]) for r in part]
            else:
                pairs = [(r["pass"] if "pass" in r else (r["score"] is None or r["score"] >= 0.6), gold[r["id"]])
                         for r in part]
            m = _metrics(pairs)
            keep = sum(p for p, _ in pairs) / len(pairs) if pairs else 0
            print(f"{path.stem[:-4]:38} {split:5} {m['n']:>4} {m['precision']:>6} {m['recall']:>6} "
                  f"{m['f1']:>6} {keep:>6.3f}")


COMMANDS = {"collect": collect, "label": label, "run": run, "export": export, "report": report, "tiers": tiers}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=sorted(COMMANDS))
    args, rest = parser.parse_known_args()
    sys.argv = [sys.argv[0], *rest]
    COMMANDS[args.command]()
