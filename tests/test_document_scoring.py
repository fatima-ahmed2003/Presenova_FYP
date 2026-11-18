"""Offline tests for deterministic, unit-normalized document scores."""

from docx import Document
from docx.shared import Pt

from services.document_scoring import score_document_units
from services.document_unit_checks import analyze_document_units
from services.document_unit_extractor import extract_document_units


class _LanguageToolResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"matches": []}


def _create_document(path, sections):
    document = Document()
    for title, paragraphs, bullet_count, font_size in sections:
        document.add_heading(title, level=1)
        for paragraph_text in paragraphs:
            paragraph = document.add_paragraph(paragraph_text)
            if font_size is not None:
                for run in paragraph.runs:
                    run.font.size = Pt(font_size)
        for index in range(bullet_count):
            paragraph = document.add_paragraph(
                f"Action {index + 1}", style="List Bullet"
            )
            if font_size is not None:
                paragraph.runs[0].font.size = Pt(font_size)
    document.save(path)
    return path


def _score_file(path):
    units = extract_document_units(str(path))
    checks = analyze_document_units(units)
    return score_document_units(units, checks)


def test_three_offline_documents_receive_distinct_ordered_scores(
    tmp_path, monkeypatch
):
    import services.document_unit_checks as document_unit_checks

    monkeypatch.setattr(
        document_unit_checks.requests,
        "post",
        lambda *_args, **_kwargs: _LanguageToolResponse(),
    )
    good_path = _create_document(
        tmp_path / "good.docx",
        [
            ("Cover", ["Library checkout pilot results."], 0, None),
            ("Agenda", ["Purpose, results, and next steps."], 0, None),
            ("Results", ["Waits fell from 30 minutes to 12 minutes."], 0, None),
            ("Conclusion", ["The pilot met its target."], 0, None),
            ("References", ["Source: library checkout records."], 0, None),
        ],
    )
    medium_path = _create_document(
        tmp_path / "medium.docx",
        [
            ("Pilot Review", ["The team improved some things during the pilot."], 0, 16),
            ("Conclusion", ["Review results next month."], 0, 16),
        ],
    )
    bad_path = _create_document(
        tmp_path / "bad.docx",
        [
            (
                "Status",
                [
                    "Basically, stuff is gonna be reviewed by staff in order to "
                    "fix things, yeah, and it is very important to do a lot of work "
                    "while the team considers many other important issues and "
                    "explains the possible results to every participant in the "
                    "local community before the next scheduled meeting."
                ],
                7,
                12,
            )
        ],
    )

    reports = {
        "good": _score_file(good_path),
        "medium": _score_file(medium_path),
        "bad": _score_file(bad_path),
    }
    scores = {
        name: report["overall_score"] for name, report in reports.items()
    }

    assert len(set(scores.values())) == 3, scores
    assert scores["bad"] < scores["medium"] < scores["good"], scores
    assert reports["good"]["seven_cs_evidence"]["Complete"]["issue_count"] == 0
    assert (
        reports["bad"]["seven_cs_evidence"]["Complete"]["issue_count"]
        > reports["medium"]["seven_cs_evidence"]["Complete"]["issue_count"]
    )
    for report in reports.values():
        assert report["seven_cs_scores"]["Coherent"] is None
        assert report["seven_cs_evidence"]["Coherent"]["status"] == "not_scored"
    assert reports["bad"]["seven_cs_evidence"]["Concise"]["issue_count"] > 0
    assert reports["bad"]["seven_cs_evidence"]["Courteous"]["issue_count"] > 0
    assert reports["bad"]["seven_cs_evidence"]["Complete"]["issue_count"] > 0


def test_penalty_is_averaged_across_units():
    units = [
        {
            "unit_no": 1,
            "title": "",
            "text": "Agenda. Conclusion. References.",
            "bullets": [],
            "min_font_size": None,
            "has_image": False,
        },
        {
            "unit_no": 2,
            "title": "Conclusion",
            "text": "Conclusion.",
            "bullets": [],
            "min_font_size": None,
            "has_image": False,
        },
        {
            "unit_no": 3,
            "title": "References",
            "text": "References.",
            "bullets": [],
            "min_font_size": None,
            "has_image": False,
        },
    ]
    report = score_document_units(
        units,
        {
            "issues": [
                {
                    "unit_no": 1,
                    "rule": "missing_title",
                    "issue": "Unit has no title.",
                    "offset": 0,
                    "original": "",
                    "suggestions": ["Add a descriptive title."],
                    "category": "Completeness",
                }
            ]
        },
    )

    assert report["seven_cs_scores"]["Complete"] == 97
    assert report["seven_cs_evidence"]["Complete"]["per_unit"] == [
        {"unit_no": 1, "penalty": 10, "score": 90},
        {"unit_no": 2, "penalty": 0, "score": 100},
        {"unit_no": 3, "penalty": 0, "score": 100},
    ]
