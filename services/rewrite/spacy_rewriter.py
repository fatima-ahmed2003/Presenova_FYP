"""
spaCy Rule-Based Slide Rewriter Engine (Local / Deterministic)

Performs text enhancement, passive-to-active voice conversion,
conciseness filler phrase substitution, and 6x6 bullet splitting.
"""

import logging
import re
import sys
import os
from typing import List, Dict, Any, Optional

_custom_site = os.getenv("CUSTOM_SITE_PACKAGES", "")
if _custom_site and os.path.isdir(_custom_site) and _custom_site not in sys.path:
    sys.path.insert(0, _custom_site)

logger = logging.getLogger(__name__)

_SPACY_NLP = None


def _load_spacy():
    global _SPACY_NLP
    if _SPACY_NLP is None:
        try:
            import spacy
            try:
                _SPACY_NLP = spacy.load("en_core_web_sm")
                logger.info("[spacy_rewriter] Loaded spaCy model en_core_web_sm")
            except Exception:
                _SPACY_NLP = spacy.blank("en")
                logger.info("[spacy_rewriter] Fallback to spacy.blank('en')")
        except Exception as exc:
            logger.warning("[spacy_rewriter] Could not load spaCy model: %s", exc)
            _SPACY_NLP = False
    return _SPACY_NLP if _SPACY_NLP is not False else None


# Comprehensive Filler & Verbose Phrase Lookup Dictionary
FILLER_SUBSTITUTIONS = {
    # Verbose prepositions & conjunctions
    r"\bin order to\b": "to",
    r"\bdue to the fact that\b": "because",
    r"\bat this point in time\b": "now",
    r"\bat the present time\b": "currently",
    r"\ba large number of\b": "many",
    r"\ba variety of\b": "various",
    r"\btake into consideration\b": "consider",
    r"\buntil such time as\b": "until",
    r"\bfor the purpose of\b": "for",
    r"\bin the event that\b": "if",
    r"\bwith reference to\b": "about",
    r"\bwith regard to\b": "regarding",
    r"\bin relation to\b": "regarding",
    r"\bhas the capability to\b": "can",
    r"\bhas the ability to\b": "can",
    r"\bis of the opinion that\b": "believes",
    r"\bmake a decision\b": "decide",
    r"\bmake an adjustment\b": "adjust",
    r"\bconduct an analysis of\b": "analyze",
    r"\bprovide an explanation of\b": "explain",
    r"\bgive consideration to\b": "consider",
    r"\bcome to a conclusion\b": "conclude",
    r"\bin close proximity to\b": "near",
    r"\ba majority of\b": "most",
    r"\ba significant amount of\b": "substantial",
    r"\bin spite of the fact that\b": "although",
    r"\bfor the reason that\b": "because",
    r"\bin accordance with\b": "per",
    r"\bprior to\b": "before",
    r"\bsubsequent to\b": "after",
    r"\bin the course of\b": "during",
    r"\bfirst and foremost\b": "first",
    r"\blast but not least\b": "finally",
    r"\bit is important to note that\b": "note that",
    r"\bit should be noted that\b": "notably,",
    r"\bas a matter of fact\b": "in fact,",
    r"\bneedless to say\b": "clearly,",
    r"\ball things considered\b": "overall,",
    r"\bat the end of the day\b": "ultimately,",
}


def substitute_filler_phrases(text: str) -> str:
    """Substitute verbose/filler phrases with concise alternatives."""
    if not text:
        return ""
    result = text
    for pattern, replacement in FILLER_SUBSTITUTIONS.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    # Clean up double spaces
    result = re.sub(r'\s{2,}', ' ', result).strip()
    return result


def convert_passive_to_active(sentence: str) -> str:
    """Detect passive voice (nsubjpass) via spaCy and restructure to active voice."""
    if not sentence or len(sentence.split()) < 4:
        return sentence

    nlp = _load_spacy()
    if not nlp:
        # Simple regex heuristics for passive voice
        sentence = re.sub(r'\bis utilized by\b', 'uses', sentence, flags=re.IGNORECASE)
        sentence = re.sub(r'\bwas performed by\b', 'performed', sentence, flags=re.IGNORECASE)
        sentence = re.sub(r'\bwere conducted by\b', 'conducted', sentence, flags=re.IGNORECASE)
        return sentence

    try:
        doc = nlp(sentence)
        nsubjpass = None
        verb = None
        agent = None

        for token in doc:
            if token.dep_ == "nsubjpass":
                nsubjpass = token.text
            elif token.dep_ == "auxpass" and token.head.pos_ == "VERB":
                verb = token.head.lemma_
            elif token.dep_ == "agent":
                agent_objs = [child.text for child in token.children if child.dep_ == "pobj"]
                if agent_objs:
                    agent = agent_objs[0]

        if nsubjpass and verb and agent:
            active = f"{agent.capitalize()} {verb}s {nsubjpass.lower()}."
            return active
    except Exception as exc:
        logger.warning("[spacy_rewriter] Passive-to-active conversion error: %s", exc)

    return sentence


def split_bullet_point(bullet: str, max_words: int = 15) -> List[str]:
    """Split bullet points exceeding max_words on conjunctions or semicolons."""
    if not bullet:
        return []
    words = bullet.split()
    if len(words) <= max_words:
        return [bullet]

    # Split on semicolon or conjunctions
    parts = re.split(r';|\b(?:and|but|however|whereas|furthermore)\b', bullet, flags=re.IGNORECASE)
    cleaned_parts = [p.strip().capitalize() for p in parts if len(p.strip()) > 5]

    if len(cleaned_parts) > 1:
        return cleaned_parts

    # Fallback to word slicing if no punctuation boundary
    half = len(words) // 2
    part1 = " ".join(words[:half]).strip().capitalize()
    part2 = " ".join(words[half:]).strip().capitalize()
    return [part1, part2]


def rewrite_text_paragraph(text: str, mode: str = "professional", tone: str = "professional") -> str:
    """Clean and polish a single paragraph or bullet line."""
    if not text or not text.strip():
        return text

    original = text.strip()
    # Step 1: Filler substitution
    refined = substitute_filler_phrases(original)

    # Step 2: Passive to active
    refined = convert_passive_to_active(refined)

    # Step 3: Capitalize first letter and ensure clean punctuation
    if refined and refined[0].islower():
        refined = refined[0].upper() + refined[1:]

    return refined


def rewrite_slide_content(slide_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Rewrite a single slide's title and bullet points using spaCy rules.
    Enforces 6x6 rule (max 6 bullets, max 15 words per bullet).
    """
    title = slide_data.get("title", "")
    bullets = slide_data.get("bullet_points", [])

    clean_title = substitute_filler_phrases(title).title()

    new_bullets = []
    for b in bullets:
        concise = substitute_filler_phrases(b)
        active = convert_passive_to_active(concise)
        split_bullets = split_bullet_point(active, max_words=15)
        new_bullets.extend(split_bullets)

    if len(new_bullets) > 6:
        new_bullets = new_bullets[:6]

    return {
        "slide_number": slide_data.get("slide_number", 1),
        "title": clean_title,
        "bullet_points": new_bullets,
        "speaker_notes": slide_data.get("speaker_notes", ""),
    }


def rewrite_slide_dict(slide: dict, mode: str = "professional", tone: str = "professional") -> dict:
    """
    Deterministically rewrite a slide dictionary (matching the shape expected by RewriteExecutor).
    Processes all textboxes and paragraphs using rule-based transformations.
    """
    slide_num = slide.get('slide_number', 1)
    textboxes_out = []

    for tb in slide.get('textboxes', []):
        shape_idx = tb.get('shape_index')
        raw_paragraphs = tb.get('paragraphs', [])
        rewritten_paras = []

        for p in raw_paragraphs:
            text = p.get('text', '') if isinstance(p, dict) else str(p)
            if not text.strip():
                rewritten_paras.append(text)
                continue

            rewritten_text = rewrite_text_paragraph(text, mode=mode, tone=tone)
            # A slide rewrite must preserve the paragraph count so the text
            # can be written back into the original styled PowerPoint runs.
            rewritten_paras.append(rewritten_text)

        textboxes_out.append({
            'shape_index': shape_idx,
            'paragraphs': rewritten_paras,
        })

    return {
        'slide_number': slide_num,
        'textboxes': textboxes_out,
        'tables': slide.get('tables', []),
        'charts': slide.get('charts', []),
        '_fallback': False,
        '_rule_based': True,
    }

