"""
Deck Enhancement (opt-in) — fixes headings, expands thin content, adds slides.

Runs AFTER the normal rewrite has produced the improved PPTX and works directly on
that file with python-pptx, so it does not touch the guarded RewriteExecutor /
update_presentation_text logic.

What it can do (all decided by ONE AI call that returns a JSON edit plan):
  1. rewrites   : replace a heading that does not match its slide, or a very thin
                  paragraph, with fuller text (same paragraph count, same markers).
  2. new_slides : insert up to 3 new slides, cloned from an existing slide so the
                  theme, colours and layout stay identical.
  3. renumber   : footer counters ("03 / 05") are rewritten for the new slide count.

Safety guards (deterministic, no AI):
  - Footers / brand lines / audience-tone lines are PROTECTED and never edited.
  - Any number in new text that does not already exist in the deck is rejected
    (no invented statistics). Whole edit is dropped, not partially applied.
  - Paragraph counts must match; templates with pictures/charts/tables are never cloned.
  - Any failure -> the file is left exactly as the normal rewrite produced it.

Public API:
    run_enhancement(provider, pptx_path) -> dict summary
"""

import copy
import json
import logging
import re
from typing import Optional

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn

logger = logging.getLogger(__name__)

MAX_NEW_SLIDES = 3
MAX_PARAGRAPH_CHARS = 320

_COUNTER_END_RE = re.compile(r"\d{1,3}\s*/\s*\d{1,3}\s*$")
_COUNTER_SUB_RE = re.compile(r"(?<!\d)(\d{1,3})(\s*/\s*)(\d{1,3})(?!\d)")
_MID_BULLET_RE = re.compile(r"\S\s+[•·]\s+\S")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_LEAD_MARKER_RE = re.compile(r"^\s*(?:[•·\-–*]\s*|\d{1,2}[.)]\s*)")
_RID_ATTRS = (qn("r:embed"), qn("r:link"), qn("r:id"), qn("r:pict"))


# ── Reading the deck ────────────────────────────────────────────────────────

def _para_text(paragraph) -> str:
    return "".join(r.text for r in paragraph.runs)


def _is_protected_text(text: str) -> bool:
    """Footers, brand lines and 'Audience … • Tone …' lines must never be edited."""
    t = text.strip()
    if not t:
        return False
    if "|" in t:
        return True
    if _MID_BULLET_RE.search(t):
        return True
    if _COUNTER_END_RE.search(t) and ("•" in t or "·" in t):
        return True
    return False


def describe_deck(prs) -> list:
    """Self-contained description of the deck (indexes match python-pptx enumeration)."""
    deck = []
    for sn, slide in enumerate(prs.slides, 1):
        shapes = []
        has_media = False
        for si, shape in enumerate(slide.shapes):
            if shape.shape_type in (MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.CHART,
                                    MSO_SHAPE_TYPE.TABLE, MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT,
                                    MSO_SHAPE_TYPE.MEDIA) or getattr(shape, "has_chart", False) \
                    or getattr(shape, "has_table", False):
                has_media = True
            if not shape.has_text_frame:
                continue
            paras = [_para_text(p) for p in shape.text_frame.paragraphs]
            if not any(p.strip() for p in paras):
                continue
            protected = any(_is_protected_text(p) for p in paras)
            tiny = len(paras) == 1 and len(paras[0].strip()) <= 3  # badges: "A", "01"
            shapes.append({
                "shape_index": si,
                "paragraphs": paras,
                "editable": not protected and not tiny,
            })
        deck.append({"slide_number": sn, "shapes": shapes, "has_media": has_media})
    return deck


def _deck_text(deck: list) -> str:
    return "\n".join(p for s in deck for sh in s["shapes"] for p in sh["paragraphs"])


# ── Prompt ──────────────────────────────────────────────────────────────────

def build_enhance_prompt(deck: list, context: Optional[dict] = None) -> str:
    blocks = []
    for s in deck:
        lines = [f"Slide {s['slide_number']}"
                 + ("  [contains picture/chart/table: NOT usable as template]" if s["has_media"] else "")]
        for sh in s["shapes"]:
            tag = "editable" if sh["editable"] else "PROTECTED"
            lines.append(f"  shape_index={sh['shape_index']} ({tag}, {len(sh['paragraphs'])} paragraphs): "
                         + " | ".join(json.dumps(p, ensure_ascii=False) for p in sh["paragraphs"]))
        blocks.append("\n".join(lines))

    ctx = ""
    if context:
        ctx = (f"\nTopic: {context.get('overall_topic', 'N/A')}\n"
               f"Objective: {context.get('main_objective', 'N/A')}\n")

    return f"""You are a senior presentation editor. Review the deck below and return a JSON EDIT PLAN.
The deck was already spell-checked; your job is structure and completeness.{ctx}
=== RULES ===
1. Write in the SAME language as the deck.
2. Use ONLY facts, terms and numbers that already appear in the deck. NEVER invent statistics,
   numbers, names, products, dates or claims. If you cannot add value without inventing facts, add nothing.
3. "rewrites": (a) a heading that does not match its slide's content, or (b) a content paragraph that is
   very thin or incomplete (roughly under 6 words, not a label/badge). Give the FULL replacement
   paragraphs for that shape: the SAME number of paragraphs, keeping markers like bullets or "01.".
   Never touch PROTECTED shapes.
4. "new_slides": ONLY if the deck clearly lacks something essential (for example agenda, conclusion,
   next steps) or one slide is overloaded. At most {MAX_NEW_SLIDES}. Choose "template_slide" = an existing
   slide WITHOUT picture/chart/table. Provide text for EVERY editable shape of that template, with the
   same number of paragraphs per shape. "insert_after" is the slide number to insert after (0 = first).
5. If the deck is already complete and clear, return empty lists. Fewer, safer edits are better.
6. Do not change slide order, and do not delete anything.

=== JSON SCHEMA ===
{{"summary":"<one sentence>",
 "rewrites":[{{"slide_number":<int>,"shape_index":<int>,"paragraphs":["<text>"],"reason":"<why>"}}],
 "new_slides":[{{"insert_after":<int>,"template_slide":<int>,"reason":"<why>",
   "textboxes":[{{"shape_index":<int>,"paragraphs":["<text>"]}}]}}]}}
Return ONLY valid JSON.

=== DECK ===
{chr(10).join(blocks)}
"""


# ── Plan validation (deterministic guards) ──────────────────────────────────

def _norm_num(token: str) -> str:
    t = token.replace(",", "")
    try:
        f = float(t)
        return str(int(f)) if f == int(f) and "." not in t else str(f)
    except ValueError:
        return t


def _numbers(text: str) -> set:
    """Normalised numbers in text (leading zeros ignored, list markers skipped)."""
    out = set()
    for line in text.splitlines():
        line = _LEAD_MARKER_RE.sub("", line)
        out.update(_norm_num(n) for n in _NUM_RE.findall(line))
    return out


def _invented_numbers(text: str, allowed: set) -> list:
    """Numbers in `text` that are neither in the deck nor harmless small ordinals (1-10)."""
    bad = []
    stripped = _LEAD_MARKER_RE.sub("", text)
    for m in _NUM_RE.finditer(stripped):
        val = _norm_num(m.group(0))
        if val in allowed:
            continue
        nxt = stripped[m.end():m.end() + 1]
        if val.isdigit() and 1 <= int(val) <= 10 and nxt not in ("%", "x", "X", "×"):
            continue
        bad.append(m.group(0))
    return bad


def _paras_ok(paras, original: list, allowed_numbers: set) -> Optional[str]:
    if not isinstance(paras, list) or len(paras) != len(original):
        return "paragraph count mismatch"
    for new, old in zip(paras, original):
        if not isinstance(new, str) or not new.strip():
            return "empty paragraph"
        if len(old.strip()) <= 3 and new.strip() != old.strip():
            return "badge/label paragraph must stay unchanged"
        if len(new) > MAX_PARAGRAPH_CHARS:
            return "paragraph too long"
        extra = _invented_numbers(new, allowed_numbers)
        if extra:
            return f"invented number(s): {extra}"
    return None


def validate_plan(plan: dict, deck: list):
    """Returns (clean_plan, rejected_reasons)."""
    rejected = []
    if not isinstance(plan, dict):
        return {"rewrites": [], "new_slides": []}, ["plan was not an object"]

    by_slide = {s["slide_number"]: s for s in deck}
    allowed_numbers = _numbers(_deck_text(deck))

    rewrites = []
    seen = set()
    for item in plan.get("rewrites") or []:
        try:
            sn, si = int(item["slide_number"]), int(item["shape_index"])
        except Exception:
            rejected.append("rewrite: bad slide/shape reference")
            continue
        shape = next((sh for sh in by_slide.get(sn, {}).get("shapes", []) if sh["shape_index"] == si), None)
        if not shape or not shape["editable"]:
            rejected.append(f"rewrite s{sn}.{si}: target missing or protected")
            continue
        if (sn, si) in seen:
            continue
        err = _paras_ok(item.get("paragraphs"), shape["paragraphs"], allowed_numbers)
        if err:
            rejected.append(f"rewrite s{sn}.{si}: {err}")
            continue
        if [p.strip() for p in item["paragraphs"]] == [p.strip() for p in shape["paragraphs"]]:
            continue
        seen.add((sn, si))
        rewrites.append({"slide_number": sn, "shape_index": si,
                         "paragraphs": [p for p in item["paragraphs"]],
                         "reason": str(item.get("reason", ""))[:200]})

    new_slides = []
    for item in (plan.get("new_slides") or [])[:MAX_NEW_SLIDES]:
        try:
            after, tpl = int(item["insert_after"]), int(item["template_slide"])
        except Exception:
            rejected.append("new slide: bad insert_after/template_slide")
            continue
        template = by_slide.get(tpl)
        if not template or template["has_media"] or not (0 <= after <= len(deck)):
            rejected.append(f"new slide: template {tpl} unusable")
            continue
        editable = [sh for sh in template["shapes"] if sh["editable"]]
        given = {}
        for tb in item.get("textboxes") or []:
            try:
                given[int(tb["shape_index"])] = tb.get("paragraphs")
            except Exception:
                pass
        ok, boxes = True, []
        for sh in editable:
            err = _paras_ok(given.get(sh["shape_index"]), sh["paragraphs"], allowed_numbers)
            if err:
                rejected.append(f"new slide (template {tpl}) shape {sh['shape_index']}: {err}")
                ok = False
                break
            boxes.append({"shape_index": sh["shape_index"], "paragraphs": given[sh["shape_index"]]})
        if ok and boxes:
            new_slides.append({"insert_after": after, "template_slide": tpl, "textboxes": boxes,
                               "reason": str(item.get("reason", ""))[:200]})
    return {"rewrites": rewrites, "new_slides": new_slides}, rejected


# ── Applying the plan ───────────────────────────────────────────────────────

def _set_paragraph(paragraph, text: str) -> None:
    runs = paragraph.runs
    if runs:
        runs[0].text = text
        for r in runs[1:]:
            r._r.getparent().remove(r._r)
    else:
        paragraph.add_run().text = text


def _set_shape_paragraphs(shape, paragraphs: list) -> None:
    for p, text in zip(shape.text_frame.paragraphs, paragraphs):
        _set_paragraph(p, text)


def clone_slide(prs, source):
    """Append a visual copy of `source` (same layout, shapes, background) to the deck."""
    new = prs.slides.add_slide(source.slide_layout)
    for shp in list(new.shapes):
        shp._element.getparent().remove(shp._element)

    rel_map = {}
    for rel in source.part.rels.values():
        if rel.reltype.endswith("/slideLayout") or rel.reltype.endswith("/notesSlide"):
            continue
        if rel.is_external:
            rel_map[rel.rId] = new.part.rels.get_or_add_ext_rel(rel.reltype, rel.target_ref)
        else:
            rel_map[rel.rId] = new.part.rels.get_or_add(rel.reltype, rel.target_part)

    src_cSld, dst_cSld = source._element.cSld, new._element.cSld
    bg = src_cSld.find(qn("p:bg"))
    if bg is not None:
        old_bg = dst_cSld.find(qn("p:bg"))
        if old_bg is not None:
            dst_cSld.remove(old_bg)
        dst_cSld.insert(0, copy.deepcopy(bg))

    dst_tree = new.shapes._spTree
    for el in source.shapes._spTree.iterchildren():
        if el.tag in (qn("p:nvGrpSpPr"), qn("p:grpSpPr")):
            continue
        clone = copy.deepcopy(el)
        for node in clone.iter():
            for attr in _RID_ATTRS:
                rid = node.get(attr)
                if rid and rid in rel_map:
                    node.set(attr, rel_map[rid])
        dst_tree.append(clone)
    return new


def _renumber_footers(prs) -> int:
    total = len(prs.slides)
    changed = 0
    for idx, slide in enumerate(prs.slides, 1):
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for p in shape.text_frame.paragraphs:
                text = _para_text(p)
                if not (_COUNTER_END_RE.search(text.strip()) and ("•" in text or "·" in text)):
                    continue

                def _fmt(m, idx=idx):
                    width = len(m.group(1))
                    return f"{idx:0{width}d}{m.group(2)}{total:0{len(m.group(3))}d}"

                done = False
                for r in p.runs:
                    new_t = _COUNTER_SUB_RE.sub(_fmt, r.text)
                    if new_t != r.text:
                        r.text, done = new_t, True
                if done:
                    changed += 1
    return changed


def apply_plan(pptx_path: str, plan: dict) -> dict:
    prs = Presentation(pptx_path)
    originals = list(prs.slides)
    n_orig = len(originals)

    applied_rewrites = 0
    for item in plan["rewrites"]:
        slide = originals[item["slide_number"] - 1]
        shapes = list(slide.shapes)
        shape = shapes[item["shape_index"]]
        _set_shape_paragraphs(shape, item["paragraphs"])
        applied_rewrites += 1

    new_elements = []   # (insert_after, sldId element, order)
    for k, item in enumerate(plan["new_slides"]):
        template = originals[item["template_slide"] - 1]
        new_slide = clone_slide(prs, template)
        shapes = list(new_slide.shapes)
        for tb in item["textboxes"]:
            _set_shape_paragraphs(shapes[tb["shape_index"]], tb["paragraphs"])
        sld_id = list(prs.slides._sldIdLst)[-1]
        new_elements.append((item["insert_after"], sld_id, k))

    if new_elements:
        sldIdLst = prs.slides._sldIdLst
        ids = list(sldIdLst)
        orig_ids = ids[:n_orig]
        order = [el for (a, el, _) in sorted(new_elements, key=lambda t: (t[0], t[2])) if a == 0]
        for pos, el in enumerate(orig_ids, 1):
            order.append(el)
            order.extend(e for (a, e, _) in sorted(new_elements, key=lambda t: (t[0], t[2])) if a == pos)
        for el in ids:
            sldIdLst.remove(el)
        for el in order:
            sldIdLst.append(el)

    footers = _renumber_footers(prs)
    prs.save(pptx_path)
    Presentation(pptx_path)  # reopen = corruption check
    return {"rewrites_applied": applied_rewrites,
            "slides_added": len(plan["new_slides"]),
            "footers_renumbered": footers,
            "slide_count": len(prs.slides)}


# ── Public entry point ──────────────────────────────────────────────────────

def run_enhancement(provider, pptx_path: str, context: Optional[dict] = None) -> dict:
    """Never raises. On any problem the file is left untouched and 'error' explains why."""
    summary = {"enabled": True, "rewrites_applied": 0, "slides_added": 0,
               "footers_renumbered": 0, "rejected": [], "plan_summary": "", "error": None}
    try:
        prs = Presentation(pptx_path)
        deck = describe_deck(prs)
        prompt = build_enhance_prompt(deck, context)
        raw = provider.generate_structured(prompt=prompt, temperature=0.3, max_output_tokens=4096)
        if not isinstance(raw, dict) or not raw:
            summary["error"] = "AI returned no usable enhancement plan; deck left as rewritten."
            return summary
        plan, rejected = validate_plan(raw, deck)
        summary["rejected"] = rejected
        summary["plan_summary"] = str(raw.get("summary", ""))[:300]
        if not plan["rewrites"] and not plan["new_slides"]:
            return summary
        summary.update(apply_plan(pptx_path, plan))
        summary["details"] = {
            "rewrites": [{"slide": r["slide_number"], "reason": r["reason"]} for r in plan["rewrites"]],
            "new_slides": [{"after": n["insert_after"], "reason": n["reason"]} for n in plan["new_slides"]],
        }
    except Exception as exc:  # file stays as the normal rewrite produced it
        logger.warning("[enhance] Enhancement skipped (non-fatal): %s", exc)
        summary["error"] = f"Enhancement skipped: {exc}"
    return summary