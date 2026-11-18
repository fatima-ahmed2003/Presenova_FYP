"""Local integration coverage for Document Analyzer's supported upload formats."""

import io
import os
import sys

import pytest
from docx import Document
from pptx import Presentation
from pptx.util import Inches
from reportlab.pdfgen import canvas

from document_analyzer_qa_backend import (
    SEVEN_CS,
    _ReportStore,
    _UploadStore,
    create_qa_app,
)


@pytest.fixture
def local_fallback_client():
    import ai_evaluator
    import services.document_unit_checks as document_unit_checks
    import services.language_tool_service as language_tool_service

    missing = object()
    environment_names = (
        "GEMINI_API_KEY",
        "LANGUAGETOOL_API_KEY",
        "LANGUAGETOOL_USERNAME",
        "FIREBASE_CREDENTIALS_JSON",
        "FIREBASE_PRIVATE_KEY",
        "FIREBASE_CLIENT_EMAIL",
        "FIREBASE_PROJECT_ID",
        "ENABLE_GRAMMAR_CHECK",
        "ALLOW_IN_MEMORY_DB",
    )
    saved_environment = {
        name: os.environ.get(name, missing) for name in environment_names
    }
    stub_module_names = (
        "models",
        "services.analysis",
        "services.analysis.statistics",
        "services.analysis.visuals",
        "services.analysis.sentiment",
        "services.analysis.pacing",
        "services.analysis.delivery",
        "services.analysis.narrative",
        "phase_two",
    )
    saved_modules = {
        name: sys.modules.get(name, missing) for name in stub_module_names
    }
    original_grammar_check = ai_evaluator.check_grammar
    original_evaluate_7cs = ai_evaluator.evaluate_7cs
    original_unit_grammar_check = document_unit_checks._language_tool_matches
    original_provider_overrides = {
        name: ai_evaluator._gemini_provider.__dict__.get(name, missing)
        for name in ("is_available", "generate_structured")
    }
    original_language_tool_check = language_tool_service.check_grammar
    original_phase_two_os = (
        getattr(saved_modules["phase_two"], "os", missing)
        if saved_modules["phase_two"] is not missing
        else missing
    )
    original_phase_two_ai_review = (
        getattr(saved_modules["phase_two"], "analyze_document_with_ai", missing)
        if saved_modules["phase_two"] is not missing
        else missing
    )
    original_upload_records = list(_UploadStore.records)
    original_report_records = list(_ReportStore.records)

    _UploadStore.records.clear()
    _ReportStore.records.clear()

    def unexpected_llm_evaluation(*_args, **_kwargs):
        raise AssertionError("The Document Analyzer must not use the LLM evaluator.")

    ai_evaluator.evaluate_7cs = unexpected_llm_evaluation
    document_unit_checks._language_tool_matches = lambda _text: ([], None)
    temporary_uploads = None
    try:
        app, temporary_uploads = create_qa_app(local_fallback=True)
        with app.test_client() as client:
            login = client.get("/api/__qa/login")
            assert login.status_code == 200
            token = login.get_json()["access_token"]
            yield client, {"Authorization": f"Bearer {token}"}
    finally:
        if temporary_uploads is not None:
            temporary_uploads.cleanup()
        for name, value in saved_environment.items():
            if value is missing:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for name, module in saved_modules.items():
            if module is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        ai_evaluator.check_grammar = original_grammar_check
        ai_evaluator.evaluate_7cs = original_evaluate_7cs
        for name, value in original_provider_overrides.items():
            if value is missing:
                ai_evaluator._gemini_provider.__dict__.pop(name, None)
            else:
                setattr(ai_evaluator._gemini_provider, name, value)
        language_tool_service.check_grammar = original_language_tool_check
        document_unit_checks._language_tool_matches = original_unit_grammar_check
        if saved_modules["phase_two"] is not missing and original_phase_two_os is not missing:
            saved_modules["phase_two"].os = original_phase_two_os
        if saved_modules["phase_two"] is not missing:
            if original_phase_two_ai_review is missing:
                saved_modules["phase_two"].__dict__.pop(
                    "analyze_document_with_ai", None
                )
            else:
                saved_modules["phase_two"].analyze_document_with_ai = (
                    original_phase_two_ai_review
                )
        _UploadStore.records[:] = original_upload_records
        _ReportStore.records[:] = original_report_records


def _upload(client, headers, filename, content):
    return client.post(
        "/api/analyze-document",
        headers=headers,
        data={"file": (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
    )


def _assert_deterministic_report(response, expected_text):
    assert response.status_code == 200, response.get_json()
    report = response.get_json()
    assert report["evaluation_method"] == "deterministic_rules"
    assert report["grammar_check_status"] == "success"
    assert report["grammar_score"] == 100
    assert set(report["seven_cs_scores"]) == set(SEVEN_CS)
    assert report["seven_cs_scores"]["Coherent"] is None
    assert report["ai_suggestions_unavailable"] is True
    assert report["ai_suggestions"] == []
    assert set(report["seven_cs_evidence"]) == set(SEVEN_CS)
    assert report["document_units"]
    assert expected_text in report["original_text"]
    assert "Deterministic rule-based assessment" in report["detailed_feedback"]
    return report


def test_txt_and_docx_paragraphs_and_tables(local_fallback_client):
    client, headers = local_fallback_client

    text_response = _upload(
        client,
        headers,
        "sample.txt",
        b"Introduction: The team recorded 24 visits. Conclusion: Review the result.",
    )
    text_report = _assert_deterministic_report(
        text_response, "The team recorded 24 visits"
    )

    document = Document()
    document.add_paragraph("Introduction: Library service review.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Measure"
    table.cell(0, 1).text = "24 visits"
    document.add_paragraph("Conclusion: Review the measured result.")
    buffer = io.BytesIO()
    document.save(buffer)

    docx_report = _assert_deterministic_report(
        _upload(client, headers, "sample.docx", buffer.getvalue()),
        "24 visits",
    )
    assert docx_report["original_text"].index("Introduction") < docx_report[
        "original_text"
    ].index("Measure") < docx_report["original_text"].index("Conclusion")
    assert text_report["original_text"]


def test_pdf_text_extraction_and_scanned_pdf_error(local_fallback_client):
    client, headers = local_fallback_client
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, "Introduction: Service usage increased by 12 percent.")
    pdf.drawString(72, 700, "Conclusion: Review the report next month.")
    pdf.save()

    report = _assert_deterministic_report(
        _upload(client, headers, "sample.pdf", buffer.getvalue()),
        "Service usage increased by 12 percent",
    )
    blank = io.BytesIO()
    canvas.Canvas(blank).save()
    scanned_response = _upload(client, headers, "image-only.pdf", blank.getvalue())
    assert scanned_response.status_code == 422
    assert scanned_response.get_json()["error"] == "ExtractionFailed"


def test_pptx_extracts_slide_text_table_and_speaker_notes(local_fallback_client):
    client, headers = local_fallback_client
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_textbox(
        Inches(1), Inches(1), Inches(6), Inches(1)
    ).text_frame.text = "Introduction: Library pilot results."
    table = slide.shapes.add_table(
        rows=1, cols=2, left=Inches(1), top=Inches(2), width=Inches(6), height=Inches(1)
    ).table
    table.cell(0, 0).text = "Attendance"
    table.cell(0, 1).text = "42 visitors"
    slide.notes_slide.notes_text_frame.text = "Review the data with library staff."
    buffer = io.BytesIO()
    presentation.save(buffer)

    report = _assert_deterministic_report(
        _upload(client, headers, "sample.pptx", buffer.getvalue()),
        "42 visitors",
    )
    assert "Slide 1:" in report["original_text"]
    assert "Review the data with library staff." in report["original_text"]


@pytest.mark.parametrize("filename", ["legacy.doc", "legacy.ppt"])
def test_legacy_binary_office_formats_are_not_advertised_as_supported(
    local_fallback_client, filename
):
    client, headers = local_fallback_client
    response = _upload(client, headers, filename, b"not a supported OOXML document")

    assert response.status_code == 400
    assert response.get_json()["error"] == "UnsupportedFileFormat"
