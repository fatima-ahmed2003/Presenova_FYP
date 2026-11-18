"""
Refresh mode helpers (services/rewrite/refresh.py)

"Refresh" turns a template-style deck full of generic filler ("Baseline
Normalization", fake KPIs, fake team names ...) into accurate, topic-specific
content WITHOUT touching the design: same slides, same shapes, same paragraph
counts -- only the text inside the existing text slots changes.

This module holds everything specific to that mode:
  * prompt builders (deck plan + per-batch rewrite)
  * topic detection
  * a refresh-specific paragraph guard (replaces the strict polish guard)

Guard differences vs. the polish-mode guard in executor.py:
  * numbers may be REMOVED (fake stats -> words) but never ADDED
    (no invented statistics); structural numbers (01, STEP 02, 02 / 12) stay
  * length is capped per paragraph (max_chars) so text still fits the shape
  * footers / brand lines / slide counters stay locked
"""

import difflib
import logging
import re
from collections import Counter
from typing import Optional

logger = logging.getLogger(__name__)

_NUM_RE = re.compile(r'\d+(?:[.,]\d+)?')

_MARKER_RE = re.compile(
    r'^[\s\u2022\u25A0\u25AA\u25CF\u25E6\u25B6\u25BA\u2713\u2714\u27A4\u2192'
    r'\*\-\u2013\u2014\u2600-\u27BF\u2B50\uFE0F\U0001F300-\U0001FAFF]+'
)

# Footers / brand lines stay locked. NOTE: the "Audience: X • Tone: Y" line is
# deliberately NOT locked here - it is prompt leakage and should be replaced.
_LOCKED_PATTERNS = [
    re.compile(r'\d{1,3}\s*/\s*\d{1,3}\s*$'),
    re.compile(r'^\s*presenova\s+ai\b', re.I),
    re.compile(r'^\s*(confidential|draft|\u00a9|copyright)\b', re.I),
]
_LOCKED_MIN_SIMILARITY = 0.92

_FOOTER_TOPIC_RE = re.compile(r'\|\s*([^|\u2022]+?)\s*\u2022\s*\d{1,3}\s*/\s*\d{1,3}\s*$')


def _ptext(p) -> str:
    if isinstance(p, dict):
        return str(p.get('text', ''))
    return str(p) if p is not None else ''


def is_locked(text: str) -> bool:
    return any(p.search(text or '') for p in _LOCKED_PATTERNS)


def max_chars_for(text: str) -> int:
    """Character budget for a replacement so it still fits the original shape."""
    n = len((text or '').strip())
    if n <= 8:
        return n + 8
    if n <= 40:
        return int(n * 1.4) + 6
    return int(n * 1.3) + 10


def _numbers(text: str) -> Counter:
    return Counter(_NUM_RE.findall(text or ''))


def _leading_marker(text: str) -> str:
    m = _MARKER_RE.match(text or '')
    return m.group(0).strip() if m else ''


# ── Topic detection ─────────────────────────────────────────────────────────
def detect_topic(slides: list, presentation_context: Optional[dict] = None) -> str:
    """Best-effort deck topic: footer text > holistic context > first title."""
    counts: Counter = Counter()
    for slide in slides or []:
        for tb in slide.get('textboxes', []) or []:
            for p in tb.get('paragraphs', []) or []:
                m = _FOOTER_TOPIC_RE.search(_ptext(p))
                if m:
                    counts[m.group(1).strip()] += 1
    if counts:
        return counts.most_common(1)[0][0]
    if isinstance(presentation_context, dict):
        topic = str(presentation_context.get('overall_topic') or '').strip()
        if topic and topic.lower() != 'untitled presentation':
            return topic
    for slide in slides or []:
        title = str(slide.get('title') or '').strip()
        if title:
            return title
    return 'the presentation topic'


def deck_outline(slides: list) -> str:
    return '\n'.join(f"{s.get('slide_number')}. {s.get('title', '')}" for s in slides or [])


def slide_texts_digest(slide: dict, limit: int = 8) -> list:
    out = []
    for tb in slide.get('textboxes', []) or []:
        for p in tb.get('paragraphs', []) or []:
            t = _ptext(p).strip()
            if t and not is_locked(t):
                out.append(t[:50])
            if len(out) >= limit:
                return out
    return out


def slide_payload(slide: dict) -> dict:
    textboxes = []
    for tb in slide.get('textboxes', []) or []:
        paras = []
        for p in tb.get('paragraphs', []) or []:
            t = _ptext(p)
            paras.append({
                'text': t,
                'max_chars': max_chars_for(t),
                'locked': bool(is_locked(t) or not t.strip()),
            })
        textboxes.append({
            'shape_index': tb.get('shape_index'),
            'shape_name': tb.get('shape_name', ''),
            'paragraphs': paras,
        })
    return {
        'slide_number': slide.get('slide_number'),
        'current_title': slide.get('title', ''),
        'textboxes': textboxes,
    }


def chunk_slides(slides: list, max_slides: int = 5, max_paragraphs: int = 70) -> list:
    chunks, cur, count = [], [], 0
    for s in slides:
        n = sum(len(tb.get('paragraphs', []) or []) for tb in s.get('textboxes', []) or [])
        if cur and (len(cur) >= max_slides or count + n > max_paragraphs):
            chunks.append(cur)
            cur, count = [], 0
        cur.append(s)
        count += n
    if cur:
        chunks.append(cur)
    return chunks


# ── Prompts ─────────────────────────────────────────────────────────────────
_RULES = """\
HARD RULES
1. Keep every slide's layout role and slot count. A 3-step workflow stays 3 steps, a 4-box SWOT stays 4 boxes,
   a row of 3 cards stays 3 cards. Short structural labels (STEP 01, 01., A, S/W/O/T, 08.) stay as they are.
2. Replace generic corporate filler (telemetry, pipelines, synergy, "baseline normalization", "component #6" ...)
   with accurate, specific, educational content about the topic. Write what a knowledgeable teacher would write.
3. If a slot's role does not fit the topic (fake team members, performance benchmarks, KPI numbers), REPURPOSE it
   to the closest useful concept of the topic with the SAME number of items (e.g. 3 "people" cards -> 3 key
   participants/concepts; initials circles must match the new headings).
4. NEVER invent statistics, percentages, multipliers, years, dates, prices, people, companies or quotes.
   Use qualitative, widely-accepted statements. Digits may appear only where the original text already had them
   (step numbers, "01", slide counters). Big KPI slots become short concept words instead of numbers.
5. Add sensible caveats where relevant (e.g. "not investment advice", "varies by market") instead of promises.
6. Every paragraph must stay within its max_chars. Same number of paragraphs per textbox, same order.
7. Keep leading bullets / emoji / symbols exactly as in the original text.
8. locked=true paragraphs (footers, slide counters, brand lines, empty lines) must be returned UNCHANGED.
9. A line like "Audience: ... Tone: ..." is prompt leakage: replace it with a short, natural subtitle
   (e.g. "A practical, educational guide"). Never output the words "Audience" or "Tone" as labels.
10. Do not repeat the same sentence on two slides. If a slide repeats blocks that also appear on another slide,
    write distinct, topic-relevant content for each.
11. Plain text only. No markdown, no quotes around text, no numbering you were not given."""


def build_plan_prompt(topic: str, tone: str, slides: list) -> str:
    lines = []
    for s in slides:
        payload = slide_texts_digest(s)
        lines.append(f"- slide {s.get('slide_number')}: current title = {s.get('title', '')!r}; "
                     f"layout hint (existing slot texts) = {payload}")
    layout = '\n'.join(lines)
    return f"""You are planning a content refresh of a {len(slides)}-slide presentation about: {topic}.
Tone: {tone}. The current text is generic template filler; the visual layout must stay as is.

For each slide, decide what accurate topic content it should carry, so the whole deck reads as one logical
teaching sequence (slide 1 = title, last slide = recap / closing). Respect each slide's layout hint
(number of items, kind of boxes).

{layout}

Return ONLY JSON:
{{"slides":[{{"slide_number":1,"title":"new slide title","purpose":"one line","points":["short idea","short idea"]}}]}}
Do not invent statistics, people or companies."""


def build_refresh_prompt(chunk: list, topic: str, tone: str, plan_by_slide: dict,
                         outline: str, total_slides: int) -> str:
    import json
    payload = {'slides': [slide_payload(s) for s in chunk]}
    plan_lines = []
    for s in chunk:
        p = plan_by_slide.get(s.get('slide_number'))
        if p:
            plan_lines.append(f"slide {s.get('slide_number')}: title={p.get('title')!r}; "
                              f"purpose={p.get('purpose')!r}; points={p.get('points')}")
    plan_text = '\n'.join(plan_lines) if plan_lines else '(no plan available - follow the deck outline)'
    return f"""You are a subject-matter editor refreshing a {total_slides}-slide presentation about: {topic}.
Writing tone: {tone}. The draft text is generic template filler. Rewrite the TEXT ONLY so it becomes accurate,
specific, useful content about the topic. The visual design and shapes are fixed; you only fill the existing slots.

DECK OUTLINE (current titles):
{outline}

PLAN FOR THE SLIDES IN THIS REQUEST:
{plan_text}

{_RULES}

OUTPUT: ONLY JSON, same structure as the input, but each paragraph is a plain string:
{{"slides":[{{"slide_number":1,"textboxes":[{{"shape_index":0,"paragraphs":["text","text"]}}]}}]}}

### INPUT
{json.dumps(payload, ensure_ascii=False)}
### END INPUT"""


def index_result(result) -> dict:
    """Map slide_number -> slide dict from a provider result."""
    if isinstance(result, dict):
        slides = result.get('slides')
    elif isinstance(result, list):
        slides = result
    else:
        slides = None
    out = {}
    for s in slides or []:
        if isinstance(s, dict):
            try:
                out[int(s.get('slide_number'))] = s
            except (TypeError, ValueError):
                continue
    return out


def index_plan(result) -> dict:
    return index_result(result)


# ── Guard ───────────────────────────────────────────────────────────────────
def guard_refresh_paragraph(original: str, new) -> tuple:
    """Validate one refreshed paragraph. Returns (final_text, reverted)."""
    new_text = _ptext(new).strip()

    if not original.strip():
        return original, False
    if not new_text:
        return original, True
    if new_text == original.strip():
        return original, False

    # Footers / slide counters / brand lines: spelling-level changes only.
    if is_locked(original):
        same_len = len(original.split()) == len(new_text.split())
        sim = difflib.SequenceMatcher(None, original.lower(), new_text.lower()).ratio()
        if same_len and sim >= _LOCKED_MIN_SIMILARITY and _numbers(original) == _numbers(new_text):
            return new_text, False
        return original, True

    # No invented numbers: numbers may disappear, never appear.
    if _numbers(new_text) - _numbers(original):
        return original, True

    # Must still fit the shape.
    if len(new_text) > max_chars_for(original):
        return original, True
    orig_words = len(original.split())
    if orig_words >= 6 and len(new_text.split()) < orig_words * 0.3:
        return original, True

    marker = _leading_marker(original)
    if marker and not new_text.lstrip().startswith(marker):
        new_text = f'{marker} {new_text.lstrip()}'
    return new_text, False
