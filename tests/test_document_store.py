import hashlib
import io
import errno
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from PIL import Image

from highschoolphysics.document_store import (
    DocumentStore,
    DocumentStoreError,
    sniff_document_bytes,
)


def docx_bytes(entries=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in (entries or {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<document/>",
        }).items():
            archive.writestr(name, payload)
    return data.getvalue()


def png_bytes(color=(40, 80, 120)):
    output = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(output, "PNG")
    return output.getvalue()


class DocumentStoreTests(unittest.TestCase):
    def test_document_magic_and_docx_container_are_checked(self):
        self.assertEqual(sniff_document_bytes(b"%PDF-1.7\n", "paper.pdf")["mime_type"], "application/pdf")
        self.assertEqual(sniff_document_bytes(docx_bytes(), "paper.docx")["extension"], "docx")
        with self.assertRaisesRegex(DocumentStoreError, "match its extension"):
            sniff_document_bytes(b"plain text", "paper.pdf")
        with self.assertRaises(DocumentStoreError) as context:
            sniff_document_bytes(docx_bytes({"../outside": b"bad"}), "paper.docx")
        self.assertEqual(context.exception.code, "invalid_container")
        with self.assertRaises(DocumentStoreError) as context:
            sniff_document_bytes(b"PK\x03\x04not a zip", "paper.docx")
        self.assertEqual(context.exception.code, "invalid_container")

    def test_docx_archive_rejects_zip_slip_and_windows_absolute_paths(self):
        for unsafe_name in ("../outside", "word/../../outside", "word\\..\\outside", "C:/outside", "C:outside", "./word/extra.xml"):
            with self.subTest(unsafe_name=unsafe_name):
                package = docx_bytes({
                    "[Content_Types].xml": b"<Types/>",
                    "word/document.xml": b"<document/>",
                    unsafe_name: b"bad",
                })
                with self.assertRaisesRegex(DocumentStoreError, "unsafe path"):
                    sniff_document_bytes(package, "paper.docx")

    def test_docx_archive_rejects_suspicious_compression_ratio(self):
        package = docx_bytes({
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<document/>",
            "word/media/oversized-ratio.bin": b"0" * (512 * 1024),
        })
        with self.assertRaisesRegex(DocumentStoreError, "compression ratio is unsafe"):
            sniff_document_bytes(package, "paper.docx")

    def test_original_write_is_hash_checked_idempotent_and_path_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DocumentStore(directory)
            body = b"%PDF-1.7\nexample"
            digest = hashlib.sha256(body).hexdigest()
            saved = store.store_original("school-1", "doc-1", "../paper.pdf", body, digest)
            self.assertEqual(store.read(saved["storage_key"], digest), body)
            store.store_original("school-1", "doc-1", "paper.pdf", body, digest)
            with self.assertRaises(DocumentStoreError) as context:
                store.store_original("school-1", "doc-1", "paper.pdf", b"%PDF-1.7\nother", digest)
            self.assertEqual(context.exception.code, "hash_mismatch")
            with self.assertRaises(DocumentStoreError):
                store.read("../../etc/passwd")

    def test_image_assets_are_decoded_normalized_hashed_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DocumentStore(directory)
            source = png_bytes()
            first = store.store_asset("school-1", source, {"part": "word/document.xml"})
            second = store.store_asset("school-1", source)
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(first["mime_type"], "image/png")
            self.assertEqual((first["width_px"], first["height_px"]), (16, 12))
            self.assertEqual(store.read(first["storage_key"], first["sha256"]), first["data"])
            with self.assertRaises(DocumentStoreError) as context:
                store.store_asset("school-1", b"not an image")
            self.assertEqual(context.exception.code, "invalid_asset")

    def test_concurrent_same_school_asset_store_reuses_one_immutable_object(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DocumentStore(directory)
            source = png_bytes()
            barrier = threading.Barrier(2)
            atomic_write = store.atomic_write

            def synchronized_write(relative_parts, chunks):
                barrier.wait(timeout=5)
                return atomic_write(relative_parts, chunks)

            store.atomic_write = synchronized_write
            with ThreadPoolExecutor(max_workers=2) as pool:
                assets = list(pool.map(lambda _: store.store_asset("school-1", source), range(2)))

            self.assertEqual(assets[0]["id"], assets[1]["id"])
            self.assertEqual(assets[0]["storage_key"], assets[1]["storage_key"])
            self.assertEqual(assets[0]["sha256"], assets[1]["sha256"])
            self.assertEqual(store.read(assets[0]["storage_key"], assets[0]["sha256"]), assets[0]["data"])

    def test_conversion_directory_is_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DocumentStore(directory)
            files = {
                "document.md": b"# Paper\n",
                "layout.json": b"{}",
                "manifest.json": b"{}",
            }
            keys = store.write_conversion("school-1", "conversion-1", files)
            self.assertEqual(set(keys), set(files))
            store.write_conversion("school-1", "conversion-1", files)
            with self.assertRaises(DocumentStoreError):
                store.write_conversion("school-1", "conversion-1", dict(files, **{"document.md": b"changed"}))

    def test_atomic_write_is_idempotent_for_same_bytes_and_never_overwrites_race_winner(self):
        for payloads, expected_successes in (((b"same", b"same"), 2), ((b"first", b"second"), 1)):
            with self.subTest(payloads=payloads), tempfile.TemporaryDirectory() as directory:
                store = DocumentStore(directory)
                barrier = threading.Barrier(2)

                def write(payload):
                    def chunks():
                        barrier.wait(timeout=5)
                        yield payload
                    try:
                        return ("stored", store.atomic_write(["schools", "school-1", "race.bin"], chunks())[1])
                    except DocumentStoreError as exc:
                        return ("rejected", exc.code)

                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(write, payloads))

                self.assertEqual(sum(result[0] == "stored" for result in results), expected_successes)
                self.assertEqual(sum(result[0] == "rejected" for result in results), 2 - expected_successes)
                stored = store.read("schools/school-1/race.bin")
                self.assertIn(stored, payloads)
                if expected_successes == 1:
                    rejected_payload = payloads[0] if stored == payloads[1] else payloads[1]
                    self.assertNotEqual(stored, rejected_payload)

    def test_atomic_write_removes_temporary_file_when_disk_sync_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DocumentStore(directory)
            with patch("highschoolphysics.document_store.os.fsync", side_effect=OSError(errno.ENOSPC, "disk full")):
                with self.assertRaises(OSError):
                    store.atomic_write(["schools", "school-1", "source.pdf"], [b"%PDF-1.7\nbody"])
            target = store._path("schools", "school-1", "source.pdf")
            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
