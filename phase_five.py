"""
Phase Five: AI Coach / Practice Mode Blueprint
Role: Provide real-time AI coaching as a live practice partner.

Key Features:
- Gemini-powered coaching (short replies, one question per turn)
- Groq as backup LLM when Gemini is busy (503) or unavailable
- Honest "AI busy" message instead of fake canned answers
- Grammar analysis via LanguageTool pre-pass
- Context injection from document/speech analysis reports
"""

import os
import logging
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from services.rate_limiter import rate_limit
from datetime import datetime
from dotenv import load_dotenv
from services.language_tool_service import (
    check_grammar,
    summarise_grammar_issues,
    grammar_score as lt_grammar_score,
)

logger = logging.getLogger(__name__)

load_dotenv()

phase_five_bp = Blueprint('phase_five', __name__, url_prefix='/api')


# ─────────────────────────────────────────────────────────────
# Helper: extract readable context from the analysis report
# ─────────────────────────────────────────────────────────────
def _build_context_text(context_report: dict) -> str:
    """Turn analysis report dict into a compact plain-text summary for the prompt."""
    if not context_report:
        return "No analysis report loaded (free practice session). Do not invent scores or metrics."

    # Frontend sends a placeholder report when no real analysis exists.
    # Do not let the AI treat placeholder numbers as real.
    if context_report.get('phase') == 'practice' and not context_report.get('v1Analysis'):
        return "No analysis report loaded (free practice session). Do not invent scores or metrics."

    parts = []
    phase = context_report.get('phase', 'practice')
    parts.append(f"Session type: {phase}")

    # Support both single analysis and V1/V2 comparison format
    analysis = (
        context_report.get('analysis')
        or context_report.get('v1Analysis')
        or context_report.get('v1Report')
    )
    if analysis and isinstance(analysis, dict):
        score = analysis.get('overall_score')
        if score is not None:
            parts.append(f"Overall score: {score}/100")

        feedback = analysis.get('detailed_feedback')
        if feedback:
            parts.append(f"Feedback summary: {str(feedback)[:250]}")

        strengths = analysis.get('strengths') or []
        if strengths:
            parts.append(f"Strengths: {'; '.join(str(s) for s in strengths[:3])}")

        recs = analysis.get('recommendations') or []
        if recs:
            parts.append(f"Recommendations: {'; '.join(str(r) for r in recs[:3])}")

        cat = analysis.get('category_scores') or {}
        if cat:
            cat_str = ', '.join(f"{k}={v}" for k, v in list(cat.items())[:5])
            parts.append(f"Category scores: {cat_str}")

        # Speech-specific metrics
        wpm = analysis.get('speech_speed_wpm')
        if wpm:
            parts.append(f"Speaking pace: {wpm} WPM")
        fillers = analysis.get('filler_words_count')
        if fillers is not None:
            parts.append(f"Filler words detected: {fillers}")

    return '\n'.join(parts)


# ─────────────────────────────────────────────────────────────
# Coach persona + prompt
# ─────────────────────────────────────────────────────────────
COACH_SYSTEM = (
    "You are Presenova AI Coach, a live presentation practice partner for students (FYP/viva/class talks). "
    "Rules: "
    "1) Reply in the language the student writes in (English, Roman Urdu or Hindi). "
    "2) Keep it under 80 words. "
    "3) End EVERY reply with exactly ONE short question or task that moves the practice forward. "
    "4) If the student wants to practice but has not given details, ask for the topic first, then audience, "
    "then time limit (one question at a time). "
    "5) Once the topic is known, ask them to type their opening 30 seconds, then give specific feedback on "
    "hook, structure, clarity and pacing, then ask for the next part. "
    "6) If a real analysis report is provided, use its scores and weaknesses. If not, never invent scores. "
    "7) Never repeat a greeting and never give generic advice unrelated to what the student just said."
)


def _build_prompt(message: str, context_text: str, grammar_context: str, history: list) -> str:
    recent = history[-8:] if history else []
    lines = []
    for m in recent:
        role = 'Student' if m.get('role') == 'user' else 'Coach'
        lines.append(f"{role}: {str(m.get('content', ''))[:400]}")
    history_text = '\n'.join(lines) if lines else '(No previous turns)'

    return f"""PRESENTATION CONTEXT:
{context_text}
{grammar_context}

CONVERSATION SO FAR:
{history_text}

STUDENT SAYS: {message}

Reply as Presenova AI Coach, following your rules."""


# ─────────────────────────────────────────────────────────────
# Gemini (primary)
# ─────────────────────────────────────────────────────────────
def _gemini_coach_response(prompt: str) -> str:
    """Returns '' if Gemini is unavailable or gives an empty answer."""
    try:
        from services.ai.gemini_provider import GeminiProvider
        provider = GeminiProvider()
        if not provider.is_available():
            logger.warning("[AI COACH] Gemini provider not available.")
            return ''

        response = provider.generate(
            prompt=prompt,
            temperature=0.7,
            max_output_tokens=1024,
            system_instruction=COACH_SYSTEM,
        )
        if not response or not response.strip():
            logger.warning("[AI COACH] Gemini returned an empty response (model=%s)", provider.model_name)
            return ''
        return response.strip()

    except Exception as exc:
        logger.warning("[AI COACH] Gemini call failed: %s", exc)
        return ''


# ─────────────────────────────────────────────────────────────
# Groq (backup)
# ─────────────────────────────────────────────────────────────
def _groq_coach_response(prompt: str) -> str:
    """Backup LLM when Gemini is busy/unavailable. Needs GROQ_API_KEY and GROQ_CHAT_MODEL in .env."""
    try:
        key = os.getenv('GROQ_API_KEY', '').strip()
        if not key:
            logger.warning("[AI COACH] GROQ_API_KEY missing; Groq backup skipped.")
            return ''
        model = os.getenv('GROQ_CHAT_MODEL', '').strip()
        if not model:
            logger.warning("[AI COACH] GROQ_CHAT_MODEL not set in .env; Groq backup skipped.")
            return ''

        from groq import Groq
        client = Groq(api_key=key, timeout=20.0)
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": COACH_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=400,
        )
        return (r.choices[0].message.content or '').strip()

    except Exception as exc:
        logger.warning("[AI COACH] Groq call failed: %s", exc)
        return ''


# ─────────────────────────────────────────────────────────────
# Local fallback (TF-IDF intent engine) - kept for reference, no longer used
# by the endpoint so that students never see fake canned answers.
# ─────────────────────────────────────────────────────────────
def _local_fallback_response(message: str, context_report: dict, has_history: bool = False) -> str:
    """Use local intent classification engine when Gemini is unavailable."""
    try:
        from services.coach_intent_engine import process_coach_chat
        result = process_coach_chat(
            user_message=message,
            current_state="PRACTICE",
            session_context=context_report,
        )
        resp = result.get("response", "")
        if has_history and ("Dr. Alexander Vance" in resp or "Presenova AI Coach" in resp or "Hello! I am" in resp):
            return (
                f"Regarding '{message[:60]}': focus on explaining this clearly with structured examples. "
                "Break complex points into three key takeaways for your audience. How would you introduce this on your first slide?"
            )
        return resp
    except Exception as exc:
        logger.warning("[AI COACH] Local fallback error: %s", exc)
        return ""


# ─────────────────────────────────────────────────────────────
# Main endpoint
# ─────────────────────────────────────────────────────────────
@phase_five_bp.route('/practice-chat', methods=['POST'])
@jwt_required(optional=True)
@rate_limit(limit_authenticated=30, limit_guest=5)
def practice_chat():
    """
    AI Coach chat endpoint.

    POST /api/practice-chat
    Body (JSON):
      {
        "message": "User's question or statement",
        "history": [{"role": "user"|"ai", "content": "..."}],
        "contextReport": { "phase": "...", "analysis": {...} }
      }

    Response:
      {
        "status": "success",
        "ai_response": "Coach reply",
        "powered_by": "gemini" | "groq" | "unavailable",
        ...
      }
    """
    try:
        # ── 1. Validate ───────────────────────────────────────────────────────
        data = request.get_json()
        if data is None:
            return jsonify({
                "success": False,
                "error": "InvalidJson",
                "message": "Request body must be valid JSON",
            }), 400

        message = data.get('message', '').strip()
        if not message:
            return jsonify({
                "success": False,
                "error": "MissingMessage",
                "message": "Please provide a 'message' field",
            }), 400

        history = data.get('history', [])
        context_report = data.get('contextReport', {})

        # ── 2. Grammar pre-pass (LanguageTool) ───────────────────────────────
        grammar_issues = []
        grammar_score_val = 100
        grammar_context = ""
        try:
            if len(message) > 10:
                grammar_issues = check_grammar(message)
                grammar_score_val = lt_grammar_score(grammar_issues, len(message.split()))
                grammar_summary = summarise_grammar_issues(grammar_issues, max_issues=5)
                if grammar_issues:
                    grammar_context = (
                        f"\nGRAMMAR NOTE ({len(grammar_issues)} issue(s), score {grammar_score_val}/100): "
                        f"{grammar_summary} - briefly mention the fix in your reply."
                    )
                    logger.info("[AI COACH] %d grammar issue(s) found.", len(grammar_issues))
        except Exception as _ge:
            logger.warning("[AI COACH] Grammar pre-pass failed: %s", _ge)

        # ── 3. Build analysis context + prompt ────────────────────────────────
        context_text = _build_context_text(context_report)
        coach_prompt = _build_prompt(message, context_text, grammar_context, history)

        # ── 4. Gemini first, Groq as backup ───────────────────────────────────
        ai_response_text = _gemini_coach_response(coach_prompt)
        powered_by = "gemini"

        if not ai_response_text:
            logger.info("[AI COACH] Gemini gave no answer, trying Groq.")
            ai_response_text = _groq_coach_response(coach_prompt)
            powered_by = "groq"

        # ── 5. Honest message instead of a fake canned answer ─────────────────
        if not ai_response_text:
            ai_response_text = "The AI coach is busy right now. Please send your message again in a few seconds."
            powered_by = "unavailable"

        # ── 6. Return response ────────────────────────────────────────────────
        logger.info("[AI COACH] Response ready. powered_by=%s", powered_by)
        return jsonify({
            "success": True,
            "status": "success",
            "ai_response": ai_response_text,
            "powered_by": powered_by,
            "message_id": f"msg_{int(datetime.now().timestamp() * 1000)}",
            "timestamp": datetime.now().isoformat(),
            "grammar_score": grammar_score_val,
            "grammar_issues": grammar_issues,
            "grammar_issues_count": len(grammar_issues),
        }), 200

    except Exception as e:
        logger.error("[AI COACH] Unexpected error: %s", e, exc_info=True)

        error_message = str(e)
        if "api_key" in error_message.lower() or "authentication" in error_message.lower():
            error_message = "API key issue. Please check your Gemini configuration."
        elif "quota" in error_message.lower() or "rate" in error_message.lower():
            error_message = "API quota exceeded. Please try again later."
        elif "timeout" in error_message.lower():
            error_message = "Request timed out. Please try again."

        return jsonify({
            "success": False,
            "error": "CoachError",
            "message": error_message,
        }), 500