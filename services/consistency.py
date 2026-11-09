"""
Cross-slide typo-correction consistency pass (standalone, no AI calls).

Problem it solves:
    Each slide is rewritten by an independent AI call, so the SAME source typo
    (e.g. "Vehcal") can be corrected differently on different slides
    ("Vehicle" on slide 1, "Vehicles" on slide 2).

How it works:
    1. For every paragraph, diff ORIGINAL vs REWRITTEN text and find word-level
       replacements that look like typo fixes (similar spelling).
    2. For each original word, count which correction the AI chose, across all slides.
    3. If the AI chose different corrections for the same word, force the most
       common one everywhere. Untouched words are never modified.

Public API:
    harmonize_texts(pairs)                       -> (new_texts, log)
    harmonize_slides(original_slides, rewritten) -> (rewritten, log)   # in-place
"""

import difflib
import re
from collections import Counter, defaultdict
from typing import List, Tuple

_TOKEN_RE = re.compile(r"\w+|\W+", re.UNICODE)
_MIN_RATIO = 0.65   # must look like a typo fix (similar spelling)
_MIN_LEN = 4


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text or "")


def _is_word(tok: str) -> bool:
    return tok.isalpha() and len(tok) >= _MIN_LEN


def _match_case(src: str, target: str) -> str:
    if len(src) > 1 and src.isupper():
        return target.upper()
    if src[:1].isupper():
        return target.capitalize()
    return target.lower()


def _find_corrections(orig: str, new: str):
    """Returns (new_tokens, [(new_token_index, orig_word_lower, new_word_lower), ...])."""
    a, b = _tokens(orig), _tokens(new)
    sm = difflib.SequenceMatcher(
        None, [x.lower() for x in a], [x.lower() for x in b], autojunk=False
    )
    found = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != 'replace':
            continue
        used = set()
        for i in range(i1, i2):
            o = a[i]
            if not _is_word(o):
                continue
            best_j, best_r = None, 0.0
            for j in range(j1, j2):
                n = b[j]
                if j in used or not _is_word(n) or n.lower() == o.lower():
                    continue
                r = difflib.SequenceMatcher(None, o.lower(), n.lower()).ratio()
                if r > best_r:
                    best_j, best_r = j, r
            if best_j is not None and best_r >= _MIN_RATIO:
                used.add(best_j)
                found.append((best_j, o.lower(), b[best_j].lower()))
    return b, found


def harmonize_texts(
    pairs: List[Tuple[str, str]], min_votes: int = 2
) -> Tuple[List[str], List[dict]]:
    """
    pairs: [(original_text, rewritten_text), ...]
    Returns rewritten texts (same order/length) + a log of what was normalized.
    """
    analysed = []
    votes = defaultdict(Counter)
    for orig, new in pairs:
        toks, found = _find_corrections(orig, new)
        analysed.append((toks, found))
        for _, o, n in found:
            votes[o][n] += 1

    canonical = {}
    for o, counter in votes.items():
        # Only act when the AI was INCONSISTENT for this word
        if len(counter) > 1 and sum(counter.values()) >= min_votes:
            canonical[o] = counter.most_common(1)[0][0]

    out, log = [], []
    for (toks, found), (orig, new) in zip(analysed, pairs):
        changed = False
        for idx, o, n in found:
            canon = canonical.get(o)
            if canon and canon != n:
                old_tok = toks[idx]
                toks[idx] = _match_case(old_tok, canon)
                log.append({'typo': o, 'was': old_tok, 'now': toks[idx]})
                changed = True
        out.append("".join(toks) if changed else new)
    return out, log


# ── Slide-structure helper (matches rewrite_engine's slide dict shape) ─────────

def _para_text(value) -> str:
    if isinstance(value, dict):
        return str(value.get('text', ''))
    return str(value)


def harmonize_slides(original_slides: list, rewritten_slides: list, min_votes: int = 2):
    """
    Normalizes inconsistent typo corrections across ALL textboxes and table cells.
    Mutates rewritten_slides in place (paragraph str, or the 'text' of a paragraph dict)
    and returns (rewritten_slides, log).
    """
    orig_tb = {}
    orig_cells = {}
    for s in original_slides or []:
        sn = s.get('slide_number')
        for tb in s.get('textboxes', []) or []:
            orig_tb[(sn, tb.get('shape_index'))] = [
                _para_text(p) for p in tb.get('paragraphs', []) or []
            ]
        tables = s.get('tables', []) or []
        if isinstance(tables, list):
            for t in tables:
                for c in t.get('cells', []) or []:
                    orig_cells[(sn, t.get('shape_index'), c.get('row_index'), c.get('column_index'))] = [
                        _para_text(p) for p in c.get('paragraphs', []) or []
                    ]

    refs = []    # (paragraph_list, index)
    pairs = []   # (orig_text, rewritten_text)

    def _collect(paras, origs):
        if not isinstance(paras, list) or origs is None:
            return
        for i, p in enumerate(paras):
            if i >= len(origs):
                break
            pairs.append((origs[i], _para_text(p)))
            refs.append((paras, i))

    for s in rewritten_slides or []:
        sn = s.get('slide_number')
        for tb in s.get('textboxes', []) or []:
            _collect(tb.get('paragraphs'), orig_tb.get((sn, tb.get('shape_index'))))
        tables = s.get('tables', []) or []
        if isinstance(tables, list):
            for t in tables:
                for c in t.get('cells', []) or []:
                    key = (sn, t.get('shape_index'), c.get('row_index'), c.get('column_index'))
                    _collect(c.get('paragraphs'), orig_cells.get(key))

    if not pairs:
        return rewritten_slides, []

    new_texts, log = harmonize_texts(pairs, min_votes=min_votes)
    for (paras, i), (_, old_new), new in zip(refs, pairs, new_texts):
        if new == old_new:
            continue
        p = paras[i]
        if isinstance(p, dict):
            p['text'] = new
        else:
            paras[i] = new
    return rewritten_slides, log


# ── Footer / running-text consistency ──────────────────────────────────────────
# Footers repeat on every slide ("Brand | Deck title • 03 / 05"). The AI edits each
# slide independently, so one slide can end up "Vehicles" and another "Vehicle", or
# some with double spaces and others without. This pass makes them identical.

_FOOTER_RE = re.compile(r"^(?P<prefix>.+?)\s*[•·]\s*(?P<num>\d{1,3}\s*/\s*\d{1,3})\s*$")
_SEP_SPACES_RE = re.compile(r"[ \t]{2,}(?=[|•·])|(?<=[|•·])[ \t]{2,}")
_FOOTER_SIM = 0.90


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _iter_paragraph_lists(slides: list):
    """Yield (slide_number, paragraph_list) for every textbox and table cell."""
    for s in slides or []:
        sn = s.get('slide_number')
        for tb in s.get('textboxes', []) or []:
            paras = tb.get('paragraphs')
            if isinstance(paras, list):
                yield sn, paras
        tables = s.get('tables', []) or []
        if isinstance(tables, list):
            for t in tables:
                for c in t.get('cells', []) or []:
                    paras = c.get('paragraphs')
                    if isinstance(paras, list):
                        yield sn, paras


def _set_para(paras: list, i: int, text: str) -> None:
    if isinstance(paras[i], dict):
        paras[i]['text'] = text
    else:
        paras[i] = text


def harmonize_footers(rewritten_slides: list):
    """
    1. Footers that differ only by a small spelling variant (Vehicle/Vehicles) are
       made identical, choosing the variant that matches the title slide, then the
       majority, then the earliest slide.
    2. The same correction is applied to other end-of-segment occurrences of that
       phrase (e.g. an un-numbered closing-slide brand line).
    3. Runs of spaces around '|' and '•' separators are collapsed to one space.
    Mutates rewritten_slides in place. Returns (rewritten_slides, log).
    """
    log = []

    # collect numbered footers
    entries = []  # dict(slide, paras, idx, prefix)
    for sn, paras in _iter_paragraph_lists(rewritten_slides):
        for i, p in enumerate(paras):
            m = _FOOTER_RE.match(_para_text(p).strip())
            if m:
                entries.append({'slide': sn if sn is not None else 0, 'paras': paras,
                                'idx': i, 'prefix': _ws(m.group('prefix'))})

    # title-slide vocabulary = tie-breaker source of truth
    title_words = set()
    slide_nums = [s.get('slide_number') for s in rewritten_slides or [] if s.get('slide_number') is not None]
    if slide_nums:
        first = min(slide_nums)
        for s in rewritten_slides:
            if s.get('slide_number') == first:
                for tb in s.get('textboxes', []) or []:
                    for p in tb.get('paragraphs', []) or []:
                        title_words.update(w.lower() for w in re.findall(r"[^\W\d_]{4,}", _para_text(p)))

    # group by fuzzy-equal prefix
    groups = []
    for e in entries:
        for g in groups:
            if difflib.SequenceMatcher(None, e['prefix'].lower(), g[0]['prefix'].lower()).ratio() >= _FOOTER_SIM:
                g.append(e)
                break
        else:
            groups.append([e])

    phrase_fixes = {}  # variant_phrase_tokens(tuple) -> target tokens (list)
    for g in groups:
        variants = Counter(e['prefix'] for e in g)
        if len(variants) < 2:
            continue
        first_slide = {v: min(e['slide'] for e in g if e['prefix'] == v) for v in variants}

        def _score(v):
            words = {w.lower() for w in re.findall(r"[^\W\d_]{4,}", v)}
            return (len(words & title_words), variants[v], -first_slide[v])

        canonical = max(variants, key=_score)
        c_tok = canonical.split(' ')
        for v in variants:
            if v == canonical:
                continue
            v_tok = v.split(' ')
            sm = difflib.SequenceMatcher(None, v_tok, c_tok, autojunk=False)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == 'replace' and (i2 - i1) == 1 and (j2 - j1) == 1:
                    ratio = difflib.SequenceMatcher(None, v_tok[i1].lower(), c_tok[j1].lower()).ratio()
                    if ratio >= 0.7:
                        start = max(0, i1 - 2)
                        key = tuple(v_tok[start:i1 + 1])
                        phrase_fixes[key] = v_tok[start:i1] + [c_tok[j1]]

    # apply phrase fixes everywhere (end-of-segment context only)
    for key, target in phrase_fixes.items():
        pattern = re.compile(
            r"(?<!\w)" + r"\s+".join(re.escape(t) for t in key) + r"(?=\s*(?:[|•·]|$))",
            re.IGNORECASE,
        )

        def _sub(m, target=target):
            got = m.group(0).split()
            if len(got) == len(target):
                return " ".join(_match_case(g, t) if g.lower() != t.lower() else g
                                for g, t in zip(got, target))
            return " ".join(target)

        for _, paras in _iter_paragraph_lists(rewritten_slides):
            for i, p in enumerate(paras):
                old = _para_text(p)
                new = pattern.sub(_sub, old)
                if new != old:
                    _set_para(paras, i, new)
                    log.append({'was': old, 'now': new})

    # collapse space runs around separators (footers / brand lines)
    for _, paras in _iter_paragraph_lists(rewritten_slides):
        for i, p in enumerate(paras):
            old = _para_text(p)
            if '|' in old or '•' in old or '·' in old:
                new = _SEP_SPACES_RE.sub(' ', old)
                if new != old:
                    _set_para(paras, i, new)

    return rewritten_slides, log