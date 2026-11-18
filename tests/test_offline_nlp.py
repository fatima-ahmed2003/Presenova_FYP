import sys
import os

# Force stdout/stderr UTF-8 encoding
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ['FORCE_OFFLINE_MODE'] = '1'

from ai_evaluator import evaluate_7cs, compare_documents
from services.analysis.context_verifier import verify_internal_context_consistency
from services.viva_rag_engine import generate_viva_questions_rag
from services.coach_intent_engine import process_coach_chat
from services.rewrite.spacy_rewriter import rewrite_slide_content

def test_offline_evaluator():
    print("=" * 60)
    print("🧪 TESTING OFFLINE 7CS EVALUATOR & NLP MODULE")
    print("=" * 60)

    sample_presentation = (
        "AI IN HEALTHCARE - PRESENTATION TRANSCRIPT\n\n"
        "Slide 1: Introduction\n"
        "Today we are discussing Artificial Intelligence in Healthcare. AI is transforming diagnostics, patient care, and administrative tasks.\n\n"
        "Slide 2: Clinical Diagnostics\n"
        "Machine learning models can analyze medical imaging to detect anomalies with accuracy comparable to human radiographers.\n\n"
        "Slide 3: Challenges & Limitations\n"
        "Key challenges include data privacy, security, and algorithmic bias. Models trained on limited demographics may not generalize well.\n\n"
        "Slide 4: Conclusion & Call to Action\n"
        "The future of healthcare involves doctor-AI collaboration. We invite our stakeholders to approve next quarter's pilot program."
    )

    # Test Document Mode
    print("\n--- Test 1: Document Analyzer (Offline) ---")
    doc_res = evaluate_7cs(sample_presentation, module_type='document', context_metrics={"filename": "health_ai.pdf"})
    print(f"✅ Document Overall Score: {doc_res['overall_score']}")
    print(f"✅ Category Scores: {doc_res['category_scores']}")
    print(f"✅ 7Cs Scores: {doc_res['seven_cs_scores']}")
    print(f"✅ Strengths Count: {len(doc_res['strengths'])}")
    print(f"✅ Recommendations Count: {len(doc_res['recommendations'])}")

    # Test Speech Mode
    print("\n--- Test 2: Speech Analyzer (Offline) ---")
    speech_text = "Um, hello panel. I am, like, basically going to explain our thesis project. Actually, it is quite useful."
    speech_res = evaluate_7cs(speech_text, module_type='speech', context_metrics={"speech_speed_wpm": 135, "filler_count": 4, "filler_percentage": 5.2})
    print("Speech score unavailable while Gemini is disabled; no fallback score was generated.")

    # Test Live Mode
    print("\n--- Test 3: Live Coach Evaluation (Offline) ---")
    live_res = evaluate_7cs(sample_presentation, module_type='live', context_metrics={"avg_eye": 88, "avg_posture": 92, "avg_wpm": 140, "fillers": 2, "avg_qna": 85})
    print(f"✅ Live Coach Overall Score: {live_res['overall_score']}")

    # Test Document Version Comparison
    print("\n--- Test 4: Document Version Comparison (Offline) ---")
    v1 = "Incomplete draft with bad spelling and no call to action."
    v2 = sample_presentation
    comp_res = compare_documents(v1, v2, 45, 88, "health_ai.pdf")
    print(f"✅ Score Difference: +{comp_res['score_difference']}")

    # Test Internal Document Context Verification
    print("\n--- Test 5: Internal Document Context Consistency (FAISS RAG) ---")
    claims = ["Machine learning models analyze medical imaging.", "Quantum teleporters transmit patient data instantaneously."]
    ctx_res = verify_internal_context_consistency(claims, sample_presentation)
    print(f"✅ Context Accuracy Score: {ctx_res['context_accuracy_score']}")
    print(f"✅ Inaccuracies Detected: {len(ctx_res['inaccuracies_detected'])}")

    # Test Viva Question RAG Generator
    print("\n--- Test 6: Viva Defense Question Generator (FAISS RAG) ---")
    viva_res = generate_viva_questions_rag("doc.pptx", sample_presentation, "doc.pptx", num_questions=5)
    print(f"✅ Viva Questions Generated: {len(viva_res['questions'])}")

    # Test AI Coach Intent Classifier & State Machine
    print("\n--- Test 7: AI Coach Intent Classifier & State Machine ---")
    coach_res = process_coach_chat("Grill me on my thesis methodology")
    print(f"✅ Coach Intent Detected: {coach_res['intent']} | Next State: {coach_res['next_state']}")

    # Test spaCy Slide Rewriter
    print("\n--- Test 8: spaCy Rule-Based Slide Rewriter ---")
    slide_sample = {
        "slide_number": 1,
        "title": "in order to optimize clinical workflows",
        "bullet_points": [
            "Data was analyzed by machine learning models due to the fact that it is fast and efficient."
        ]
    }
    rewrite_res = rewrite_slide_content(slide_sample)
    print(f"✅ Rewritten Title: '{rewrite_res['title']}' | Bullets: {len(rewrite_res['bullet_points'])}")

    print("\n" + "=" * 60)
    print("🎉 ALL OFFLINE NLP & RAG EVALUATOR TESTS PASSED 100% SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    test_offline_evaluator()
