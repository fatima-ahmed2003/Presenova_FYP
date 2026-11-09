"""
Centralized Prompt Templates for the AI Presentation Enhancement Engine.

All prompts are defined here to keep them consistent, auditable, and
easy to modify across processing modes and tones.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Processing Modes ─────────────────────────────────────────────────────────
# Each mode defines a distinct prompt personality and quality target.
MODE_SYSTEM_INSTRUCTIONS = {
    "quick": (
        "You are a helpful presentation text improver. Make quick, conservative "
        "improvements to grammar, spelling, and clarity. Do not restructure content. "
        "Return valid JSON only."
    ),
    "professional": (
        "You are an Expert AI Presentation Enhancement Engine, Professional "
        "Presentation Designer, Technical Writer, and Communication Coach. "
        "Improve text to be concise, scannable, professional, and presentation-friendly."
    ),
    "academic": (
        "You are an Expert Academic Presentation Consultant. Improve the text "
        "for academic rigor, precision, and formal tone while keeping it readable "
        "from a stage. Preserve all technical terms, citations, and data."
    ),
}

# ── Tone Instructions ────────────────────────────────────────────────────────
TONE_INSTRUCTIONS = {
    "professional": "Use a professional, confident, and authoritative tone.",
    "academic": "Use a formal, precise, and scholarly tone suitable for academic audiences.",
    "business": "Use a corporate, results-oriented tone with clear business language.",
    "technical": "Use a precise technical tone; preserve all technical details and terminology.",
    "executive": "Use a concise, high-level tone suitable for executive briefings.",
    "marketing": "Use a persuasive, benefit-focused tone suitable for marketing contexts.",
    "formal": "Use a strictly formal tone; avoid all colloquialisms and contractions.",
    "simple_english": "Use plain, simple English suitable for a broad, non-specialist audience.",
}

DEFAULT_TONE = "professional"


# ── Small helpers ────────────────────────────────────────────────────────────

def _para_text(p) -> str:
    """Text of a paragraph that may be a dict ({'text': ...}) or a plain string."""
    if isinstance(p, dict):
        return str(p.get("text", ""))
    return str(p) if p is not None else ""


def _slide_text(slide: dict, limit: int = 700) -> str:
    """Flatten all textbox paragraphs of a slide into one short string."""
    parts = []
    for tb in slide.get("textboxes", []) or []:
        for p in tb.get("paragraphs", []) or []:
            text = _para_text(p).strip()
            if text:
                parts.append(text)
    return " / ".join(parts)[:limit]


def _slides_digest(slides: list[dict], limit: int = 700) -> str:
    """Readable per-slide text digest used by scoring / validation prompts."""
    lines = []
    for s in slides:
        lines.append(
            f'Slide {s.get("slide_number")} | {json.dumps(s.get("title", ""), ensure_ascii=False)} | '
            f'{json.dumps(_slide_text(s, limit), ensure_ascii=False)}'
        )
    return "\n".join(lines)


# ── Build quality analysis prompt ────────────────────────────────────────────

def build_quality_analysis_prompt(
    text: str,
    filename: str = "",
    mode: str = "professional",
    grammar_issues: list | None = None,
    grammar_score: int | None = None,
) -> str:
    """Build a prompt for evaluating the quality of presentation text.

    Accepts optional LanguageTool grammar pre-analysis results to calibrate
    the Grammar, Spelling, and 'Correct' 7Cs scores accurately.

    Returns a prompt asking for a structured JSON scorecard.
    """
    mode_instruction = MODE_SYSTEM_INSTRUCTIONS.get(mode, MODE_SYSTEM_INSTRUCTIONS["professional"])

    # Inject grammar context block if LanguageTool data is available
    grammar_block = ""
    if grammar_issues is not None:
        from services.language_tool_service import summarise_grammar_issues
        summary = summarise_grammar_issues(grammar_issues)
        _score = grammar_score if grammar_score is not None else 100
        grammar_block = f"""
=== GRAMMAR PRE-ANALYSIS (LanguageTool Cloud) ===
Grammar Quality Score: {_score}/100
Total Issues Detected: {len(grammar_issues)}
{summary if summary else 'No grammar issues detected — text appears grammatically clean.'}
IMPORTANT: Use the above grammar score to SET the 'Grammar' and 'Spelling' category scores
and the 'Correct' 7Cs score. Do NOT override these with your own estimate.
=== END GRAMMAR PRE-ANALYSIS ===
"""

    return f"""You are a {mode_instruction}.

Analyse the following presentation text and return a detailed quality scorecard as JSON.{grammar_block}
=== SCORING CRITERIA ===
Score each category from 0–100 where 100 is perfect:
1. **Grammar** — correct grammar, no errors
2. **Spelling** — no spelling mistakes (visible typos MUST lower this score)
3. **Readability** — clear, easy to understand; appropriate sentence length
4. **Tone** — professional, audience-appropriate tone
5. **Clarity** — clear and unambiguous messaging
6. **Conciseness** — no unnecessary words or repetition
7. **Structure** — logical organisation, good bullet use

Also score the 7 Cs of Communication (0–100 each):
Clear, Concise, Correct, Complete, Concrete, Coherent, Courteous

=== HONESTY RULES ===
- Base EVERY score and sentence on the actual text below. Do not use generic or template wording.
- Each "seven_cs_evaluation" sentence must point to something specific in this text.
- "strengths" and "issues_found" must quote or name real parts of the text (typos, long sentences, missing data).
- Do not return round placeholder numbers; scores must reflect the text.

=== JSON SCHEMA (replace every <...> with real content) ===
{{
    "overall_score": <integer 0-100>,
    "grade": "<A|B|C|D|F>",
    "summary": "<1-2 sentence summary specific to this presentation>",
    "category_scores": {{
        "Grammar": <integer 0-100>,
        "Spelling": <integer 0-100>,
        "Readability": <integer 0-100>,
        "Tone": <integer 0-100>,
        "Clarity": <integer 0-100>,
        "Conciseness": <integer 0-100>,
        "Structure": <integer 0-100>
    }},
    "seven_cs_scores": {{
        "Clear": <integer 0-100>,
        "Concise": <integer 0-100>,
        "Correct": <integer 0-100>,
        "Complete": <integer 0-100>,
        "Concrete": <integer 0-100>,
        "Coherent": <integer 0-100>,
        "Courteous": <integer 0-100>
    }},
    "seven_cs_evaluation": {{
        "Clear": "<specific evaluation>",
        "Concise": "<specific evaluation>",
        "Correct": "<specific evaluation>",
        "Complete": "<specific evaluation>",
        "Concrete": "<specific evaluation>",
        "Coherent": "<specific evaluation>",
        "Courteous": "<specific evaluation>"
    }},
    "strengths": ["<specific strength>", "<specific strength>"],
    "issues_found": ["<specific issue>", "<specific issue>"],
    "recommendations": ["<actionable recommendation>", "<actionable recommendation>"]
}}

Return ONLY valid JSON. No markdown, no code fences.

=== FILE ===
{filename}

=== TEXT ===
{text[:120000]}
"""


# ── Build rewrite prompt ─────────────────────────────────────────────────────
def build_rewrite_prompt(
    slides: list[dict],
    grammar_issues_summary: str = "",
    presentation_context: Optional[dict] = None,
    mode: str = "professional",
    tone: str = "professional",
    focus_items: Optional[list[str]] = None,
) -> str:
    """Build a comprehensive prompt for rewriting presentation text.

    Incorporates processing mode, tone, presentation context, grammar issues,
    and pre-analysis recommendations (focus_items). Contains strict
    preservation rules so the model only polishes the user's own text.
    """
    mode_system = MODE_SYSTEM_INSTRUCTIONS.get(mode, MODE_SYSTEM_INSTRUCTIONS["professional"])
    tone_instruction = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["professional"])

    # Build slide blocks
    slide_blocks = []
    for slide in slides:
        slide_type = slide.get('_inferred_type', 'Content Slide')
        target_lines = [f'  Slide role: {slide_type}']

        for textbox in slide.get('textboxes', []):
            paragraphs = ' | '.join(
                f'[P{p.get("para_index", i) if isinstance(p, dict) else i}]: '
                f'{json.dumps(_para_text(p), ensure_ascii=False)}'
                for i, p in enumerate(textbox.get('paragraphs', []))
            )
            target_lines.append(
                f'  Textbox shape_index={textbox.get("shape_index")} '
                f'({len(textbox.get("paragraphs", []))} paragraphs): {paragraphs}'
            )

        for table in slide.get('tables_data', []):
            for cell in table.get('cells', []):
                paragraphs = ' | '.join(
                    f'[P{i}]: {json.dumps(_para_text(p), ensure_ascii=False)}'
                    for i, p in enumerate(cell.get('paragraphs', []))
                )
                target_lines.append(
                    f'  Table shape_index={table.get("shape_index")} '
                    f'cell=({cell.get("row_index")},{cell.get("column_index")}): {paragraphs}'
                )

        for chart in slide.get('charts_data', []):
            if chart.get('title_paragraphs'):
                paragraphs = ' | '.join(
                    f'[P{i}]: {json.dumps(_para_text(p), ensure_ascii=False)}'
                    for i, p in enumerate(chart.get('title_paragraphs', []))
                )
                target_lines.append(f'  Chart shape_index={chart.get("shape_index")} title: {paragraphs}')

        slide_blocks.append(
            f'Slide {slide.get("slide_number")} — Title: {json.dumps(slide.get("title", ""), ensure_ascii=False)}\n'
            + ('\n'.join(target_lines) if target_lines else '  (no text targets)')
        )

    grammar = f'\nGrammar issues to fix:\n{grammar_issues_summary}\n' if grammar_issues_summary else ''
    focus_block = ''
    if focus_items:
        items = '\n'.join(f'- {item}' for item in focus_items)
        focus_block = f'''
=== ISSUES TO ADDRESS (only where they apply, and only by editing existing wording) ===
{items}
'''
    context_block = ''

    if presentation_context:
        themes = ', '.join(presentation_context.get('key_themes', [])) or 'N/A'
        context_block = f'''
=== PRESENTATION CONTEXT (background only — NEVER copy its wording into the slides) ===
• Topic: {presentation_context.get('overall_topic', 'N/A')}
• Objective: {presentation_context.get('main_objective', 'N/A')}
• Type: {presentation_context.get('presentation_type', 'N/A')}
• Level: {presentation_context.get('technical_level', 'N/A')}
• Tone guidance: {presentation_context.get('tone_guidance', 'N/A')}
• Key themes: {themes}
'''

    return f'''{mode_system}

=== TONE ===
{tone_instruction}

=== OBJECTIVE ===
Polish the wording of each slide (spelling, grammar, clarity, concision, tone) so it is
scannable and easy to explain verbally — while keeping 100% of the original meaning, facts
and visual structure. You are EDITING the author's text, not writing new content.
{context_block}
=== RULES ===
1. Return **only valid JSON**. No markdown, no code fences, no introductory explanations.
2. Every slide and textbox listed below must appear in the output with the SAME shape_index.
3. Keep each paragraph array **exactly the same length** as the input array (same number of paragraphs).
4. NEVER use placeholders like "..." or dummy text. Each output paragraph is a real rewritten
   version of the matching input paragraph.
5. PRESERVE exactly: facts, names, dates, URLs, formulas, technical meaning, and ALL numbers and
   percentages (copy every digit exactly; do not add, remove or change any number).
6. NEVER ADD NEW CLAIMS. Do not introduce products, industries, metrics, benefits, safety/compliance
   claims or domain words that are not already in that paragraph (e.g. do not add "fleet", "edge",
   "safety", "enterprise" unless the original says so). If unsure, keep the original wording.
7. DO NOT REWRITE branding or navigation text: footers, slide counters (e.g. "02 / 05"), lines starting
   with "PRESENOVA AI", and lines like "Audience: ... Tone: ...". In these lines you may ONLY fix a
   misspelled word; otherwise return them exactly unchanged. Never change the stated audience or tone.
8. KEEP leading markers and structure: bullets, symbols, emoji, numbering ("01.", "A") and
   "Label: description" patterns must stay as in the original.
9. Fix misspelled words consistently everywhere (for example a misspelled topic name) — spelling only.
10. If a paragraph is already correct and clear, return it UNCHANGED. Fewer edits are better than risky edits.
11. Do not make a paragraph more than about 30% longer than the original.
12. Titles: keep the meaning, make them clear and concise.
13. Use parallel grammatical structure for bullet points of the same textbox.
14. Remove weak phrases ("In this section", "As you can see", "It is important to note") when they appear.
15. Never add, remove or reorder slides, shapes, textboxes, tables, charts, or cells.
16. NEVER change a correct word into another form of it: do not split or merge compounds (e.g. "speedup" stays "speedup"), and do not switch noun/adjective forms (e.g. "Architectural" stays "Architectural"). Change a word only when it is a genuine spelling or grammar error.

=== JSON SCHEMA EXAMPLE (do NOT copy literal text, write the actual rewritten paragraphs) ===
{{"slides":[{{"slide_number":1,"textboxes":[{{"shape_index":0,"paragraphs":["<rewritten paragraph 1>","<rewritten paragraph 2>"]}}]}}]}}
{grammar}{focus_block}
=== SLIDE DATA ===
{chr(10).join(slide_blocks)}
'''


# ── Build presentation context prompt ────────────────────────────────────────

def build_presentation_context_prompt(slides: list[dict]) -> str:
    """Build a prompt for holistic presentation context analysis.

    Phase 1 of the analysis pipeline: understand the presentation as a whole
    before improving individual slides.
    """
    slide_summaries = []
    for slide in slides:
        title = slide.get('title', '')
        full_text = ' '.join(
            _para_text(p)
            for tb in slide.get('textboxes', [])
            for p in tb.get('paragraphs', [])
        )[:300]
        slide_summaries.append(
            f'Slide {slide.get("slide_number")}: Title={json.dumps(title, ensure_ascii=False)} | '
            f'Content={json.dumps(full_text, ensure_ascii=False)}'
        )

    return f'''Analyse the COMPLETE presentation below and return a compact JSON summary.

Determine:
- overall_topic — the single main subject
- main_objective — what the presenter wants to achieve
- presentation_type — e.g. thesis defense, sales pitch, training
- technical_level — beginner | intermediate | advanced | academic
- audience — the audience ONLY if the slides state it; otherwise "not stated"
- story_flow — 1-sentence narrative arc
- key_themes — 3-5 recurring themes
- tone_guidance — 1 sentence on appropriate rewrite tone
- slide_roles — mapping of slide_number → role label

JSON SCHEMA:
{{"overall_topic":"<text>","main_objective":"<text>","presentation_type":"<text>","technical_level":"<text>","audience":"<text>","story_flow":"<text>","key_themes":["<text>"],"tone_guidance":"<text>","slide_roles":{{"1":"<role>"}}}}

Return ONLY valid JSON.

PRESENTATION DATA:
{chr(10).join(slide_summaries)}
'''


# ── Build validation prompt ─────────────────────────────────────────────────

def build_validation_prompt(
    original_slides: list[dict],
    rewritten_slides: list[dict],
) -> str:
    """Build a prompt for validating rewritten slides against originals.

    Compares the actual text (not just counts) to detect semantic drift,
    invented claims, changed numbers, changed audience and altered footers.
    """
    return f'''Compare the original and rewritten slides below. Verify that:
1. All facts, numbers, percentages, names and dates are preserved exactly.
2. No new claims, products, industries or hallucinated information were added.
3. The meaning and intent of each slide is unchanged (including the stated audience and tone).
4. Footers, slide counters and brand lines were not rewritten (spelling fixes only).
5. All slides, textboxes, and paragraphs are accounted for.

Return JSON: {{"valid":<true|false>,"issues":["<issue>"],"slide_issues":[{{"slide_number":<int>,"issue":"<text>","severity":"minor|major"}}]}}
If everything is fine return {{"valid":true,"issues":[],"slide_issues":[]}}.

ORIGINAL (slide | title | text):
{_slides_digest(original_slides)}

REWRITTEN (slide | title | text):
{_slides_digest(rewritten_slides)}
'''


# ── Build scoring prompt ────────────────────────────────────────────────────

def build_scoring_prompt(
    original_slides: list[dict],
    rewritten_slides: list[dict],
    analysis_results: Optional[dict] = None,
) -> str:
    """Build a prompt for post-rewrite scoring and quality assessment.

    The prompt includes the actual slide text of both versions so scores are
    grounded in content (not just titles).
    """
    analysis_block = ''
    if analysis_results:
        analysis_block = f'\nPre-rewrite analysis:\n{json.dumps(analysis_results, indent=2)}\n'

    return f'''You are a Presentation Quality Assessor. Evaluate the ORIGINAL vs IMPROVED presentation text.

Score each category 0-100 for both original and improved, and compute the delta (improved minus original).
Also give a confidence score (0-100) per category reflecting how sure you are the text really changed for the better.

RULES:
- Judge only the text shown below. Do not assume improvements that are not visible.
- If a category's text is identical in both versions, its delta MUST be 0.
- Do not inflate the improved score; if the rewrite added unsupported claims or changed facts, lower the score.

Categories: Grammar, Spelling, Readability, Tone, Clarity, Conciseness, Structure
Also score the 7 Cs: Clear, Concise, Correct, Complete, Concrete, Coherent, Courteous{analysis_block}

JSON SCHEMA (replace every <...>):
{{"original_scores":{{"Grammar":<int>,"Spelling":<int>,"Readability":<int>,"Tone":<int>,"Clarity":<int>,"Conciseness":<int>,"Structure":<int>}},
"improved_scores":{{"Grammar":<int>,"Spelling":<int>,"Readability":<int>,"Tone":<int>,"Clarity":<int>,"Conciseness":<int>,"Structure":<int>}},
"delta":{{"Grammar":<int>,"Spelling":<int>,"Readability":<int>,"Tone":<int>,"Clarity":<int>,"Conciseness":<int>,"Structure":<int>}},
"confidence":{{"Grammar":<int>,"Spelling":<int>,"Readability":<int>,"Tone":<int>,"Clarity":<int>,"Conciseness":<int>,"Structure":<int>}},
"overall_original":<int>,"overall_improved":<int>,"grade":"<A|B|C|D|F>"}}

Return ONLY valid JSON.

ORIGINAL slides (slide | title | text):
{_slides_digest(original_slides)}

IMPROVED slides (slide | title | text):
{_slides_digest(rewritten_slides)}
'''


# ── Build narrative analysis prompt ─────────────────────────────────────────

def build_narrative_analysis_prompt(slides: list[dict]) -> str:
    """Build a prompt for analyzing the narrative/storytelling quality."""
    slide_texts = []
    for slide in slides:
        text = ' '.join(
            _para_text(p)
            for tb in slide.get('textboxes', [])
            for p in tb.get('paragraphs', [])
        )
        slide_texts.append(f'Slide {slide.get("slide_number")}: {slide.get("title")} — {text[:500]}')

    return f'''Analyse this presentation's storytelling and narrative quality.

Evaluate:
- Hook strength (does the opening grab attention?)
- Narrative arc (clear beginning, middle, end?)
- Pacing (do slides flow at the right speed?)
- Transitions (are there logical bridges between slides?)
- Conclusion impact (does the ending satisfy?)
- Missing sections (hook? problem? solution? evidence? conclusion? CTA?)

Base all scores on the slides below; do not use generic numbers.

Return JSON:
{{"hook_score":<int>,"narrative_score":<int>,"pacing_score":<int>,"conclusion_score":<int>,"transitions_score":<int>,"overall_storytelling_score":<int>,"weakest_transition":"<text>","strongest_transition":"<text>","missing_sections":["<text>"],"suggested_order":[<slide numbers>],"feedback":"<text>"}}

Return ONLY valid JSON.

PRESENTATION:
{chr(10).join(slide_texts)}
'''


# ── Build Viva Question Generation Prompt ─────────────────────────────────

def build_question_generation_prompt(
    text: str,
    presentation_context: Optional[dict] = None,
    num_questions: int = 10,
) -> str:
    """Build a prompt for generating viva/thesis-defense style questions.

    The prompt instructs the AI to generate questions grounded in the actual
    uploaded content, covering conceptual, justification, critical, evidence,
    and future categories. Returns valid JSON only.

    Args:
        text: The extracted text from the uploaded document.
        presentation_context: Optional dict with topic, objective, type, etc.
        num_questions: Desired number of questions to generate.

    Returns:
        A complete prompt string for the AI provider.
    """
    context_block = ""
    if presentation_context:
        themes = ", ".join(presentation_context.get("key_themes", [])) or "N/A"
        context_block = f"""
=== PRESENTATION CONTEXT ===
• Topic: {presentation_context.get("overall_topic", "N/A")}
• Objective: {presentation_context.get("main_objective", "N/A")}
• Type: {presentation_context.get("presentation_type", "N/A")}
• Level: {presentation_context.get("technical_level", "N/A")}
• Audience: {presentation_context.get("audience", "N/A")}
• Key themes: {themes}
"""

    return f"""You are an Expert Academic Viva Voce Examiner. Your role is to generate
high-quality, defense-style oral examination questions that are deeply grounded
in the specific content of the uploaded document. Every question must reference
actual claims, data, diagrams, or sections from the provided text.

=== OBJECTIVE ===
Generate {num_questions} viva/thesis-defense style questions that test deep
understanding of the material. Questions must cover a MIX of these five categories:

1. **Conceptual** — Tests understanding of core concepts, definitions, and theories.
2. **Justification** — Asks the student to justify design choices, methodology, or conclusions.
3. **Critical** — Challenges assumptions, limitations, or alternative perspectives.
4. **Evidence** — Asks for supporting data, citations, or empirical backing.
5. **Future** — Explores future work, extensions, open questions, or real-world impact.

=== RULES ===
1. Every question MUST be directly grounded in the uploaded document content.
   Do NOT generate generic questions that could apply to any document.
2. Distribute questions across all five categories — no category should dominate.
3. Mix difficulty levels: basic, intermediate, and advanced.
4. Each question must include a specific source_reference pointing to which part
   of the document the question is based on (e.g. "Slide 3 — mentions X",
   "Section 2.3 — discusses Y", "Figure 1 — shows Z").
5. For prep_tip, provide 2-3 bullet points of what a STRONG answer should cover.
   These are preparation hints, NOT the full answer.
6. If the document content is too short or thin to generate {num_questions}
   quality distinct questions, generate FEWER questions (as few as needed) rather
   than repeating similar or generic questions.{context_block}
=== JSON SCHEMA ===
{{
  "questions": [
    {{
      "id": "q1",
      "category": "conceptual|justification|critical|evidence|future",
      "difficulty": "basic|intermediate|advanced",
      "question": "Your question here?",
      "source_reference": "e.g. Slide 3 / Section 2.1 — mentions X",
      "prep_tip": "- Point 1\\n- Point 2\\n- Point 3"
    }}
  ],
  "summary": {{
    "total_questions": 10,
    "by_difficulty": {{"basic": 3, "intermediate": 4, "advanced": 3}},
    "focus_areas": ["List of 2-4 key topics the questions cover"]
  }}
}}

Return ONLY valid JSON — no markdown, no explanations outside the JSON.

=== DOCUMENT TEXT ===
{text[:120000]}
"""


def get_system_prompt(mode: str = "professional") -> str:
    """Get the system-level instruction for a given mode."""
    return MODE_SYSTEM_INSTRUCTIONS.get(mode, MODE_SYSTEM_INSTRUCTIONS["professional"])


def get_tone_instruction(tone: str = "professional") -> str:
    """Get the tone instruction string for a given tone."""
    return TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["professional"])


__all__ = [
    "build_quality_analysis_prompt",
    "build_rewrite_prompt",
    "build_presentation_context_prompt",
    "build_validation_prompt",
    "build_scoring_prompt",
    "build_narrative_analysis_prompt",
    "build_question_generation_prompt",
    "get_system_prompt",
    "get_tone_instruction",
    "MODE_SYSTEM_INSTRUCTIONS",
    "TONE_INSTRUCTIONS",
]