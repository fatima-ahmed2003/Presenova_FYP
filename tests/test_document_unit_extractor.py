"""Offline tests for the structured Document Analyzer extractor."""

import io

import pymupdf
from docx import Document
from docx.shared import Inches, Pt
from PIL import Image
from pptx import Presentation
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches as PptInches

from services.document_unit_extractor import extract_document_units


def _small_png() -> io.BytesIO:
    image = Image.new("RGB", (2, 2), color="blue")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    return stream


def test_extracts_pptx_slide_units(tmp_path):
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Quarterly Results"
    body = slide.placeholders[1].text_frame
    body.text = "Revenue increased by 12 percent."
    bullet = body.add_paragraph()
    bullet.text = "Customer retention improved."
    bullet.level = 0
    bullet_properties = OxmlElement("a:buChar")
    bullet_properties.set("char", "•")
    bullet._p.get_or_add_pPr().append(bullet_properties)
    bullet.runs[0].font.size = Pt(18)
    slide.shapes.add_picture(
        _small_png(), PptInches(6), PptInches(1), width=PptInches(1), height=PptInches(1)
    )
    path = tmp_path / "sample.pptx"
    presentation.save(path)

    units = extract_document_units(str(path))

    assert len(units) == 1
    assert units[0] == {
        "unit_no": 1,
        "type": "slide",
        "title": "Quarterly Results",
        "text": "Quarterly Results\nRevenue increased by 12 percent.\nCustomer retention improved.",
        "bullets": ["Customer retention improved."],
        "word_count": 10,
        "min_font_size": 18.0,
        "has_image": True,
    }


def test_extracts_pdf_page_units(tmp_path):
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Usage Summary", fontsize=20)
    page.insert_text((72, 100), "- Visits increased by 24 percent.", fontsize=12)
    path = tmp_path / "sample.pdf"
    document.save(path)
    document.close()

    units = extract_document_units(str(path))

    assert len(units) == 1
    assert units[0] == {
        "unit_no": 1,
        "type": "page",
        "title": "Usage Summary",
        "text": "Usage Summary\n- Visits increased by 24 percent.",
        "bullets": ["Visits increased by 24 percent."],
        "word_count": 7,
        "min_font_size": 12.0,
        "has_image": False,
    }


def test_extracts_docx_heading_sections_and_images(tmp_path):
    document = Document()
    document.add_heading("Overview", level=1)
    paragraph = document.add_paragraph("The project reached 40 users.")
    paragraph.runs[0].font.size = Pt(11)
    bullet = document.add_paragraph("Reduced service delays.", style="List Bullet")
    bullet.runs[0].font.size = Pt(10)
    document.add_heading("Next Steps", level=1)
    document.add_paragraph("Review the pilot results.")
    document.add_picture(_small_png(), width=Inches(0.2))
    path = tmp_path / "sample.docx"
    document.save(path)

    units = extract_document_units(str(path))

    assert units == [
        {
            "unit_no": 1,
            "type": "section",
            "title": "Overview",
            "text": "Overview\nThe project reached 40 users.\nReduced service delays.",
            "bullets": ["Reduced service delays."],
            "word_count": 9,
            "min_font_size": 10.0,
            "has_image": False,
        },
        {
            "unit_no": 2,
            "type": "section",
            "title": "Next Steps",
            "text": "Next Steps\nReview the pilot results.",
            "bullets": [],
            "word_count": 6,
            "min_font_size": None,
            "has_image": True,
        },
    ]


def test_headingless_docx_does_not_infer_a_title_from_body_text(tmp_path):
    document = Document()
    document.add_paragraph("This is body text, not a document title.")
    path = tmp_path / "untitled.docx"
    document.save(path)

    units = extract_document_units(str(path))

    assert len(units) == 1
    assert units[0]["title"] == ""
    assert units[0]["text"] == "This is body text, not a document title."
