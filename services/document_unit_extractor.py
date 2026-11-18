"""Extract structured slide, page, or section units from local documents."""

from __future__ import annotations

import os
import re
from typing import Literal, TypedDict


UnitType = Literal["slide", "page", "section"]


class DocumentUnit(TypedDict):
    unit_no: int
    type: UnitType
    title: str
    text: str
    bullets: list[str]
    word_count: int
    min_font_size: float | None
    has_image: bool


_BULLET_PREFIX = re.compile(r"^\s*(?:[•●▪◦‣⁃–—-]|\d+[.)]|[A-Za-z][.)])\s+")
_WORD = re.compile(r"\b[\w]+(?:['’][\w]+)*\b", re.UNICODE)


def _is_bullet_text(text: str) -> bool:
    return bool(_BULLET_PREFIX.match(text))


def _bullet_text(text: str) -> str:
    return _BULLET_PREFIX.sub("", text, count=1).strip()


def _make_unit(
    unit_no: int,
    unit_type: UnitType,
    title: str,
    lines: list[str],
    bullets: list[str],
    font_sizes: list[float],
    has_image: bool,
) -> DocumentUnit:
    text = "\n".join(line for line in lines if line.strip()).strip()
    return {
        "unit_no": unit_no,
        "type": unit_type,
        "title": title.strip(),
        "text": text,
        "bullets": bullets,
        "word_count": len(_WORD.findall(text)),
        "min_font_size": min(font_sizes) if font_sizes else None,
        "has_image": has_image,
    }


def _paragraph_has_bullet(paragraph: object) -> bool:
    if _is_bullet_text(paragraph.text):
        return True
    if getattr(paragraph, "level", 0) > 0:
        return True
    p_pr = getattr(paragraph._p, "pPr", None)
    if p_pr is None:
        return False
    from pptx.oxml.ns import qn

    return p_pr.find(qn("a:buChar")) is not None or p_pr.find(qn("a:buAutoNum")) is not None


def _extract_pptx(file_path: str) -> list[DocumentUnit]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    presentation = Presentation(file_path)
    units: list[DocumentUnit] = []

    def collect_shape(
        shape: object,
        lines: list[str],
        bullets: list[str],
        font_sizes: list[float],
    ) -> bool:
        shape_type = getattr(shape, "shape_type", None)
        has_image = shape_type == MSO_SHAPE_TYPE.PICTURE

        if shape_type == MSO_SHAPE_TYPE.GROUP:
            for child in shape.shapes:
                has_image = collect_shape(child, lines, bullets, font_sizes) or has_image
            return has_image

        if shape_type == MSO_SHAPE_TYPE.TABLE:
            for row in shape.table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
                for cell in row.cells:
                    for paragraph in cell.text_frame.paragraphs:
                        for run in paragraph.runs:
                            if run.font.size is not None:
                                font_sizes.append(run.font.size.pt)
            return has_image

        if getattr(shape, "has_text_frame", False):
            for paragraph in shape.text_frame.paragraphs:
                text = paragraph.text.strip()
                if text:
                    lines.append(text)
                    if _paragraph_has_bullet(paragraph):
                        bullets.append(_bullet_text(text))
                for run in paragraph.runs:
                    if run.font.size is not None:
                        font_sizes.append(run.font.size.pt)
        return has_image

    for unit_no, slide in enumerate(presentation.slides, start=1):
        lines: list[str] = []
        bullets: list[str] = []
        font_sizes: list[float] = []
        has_image = False
        for shape in slide.shapes:
            has_image = collect_shape(shape, lines, bullets, font_sizes) or has_image

        title_shape = slide.shapes.title
        title = title_shape.text.strip() if title_shape is not None else ""
        units.append(
            _make_unit(unit_no, "slide", title, lines, bullets, font_sizes, has_image)
        )
    return units


def _extract_pdf(file_path: str) -> list[DocumentUnit]:
    import pymupdf

    units: list[DocumentUnit] = []
    with pymupdf.open(file_path) as document:
        for unit_no, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            bullets = [_bullet_text(line) for line in lines if _is_bullet_text(line)]
            page_data = page.get_text("dict")
            font_sizes = [
                float(span["size"])
                for block in page_data["blocks"]
                if block.get("type") == 0
                for line in block.get("lines", [])
                for span in line.get("spans", [])
                if span.get("text", "").strip()
            ]
            has_image = bool(page.get_images(full=True)) or any(
                block.get("type") == 1 for block in page_data["blocks"]
            )
            title = lines[0] if lines else ""
            units.append(
                _make_unit(unit_no, "page", title, lines, bullets, font_sizes, has_image)
            )
    return units


def _extract_docx(file_path: str) -> list[DocumentUnit]:
    from docx import Document
    from docx.oxml.text.paragraph import CT_P
    from docx.oxml.table import CT_Tbl
    from docx.text.paragraph import Paragraph
    from docx.table import Table

    document = Document(file_path)
    units: list[DocumentUnit] = []
    current_title = ""
    current_lines: list[str] = []
    current_bullets: list[str] = []
    current_font_sizes: list[float] = []
    current_has_image = False

    def emit_section() -> None:
        nonlocal current_title, current_lines, current_bullets, current_font_sizes
        nonlocal current_has_image
        if current_title or current_lines:
            units.append(
                _make_unit(
                    len(units) + 1,
                    "section",
                    current_title,
                    current_lines,
                    current_bullets,
                    current_font_sizes,
                    current_has_image,
                )
            )
        current_title = ""
        current_lines = []
        current_bullets = []
        current_font_sizes = []
        current_has_image = False

    for child in document._element.body.iterchildren():
        if isinstance(child, CT_P):
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                current_has_image = current_has_image or bool(
                    paragraph._p.xpath(".//a:blip")
                )
                continue
            current_has_image = current_has_image or bool(
                paragraph._p.xpath(".//a:blip")
            )
            style_name = paragraph.style.name.casefold() if paragraph.style else ""
            is_section_heading = style_name.startswith("heading 1")
            if is_section_heading:
                emit_section()
                current_title = text
                current_lines.append(text)
            else:
                if style_name == "title" and not current_title:
                    current_title = text
                current_lines.append(text)
                style_id = paragraph.style.style_id.casefold() if paragraph.style else ""
                if (
                    "list bullet" in style_name
                    or "list number" in style_name
                    or style_id.startswith("listbullet")
                    or style_id.startswith("listnumber")
                    or paragraph._p.pPr is not None
                    and paragraph._p.pPr.numPr is not None
                    or _is_bullet_text(text)
                ):
                    current_bullets.append(_bullet_text(text))
            for run in paragraph.runs:
                if run.font.size is not None:
                    current_font_sizes.append(run.font.size.pt)
            current_has_image = current_has_image or bool(
                paragraph._p.xpath(".//a:blip")
            )
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    current_lines.append(" | ".join(cells))
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            if run.font.size is not None:
                                current_font_sizes.append(run.font.size.pt)

    emit_section()
    return units


def extract_plain_text_units(text: str) -> list[DocumentUnit]:
    """Represent a plain-text upload as one untitled section unit."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    bullets = [
        _bullet_text(line)
        for line in lines
        if _is_bullet_text(line)
    ]
    return [_make_unit(1, "section", "", lines, bullets, [], False)]


def extract_document_units(file_path: str) -> list[DocumentUnit]:
    """Extract one structured unit per PPTX slide, PDF page, or DOCX section.

    DOCX sections begin at each Heading 1 paragraph; content before the first
    Heading 1 is returned as its own section.
    """
    extension = os.path.splitext(file_path)[1].lower()
    if extension == ".pptx":
        return _extract_pptx(file_path)
    if extension == ".pdf":
        return _extract_pdf(file_path)
    if extension == ".docx":
        return _extract_docx(file_path)
    if extension == ".txt":
        with open(file_path, "r", encoding="utf-8", errors="replace") as stream:
            return extract_plain_text_units(stream.read())
    raise ValueError(f"Unsupported document format: '{extension}'")
