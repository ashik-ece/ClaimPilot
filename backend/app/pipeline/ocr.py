"""Turn PDF bytes into per-page text with a quality signal.

Text PDFs are read natively (exact, confidence 100). Image-only pages are rendered, rotated
upright using Tesseract's orientation detection, and OCR'd; the mean word confidence tells
the extractor whether the text can be trusted or the page image should go to the vision model.
"""

import io
from dataclasses import dataclass

import pymupdf
import pytesseract
from PIL import Image

OCR_DPI = 200
MIN_NATIVE_CHARS = 20  # fewer characters than this on a page means it is a scan


@dataclass
class PageText:
    document: str
    page: int
    text: str
    source: str  # "native" | "ocr"
    confidence: float  # 0-100; native text is 100
    image_png: bytes | None = None  # kept for OCR pages so the vision fallback can use them


def _upright(image: Image.Image) -> Image.Image:
    try:
        osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractError:
        return image
    angle = int(osd.get("rotate", 0))
    return image.rotate(-angle, expand=True, fillcolor="white") if angle else image


def _ocr(image: Image.Image) -> tuple[str, float]:
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    words = [
        (w, float(c))
        for w, c in zip(data["text"], data["conf"], strict=True)
        if w.strip() and float(c) >= 0
    ]
    text = pytesseract.image_to_string(image)
    confidence = sum(c for _, c in words) / len(words) if words else 0.0
    return text, round(confidence, 1)


def read_pdf(data: bytes, document: str) -> list[PageText]:
    pages: list[PageText] = []
    with pymupdf.open(stream=data, filetype="pdf") as pdf:  # type: ignore[no-untyped-call]
        for number, page in enumerate(pdf, start=1):
            native = page.get_text().strip()
            if len(native) >= MIN_NATIVE_CHARS:
                pages.append(PageText(document, number, native, "native", 100.0))
                continue
            pix = page.get_pixmap(dpi=OCR_DPI)
            image = _upright(Image.open(io.BytesIO(pix.tobytes("png"))))
            text, confidence = _ocr(image)
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            pages.append(
                PageText(document, number, text.strip(), "ocr", confidence, buf.getvalue())
            )
    return pages
