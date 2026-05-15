from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET


class DocumentParser:
    """Parse PDF, Markdown, HTML, and plain text into raw text."""

    async def parse(self, file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            return await self._parse_pdf(file_path)
        elif suffix == ".docx":
            return await self._parse_docx(file_path)
        elif suffix in (".md", ".markdown"):
            return file_path.read_text(encoding="utf-8")
        elif suffix in (".html", ".htm"):
            return await self._parse_html(file_path)
        else:
            return file_path.read_text(encoding="utf-8")

    async def _parse_pdf(self, file_path: Path) -> str:
        from PyPDF2 import PdfReader

        reader = PdfReader(str(file_path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    async def _parse_docx(self, file_path: Path) -> str:
        with ZipFile(file_path) as docx:
            xml = docx.read("word/document.xml")

        root = ET.fromstring(xml)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs: list[str] = []
        for paragraph in root.findall(".//w:p", namespace):
            text = "".join(
                node.text or ""
                for node in paragraph.findall(".//w:t", namespace)
            ).strip()
            if text:
                paragraphs.append(text)
        return "\n".join(paragraphs)

    async def _parse_html(self, file_path: Path) -> str:
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text: list[str] = []

            def handle_data(self, data: str) -> None:
                self.text.append(data.strip())

        extractor = TextExtractor()
        extractor.feed(file_path.read_text(encoding="utf-8"))
        return "\n".join(extractor.text)
