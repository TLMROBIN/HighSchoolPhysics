"""PDF generation service backed by Playwright with injectable test engines."""

from pathlib import Path


class PdfExportError(RuntimeError):
    pass


def _render_with_playwright(html, options):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as error:
        raise PdfExportError("Playwright is not installed") from error
    pdf_options = {
        "format": options.get("format", "A4"),
        "print_background": options.get("print_background", True),
        "margin": options.get(
            "margin",
            {
                "top": "16mm",
                "right": "14mm",
                "bottom": "16mm",
                "left": "14mm",
            },
        ),
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="networkidle")
            pdf_bytes = page.pdf(**pdf_options)
        finally:
            browser.close()
    return {
        "pdf_bytes": pdf_bytes,
        "engine_version": "playwright-chromium",
    }


def write_pdf_artifact(html, output_path, engine=None, options=None):
    options = options or {}
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    renderer = engine or _render_with_playwright
    rendered = renderer(html, options)
    if isinstance(rendered, bytes):
        pdf_bytes = rendered
        engine_version = getattr(renderer, "__name__", "custom-pdf-engine")
    else:
        pdf_bytes = rendered["pdf_bytes"]
        engine_version = rendered.get("engine_version", "custom-pdf-engine")
    output_path.write_bytes(pdf_bytes)
    return {
        "output_path": str(output_path),
        "file_name": output_path.name,
        "content_type": "application/pdf",
        "byte_size": len(pdf_bytes),
        "engine_version": engine_version,
    }
