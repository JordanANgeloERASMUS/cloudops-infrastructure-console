import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from app import app


class FilePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(
            dir=Path.home()
        )
        self.directory = Path(self.temporary_directory.name)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["access_configured"] = True
            session["access_permissions"] = {
                "monitoring": False,
                "read_files": True,
                "write_files": True,
                "control_processes": False,
            }

    def tearDown(self):
        self.temporary_directory.cleanup()

    def file_url(self, endpoint, path):
        return f"{endpoint}?{urlencode({'root': 'system', 'path': str(path)})}"

    def test_text_source_preview_does_not_depend_on_file_extension(self):
        source = self.directory / "service.configlang"
        source.write_text("service { enabled = true }\n", encoding="utf-8")

        response = self.client.get(self.file_url("/api/files/read", source))

        self.assertEqual(response.status_code, 200)
        self.assertIn("service { enabled = true }", response.json["content"])

    def test_pdf_preview_and_download_have_inline_and_attachment_dispositions(self):
        pdf = self.directory / "report.pdf"
        pdf.write_bytes(b"%PDF-1.4\npreview test\n")

        preview = self.client.head(self.file_url("/api/files/preview", pdf))
        preflight = self.client.head(self.file_url("/api/files/download", pdf))
        download = self.client.get(self.file_url("/api/files/download", pdf))

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, "application/pdf")
        self.assertTrue(preview.headers["Content-Disposition"].startswith("inline"))
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(preflight.mimetype, "application/pdf")
        self.assertTrue(download.headers["Content-Disposition"].startswith("attachment"))
        self.assertEqual(download.data, pdf.read_bytes())
        preview.close()
        preflight.close()
        download.close()

    def test_image_preview_is_available_inline(self):
        image = self.directory / "preview.png"
        image.write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d494844520000000100000001"
                "0804000000b51c0c020000000b4944415478da63fcff1f000"
                "3030200efbf97670000000049454e44ae426082"
            )
        )

        response = self.client.head(self.file_url("/api/files/preview", image))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        self.assertTrue(response.headers["Content-Disposition"].startswith("inline"))
        response.close()

    def test_docx_preview_extracts_paragraphs_and_tables(self):
        document = self.directory / "notes.docx"
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Operations</w:t></w:r></w:p>
            <w:tbl><w:tr>
              <w:tc><w:p><w:r><w:t>Service</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Active</w:t></w:r></w:p></w:tc>
            </w:tr></w:tbl>
          </w:body>
        </w:document>"""
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("word/document.xml", xml)

        response = self.client.get(
            self.file_url("/api/files/docx-preview", document)
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["blocks"][0]["style"], "Heading1")
        self.assertEqual(response.json["blocks"][0]["text"], "Operations")
        self.assertEqual(
            response.json["blocks"][1]["rows"],
            [["Service", "Active"]],
        )

    def test_invalid_docx_returns_a_clear_client_error(self):
        document = self.directory / "broken.docx"
        document.write_bytes(b"not a zip file")

        response = self.client.get(
            self.file_url("/api/files/docx-preview", document)
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not a readable Word document", response.json["error"])

    def test_docx_preview_rejects_entity_declarations(self):
        document = self.directory / "entities.docx"
        xml = """<!DOCTYPE w:document [<!ENTITY expansion "unsafe">]>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body><w:p><w:r><w:t>&expansion;</w:t></w:r></w:p></w:body>
        </w:document>"""
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("word/document.xml", xml)

        response = self.client.get(
            self.file_url("/api/files/docx-preview", document)
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("custom entities", response.json["error"])


if __name__ == "__main__":
    unittest.main()
