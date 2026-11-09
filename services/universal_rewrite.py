"""
services/universal_rewrite.py
Presentation-independent PPTX rewrite pipeline for Presenova.

    report = run_pipeline(src_pptx, dst_pptx, llm)      # llm: Callable[[str], str]

Stages: extract -> select -> prompt -> AI -> validate -> apply (format-preserving)
        -> re-open & verify -> before/after comparison -> before/after scoring.
Nothing here depends on topic, language, slide count, or specific text.
Only dependency: python-pptx.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
import difflib
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

_SKIP_PH = {PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.SLIDE_NUMBER, PP_PLACEHOLDER.DATE}
_TITLE_PH = {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.VERTICAL_TITLE}
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

MAX_CHARS_PER_CALL = 6000
_NUM_RE = re.compile(r"\d[\d.,:/%-]*\d|\d")
_PROTECTED_RE = re.compile(r"https?://\S+|www\.\S+|\S+@\S+\.\S+")


# --------------------------------------------------------------------------- #
# 1. Extraction
# --------------------------------------------------------------------------- #
@dataclass
class Unit:
    uid: str
    slide: int
    role: str                      # title | body | table | text
    text: str
    skip_reason: Optional[str] = None
    para: object = field(default=None, repr=False)

    @property
    def eligible(self) -> bool:
        return self.skip_reason is None


def _walk(shapes):
    for sh in shapes:
        if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _walk(sh.shapes)
        else:
            yield sh


def _skip_reason(par, text: str) -> Optional[str]:
    if sum(ch.isalpha() for ch in text) < 3:
        return "no_words"
    if "\v" in text or par._p.findall(f"{_A}br"):
        return "line_break"
    if par._p.findall(f"{_A}fld"):
        return "field"
    if not par.runs:
        return "no_runs"
    stripped = _PROTECTED_RE.sub("", text)
    if sum(ch.isalpha() for ch in stripped) < 3:
        return "url_or_email_only"
    return None


def extract_units(prs) -> List[Unit]:
    units: List[Unit] = []
    for sidx, slide in enumerate(prs.slides, start=1):
        for sh in _walk(slide.shapes):
            if sh.is_placeholder and sh.placeholder_format.type in _SKIP_PH:
                continue
            if sh.is_placeholder and sh.placeholder_format.type in _TITLE_PH:
                role = "title"
            elif sh.is_placeholder:
                role = "body"
            else:
                role = "text"
            containers = []
            if getattr(sh, "has_table", False) and sh.has_table:
                for r, row in enumerate(sh.table.rows):
                    for c, cell in enumerate(row.cells):
                        containers.append((f"t{r}.{c}", "table", cell.text_frame))
            elif sh.has_text_frame:
                containers.append(("tf", role, sh.text_frame))
            for tag, crole, tf in containers:
                for pi, par in enumerate(tf.paragraphs):
                    text = par.text
                    if not text.strip():
                        continue
                    units.append(Unit(
                        uid=f"s{sidx}:{sh.shape_id}:{tag}:p{pi}", slide=sidx,
                        role=crole, text=text,
                        skip_reason=_skip_reason(par, text), para=par))
    return units


# --------------------------------------------------------------------------- #
# 2. Prompt
# --------------------------------------------------------------------------- #
def _budget(unit: Unit) -> int:
    n = len(unit.text)
    return math.ceil(n * 1.10) + 6 if unit.role == "title" else math.ceil(n * 1.20) + 8


PROMPT_HEAD = """You are a meticulous presentation editor. You receive the real text of slides as JSON.
Improve ONLY items that genuinely need it: spelling, grammar, clarity, concision, and
parallel wording among sibling bullets on the same slide. Use each slide's title/context to
keep the meaning right.

RULES
1. Write in the SAME language and script as the input item. Never translate.
2. Keep the exact meaning. Do not add facts, claims, names, or numbers. Keep every number,
   date, URL, email, and proper noun exactly as given.
3. Never change a correct word into another form of it: do not split or merge compounds
   (e.g. "speedup" stays "speedup") and do not switch noun/adjective forms (e.g.
   "Architectural" stays "Architectural"). Change a word only for a genuine spelling or
   grammar error, or to remove clear redundancy.
4. Each rewritten item must not exceed its "max_chars". Titles stay short.
5. Do not add bullet symbols, numbering, markdown, quotes, or line breaks.
6. Return ONLY items you changed. If nothing needs changing, return {"edits": []}.
7. Use the exact "id" values given.

OUTPUT: a single JSON object and nothing else:
{"edits": [{"id": "<id>", "text": "<improved text>"}]}
"""


def build_prompt(slide_groups: List[dict]) -> str:
    return (PROMPT_HEAD + "\n<<<SLIDES_JSON\n"
            + json.dumps(slide_groups, ensure_ascii=False)
            + "\nSLIDES_JSON>>>\n")


def _slide_groups(units: List[Unit]) -> List[dict]:
    by_slide: Dict[int, List[Unit]] = {}
    for u in units:
        by_slide.setdefault(u.slide, []).append(u)
    groups = []
    for s, us in by_slide.items():
        title = next((u.text for u in us if u.role == "title"), "")
        groups.append({
            "slide": s, "slide_title": title,
            "items": [{"id": u.uid, "role": u.role, "text": u.text, "max_chars": _budget(u)}
                      for u in us if u.eligible],
        })
    return [g for g in groups if g["items"]]


def _chunks(groups: List[dict]) -> List[List[dict]]:
    out, cur, size = [], [], 0
    for g in groups:
        gsize = len(json.dumps(g, ensure_ascii=False))
        if cur and size + gsize > MAX_CHARS_PER_CALL:
            out.append(cur)
            cur, size = [], 0
        cur.append(g)
        size += gsize
    if cur:
        out.append(cur)
    return out


# --------------------------------------------------------------------------- #
# 3. Parse + validate AI output
# --------------------------------------------------------------------------- #
def parse_edits(raw: str) -> Optional[List[dict]]:
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I).strip()
    for cand in (s, s[s.find("{"): s.rfind("}") + 1] if "{" in s else ""):
        try:
            data = json.loads(cand)
            edits = data.get("edits") if isinstance(data, dict) else data
            if isinstance(edits, list):
                return [e for e in edits if isinstance(e, dict)]
        except Exception:
            continue
    return None


def _dominant_script(text: str) -> str:
    c = Counter()
    for ch in text:
        if ch.isalpha():
            c[unicodedata.name(ch, "UNKNOWN").split()[0]] += 1
    return c.most_common(1)[0][0] if c else ""


def _norm_nums(text: str) -> Counter:
    return Counter(n.rstrip(".,:;-") for n in _NUM_RE.findall(text))


def validate_edit(unit: Unit, new: object) -> Optional[str]:
    """Return a rejection reason, or None if the edit is acceptable."""
    if not isinstance(new, str) or not new.strip():
        return "empty"
    new = new.strip()
    if new == unit.text.strip():
        return "unchanged"
    if "\n" in new or "\r" in new or "\v" in new:
        return "line_break"
    if len(new) > _budget(unit):
        return "too_long"
    if _norm_nums(new) != _norm_nums(unit.text):
        return "numbers_changed"
    if Counter(_PROTECTED_RE.findall(new)) != Counter(_PROTECTED_RE.findall(unit.text)):
        return "url_or_email_changed"
    if _dominant_script(new) != _dominant_script(unit.text):
        return "language_changed"
    return None


# --------------------------------------------------------------------------- #
# 4. Format-preserving apply
# --------------------------------------------------------------------------- #
def apply_text(par, new: str) -> None:
    """Replace paragraph text keeping each run's formatting (bold/italic/font/colour)."""
    runs = par.runs
    if len(runs) == 1:
        runs[0].text = new
        return
    lens = [len(r.text) for r in runs]
    total = sum(lens) or 1
    bounds, cum = [], 0
    for l in lens:
        cum += l
        bounds.append(cum / total * len(new))
    out, ri, pos = [""] * len(runs), 0, 0
    for w in re.findall(r"\S+\s*|\s+", new):
        while ri < len(runs) - 1 and pos >= bounds[ri]:
            ri += 1
        out[ri] += w
        pos += len(w)
    for r, t in zip(runs, out):
        r.text = t


# --------------------------------------------------------------------------- #
# 5. Scoring (deterministic, language-agnostic, computed from actual content)
# --------------------------------------------------------------------------- #
def _wc(text: str) -> int:
    t = text.strip()
    if not t:
        return 0
    return max(1, len(t) // 2) if (" " not in t and len(t) > 10) else len(t.split())


def score_units(texts: List[dict]) -> dict:
    """texts: [{uid, slide, role, text, group}] -> metrics, score, recommendations."""
    body = [t for t in texts if t["role"] != "title"]
    titles = [t for t in texts if t["role"] == "title"]
    words = max(1, sum(_wc(t["text"]) for t in texts))

    long20 = [t for t in body if _wc(t["text"]) > 20]
    long30 = [t for t in body if _wc(t["text"]) > 30]
    concision = 100.0
    if body:
        concision = 100 * (1 - 0.6 * len(long20) / len(body) - 0.4 * len(long30) / len(body))
    long_titles = [t for t in titles if _wc(t["text"]) > 10]
    title_score = 100 * (1 - len(long_titles) / len(titles)) if titles else 100.0

    groups: Dict[str, List[dict]] = {}
    for t in body:
        groups.setdefault(t["group"], []).append(t)
    cons, n = 0.0, 0
    for g in groups.values():
        if len(g) < 2:
            continue
        end = Counter(("." if x["text"].rstrip()[-1:] in ".!?。" else "") for x in g)
        cap = Counter(x["text"].lstrip()[:1].isupper() for x in g if x["text"].lstrip()[:1].isalpha())
        cons += end.most_common(1)[0][1] / len(g)
        cons += (cap.most_common(1)[0][1] / sum(cap.values())) if cap else 1
        n += 2
    consistency = 100 * cons / n if n else 100.0

    issues = []
    for t in texts:
        s = t["text"]
        c = 0
        c += len(re.findall(r"\b(\w+)\s+\1\b", s, flags=re.I | re.U))
        c += len(re.findall(r"\S {2,}\S", s))
        c += len(re.findall(r"\s[,.;:!?]", s))
        c += 1 if s != s.strip() else 0
        c += 1 if (len(s) > 12 and s.isupper() and s.isalpha() is False and any(ch.isalpha() for ch in s)) else 0
        if c:
            issues.append((t["slide"], c))
    issue_total = sum(c for _, c in issues)
    clean = max(0.0, 100 - min(100.0, issue_total / words * 100 * 15))

    clamp = lambda v: round(max(0.0, min(100.0, v)), 1)
    metrics = {"concision": clamp(concision), "title_brevity": clamp(title_score),
               "consistency": clamp(consistency), "cleanliness": clamp(clean)}
    overall = clamp(0.35 * metrics["concision"] + 0.15 * metrics["title_brevity"]
                    + 0.25 * metrics["consistency"] + 0.25 * metrics["cleanliness"])

    recs = []
    if long20:
        sl = sorted({t["slide"] for t in long20})
        recs.append(f"{len(long20)} text item(s) exceed 20 words (slides {sl}); split or shorten.")
    if long_titles:
        sl = sorted({t["slide"] for t in long_titles})
        recs.append(f"{len(long_titles)} title(s) exceed 10 words (slides {sl}); shorten.")
    if metrics["consistency"] < 90:
        recs.append("Bullet punctuation/capitalisation is inconsistent within some text boxes.")
    if issue_total:
        sl = sorted({s for s, _ in issues})
        recs.append(f"{issue_total} typographic issue(s) (repeats, spacing) on slides {sl}.")
    return {"overall": overall, "metrics": metrics, "word_count": words,
            "item_count": len(texts), "recommendations": recs}


def _score_prs(prs) -> dict:
    texts = [{"uid": u.uid, "slide": u.slide, "role": u.role, "text": u.text,
              "group": u.uid.rsplit(":p", 1)[0]} for u in extract_units(prs)]
    return score_units(texts)


# --------------------------------------------------------------------------- #
# 6. Comparison
# --------------------------------------------------------------------------- #
def compare(before: Dict[str, str], after: Dict[str, str], meta: Dict[str, dict]) -> dict:
    slides: Dict[int, dict] = {}
    for uid, old in before.items():
        new = after.get(uid, old)
        s = meta[uid]["slide"]
        d = slides.setdefault(s, {"slide": s, "changes": [], "total_items": 0})
        d["total_items"] += 1
        if new != old:
            ow, nw = old.split(), new.split()
            sm = difflib.SequenceMatcher(None, ow, nw)
            removed = [w for tag, i1, i2, _, _ in sm.get_opcodes() if tag in ("replace", "delete") for w in ow[i1:i2]]
            added = [w for tag, _, _, j1, j2 in sm.get_opcodes() if tag in ("replace", "insert") for w in nw[j1:j2]]
            d["changes"].append({"id": uid, "role": meta[uid]["role"], "before": old, "after": new,
                                 "similarity": round(sm.ratio(), 3),
                                 "removed_words": removed, "added_words": added})
    changed = sum(len(d["changes"]) for d in slides.values())
    return {"slides": [slides[k] for k in sorted(slides)],
            "items_total": len(before), "items_changed": changed}


# --------------------------------------------------------------------------- #
# 7. Orchestrator
# --------------------------------------------------------------------------- #
def run_pipeline(src: str, dst: str, llm: Callable[[str], str], max_retries: int = 1) -> dict:
    prs = Presentation(src)
    units = extract_units(prs)
    by_id = {u.uid: u for u in units}
    meta = {u.uid: {"slide": u.slide, "role": u.role} for u in units}
    before = {u.uid: u.text for u in units}
    score_before = _score_prs(Presentation(src))

    report = {"slides": len(prs.slides), "units": len(units),
              "eligible": sum(u.eligible for u in units),
              "skipped": dict(Counter(u.skip_reason for u in units if u.skip_reason)),
              "ai_calls": 0, "ai_failures": [], "applied": [], "rejected": []}

    chunks = _chunks(_slide_groups(units))
    accepted: Dict[str, str] = {}
    for chunk in chunks:
        prompt = build_prompt(chunk)
        edits = None
        for _ in range(1 + max_retries):
            report["ai_calls"] += 1
            try:
                edits = parse_edits(llm(prompt))
            except Exception as exc:                       # quota, network, etc.
                report["ai_failures"].append(f"{type(exc).__name__}: {exc}")
                edits = None
            if edits is not None:
                break
        if edits is None:
            report["ai_failures"].append("unparseable_or_failed_chunk")
            continue
        allowed = {i["id"] for g in chunk for i in g["items"]}
        for e in edits:
            uid, new = e.get("id"), e.get("text")
            if uid not in allowed or uid not in by_id:
                report["rejected"].append({"id": uid, "reason": "unknown_id"})
                continue
            reason = validate_edit(by_id[uid], new)
            if reason:
                if reason != "unchanged":
                    report["rejected"].append({"id": uid, "reason": reason})
                continue
            accepted[uid] = new.strip()

    for uid, new in accepted.items():
        apply_text(by_id[uid].para, new)
        report["applied"].append(uid)
    prs.save(dst)

    # ---- verify the FINAL file, re-opened from disk ----
    out_prs, src_prs = Presentation(dst), Presentation(src)
    after_units = extract_units(out_prs)
    after = {u.uid: u.text for u in after_units}
    mism = [uid for uid in before if after.get(uid) != accepted.get(uid, before[uid])]

    def _count(p, pic):
        return sum(1 for s in p.slides for sh in _walk(s.shapes)
                   if (sh.shape_type == MSO_SHAPE_TYPE.PICTURE) == pic)
    report["verification"] = {
        "ok": not mism and len(out_prs.slides) == len(src_prs.slides)
              and _count(out_prs, True) == _count(src_prs, True)
              and _count(out_prs, False) == _count(src_prs, False)
              and set(after) == set(before),
        "text_mismatches": mism,
        "slide_count_same": len(out_prs.slides) == len(src_prs.slides),
        "pictures_same": _count(out_prs, True) == _count(src_prs, True),
        "shapes_same": _count(out_prs, False) == _count(src_prs, False),
        "units_same": set(after) == set(before),
    }
    report["comparison"] = compare(before, after, meta)
    score_after = _score_prs(out_prs)
    report["scores"] = {"before": score_before, "after": score_after,
                        "delta_overall": round(score_after["overall"] - score_before["overall"], 1)}
    return report