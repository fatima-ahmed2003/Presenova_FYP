"""
One-shot patcher for ai_evaluator.py (speech prompt fixes).

Usage (from the folder that contains ai_evaluator.py):
    python patch_ai_evaluator.py
or with an explicit path:
    python patch_ai_evaluator.py path\\to\\ai_evaluator.py

It makes a backup (ai_evaluator.py.bak), checks that every anchor text exists exactly once,
and only then writes the file. If anything does not match, NOTHING is changed.
"""
import shutil
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "ai_evaluator.py"

with open(path, encoding="utf-8") as f:
    src = f.read()

if "_local_clean_transcript" in src:
    print("Already patched. Nothing to do.")
    sys.exit(0)

HELPER = '''def _local_clean_transcript(text: str) -> str:
    """Safety net: remove fillers and back-to-back repeats WITHOUT adding any new words."""
    t = re.sub(r'\\b(um+|uh+|erm|err)\\b[,]?\\s*', '', text, flags=re.I)
    t = re.sub(r'\\b(you know|basically|actually|kind of)\\b[,]?\\s*', '', t, flags=re.I)
    t = re.sub(r'\\b(\\w+)(\\s+\\1\\b)+', r'\\1', t, flags=re.I)
    t = re.sub(r'\\s+', ' ', t).strip()
    return t[:1].upper() + t[1:] if t else t


'''

ASR_NOTE = (
    "TRANSCRIPT RELIABILITY: This transcript comes from automatic speech recognition (ASR) and may "
    "contain recognition errors (wrong or odd words). Do NOT blame the speaker for words that look like "
    "ASR mistakes, and do NOT invent or assume what the speaker meant. Judge only delivery: pacing, "
    "fillers, structure, and clarity of ideas. Never introduce technical terms or product details the "
    "speaker did not say.\n\n"
)

OLD_IMPROVED = (
    '"improved_text": "<Complete, professional rewrite of the transcript. Remove all filler words '
    "(um, like, basically), repetitions, correct any grammatical errors, and format it with clear "
    'structural headings. This rewrite must be fully written out and ready to read aloud.>"'
)
NEW_IMPROVED = (
    '"improved_text": "<The ORIGINAL transcript with ONLY these changes: remove filler words (um, uh, like, '
    "you know, basically, actually, so) and back-to-back repeated words, and fix punctuation and "
    "capitalization. STRICT RULES: do NOT add, replace, or reorder any words. Do NOT add headings, lists, "
    "or new sentences. Do NOT improve vocabulary. If something is unclear, keep it exactly as spoken. "
    'Output length must be about the same as the original minus the fillers.>"'
)

SAFETY_NET = (
    "if module_type == 'speech':\n"
    "                orig_words = len(text.split())\n"
    "                new_words = len(str(analysis_json.get('improved_text', '')).split())\n"
    "                if new_words > orig_words * 1.1 or new_words < orig_words * 0.5:\n"
    "                    analysis_json['improved_text'] = _local_clean_transcript(text)\n"
    "\n"
    "            "
)

EDITS = [
    (
        "def evaluate_7cs(text: str, module_type: str, context_metrics: dict) -> dict:",
        HELPER + "def evaluate_7cs(text: str, module_type: str, context_metrics: dict) -> dict:",
    ),
    (
        "We have also calculated the following real-time speaking metrics from the audio recording for your context:",
        ASR_NOTE + "We have also calculated the following real-time speaking metrics from the audio recording for your context:",
    ),
    (OLD_IMPROVED, NEW_IMPROVED),
    (
        "# ===== ATTACH GRAMMAR DATA TO ALL MODULE RESPONSES =====",
        SAFETY_NET + "# ===== ATTACH GRAMMAR DATA TO ALL MODULE RESPONSES =====",
    ),
]

for i, (old, _new) in enumerate(EDITS, 1):
    n = src.count(old)
    if n != 1:
        print(f"STOP: edit {i} anchor found {n} times (expected 1). File NOT changed.")
        print("Anchor starts with:", old[:80])
        sys.exit(1)

for old, new in EDITS:
    src = src.replace(old, new, 1)

shutil.copyfile(path, path + ".bak")
with open(path, "w", encoding="utf-8") as f:
    f.write(src)

print("Done. 4 edits applied. Backup saved as", path + ".bak")
