"""Attachment fixture generators - real, valid files, built with stdlib only.

Nothing binary is stored in the repo; every seeder calls build() and gets the
files written into its own directory on first run.
"""
from __future__ import annotations

import base64
import struct
import zipfile
import zlib
from pathlib import Path

MIME = {
    ".png": "image/png",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".zip": "application/zip",
    ".docx": "application/vnd.openxmlformats-officedocument."
             "wordprocessingml.document",
}


def png(w: int, h: int, path: Path, *, accent=(220, 76, 70)) -> None:
    """Valid RGB PNG that looks like a screenshot rather than a test pattern."""
    rows = b""
    for y in range(h):
        row = bytearray()
        for x in range(w):
            if y < max(4, h // 12):                       # title bar
                row += bytes((36, 42, 58))
            elif h // 8 < y < h // 2 and w // 12 < x < w - w // 12:
                row += bytes(accent) if y < h // 3 else bytes((246, 246, 248))
            else:
                g = 248 - (y * 40 // max(h, 1))
                row += bytes((g, g, min(255, g + 4)))
        rows += b"\x00" + bytes(row)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b""))


def png_data_uri(w: int = 240, h: int = 120) -> str:
    """Small PNG as a data: URI, for testing inline images inside HTML bodies."""
    tmp = Path(__file__).parent / "_inline_tmp.png"
    png(w, h, tmp)
    uri = "data:image/png;base64," + base64.b64encode(tmp.read_bytes()).decode()
    tmp.unlink(missing_ok=True)
    return uri


def pdf(title: str, lines: list[str], path: Path) -> None:
    """Minimal but valid PDF 1.4, single page, Helvetica."""
    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content = f"BT /F1 15 Tf 56 790 Td ({esc(title)}) Tj ET\n"
    y = 764
    for ln in lines:
        if y < 40:
            break
        content += f"BT /F1 9 Tf 56 {y} Td ({esc(ln)}) Tj ET\n"
        y -= 13
    cb = content.encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(cb)).encode() + b" >>\nstream\n" + cb + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode()
            + b" /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    path.write_bytes(out)


def docx(title: str, paras: list[str], path: Path) -> None:
    """Minimal valid Office Open XML document."""
    def p(t: str) -> str:
        return ("<w:p><w:r><w:t xml:space=\"preserve\">"
                + t.replace("&", "&amp;").replace("<", "&lt;")
                + "</w:t></w:r></w:p>")

    doc = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
           "<w:document xmlns:w=\"http://schemas.openxmlformats.org/"
           "wordprocessingml/2006/main\"><w:body>"
           + p(title) + "".join(p(x) for x in paras)
           + "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/></w:sectPr>"
             "</w:body></w:document>")
    ct = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
          "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/"
          "content-types\"><Default Extension=\"rels\" ContentType=\"application/"
          "vnd.openxmlformats-package.relationships+xml\"/>"
          "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
          "<Override PartName=\"/word/document.xml\" ContentType=\"application/"
          "vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
          "</Types>")
    rels = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/"
            "2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://"
            "schemas.openxmlformats.org/officeDocument/2006/relationships/"
            "officeDocument\" Target=\"word/document.xml\"/></Relationships>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)


def big_zip(path: Path, mb: float = 1.5) -> None:
    """A genuinely large file. ZIP_STORED so the bytes actually hit the wire -
    a compressed archive of repeated text would upload as a few KB and prove
    nothing about size handling."""
    payload = ("archive line %06d - migration size test padding\n" % 0) * 1
    line = "archive line %06d - migration size test padding\n"
    target = int(mb * 1024 * 1024)
    buf = []
    n = 0
    total = 0
    while total < target:
        s = line % n
        buf.append(s)
        total += len(s)
        n += 1
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr("payload.txt", "".join(buf))


def build(d: Path) -> dict:
    """Create the standard fixture set in `d`. Returns {name: size}."""
    d.mkdir(parents=True, exist_ok=True)
    png(880, 520, d / "screenshot-error.png")
    png(640, 360, d / "screenshot-console.png", accent=(64, 128, 220))
    pdf("INVOICE INV-2026-0912",
        ["Northgate Retail Ltd", "Billing period: 01-31 August 2026", "",
         "Platform subscription (40 agents)          EUR 3,000.00",
         "Overage - additional storage               EUR   120.00",
         "", "Total                                      EUR 3,120.00"],
        d / "invoice-INV-2026-0912.pdf")
    pdf("Отчёт Q3 2026 / 报告",
        ["Unicode filename test.",
         "The FILE NAME is the subject of this test, not the contents.",
         "A migration that mangles the name still 'succeeds' on a byte count."],
        d / "отчёт-2026-Q3_报告.pdf")
    (d / "api-error.log").write_text(
        "".join(f"2026-08-2{d_} 14:0{d_}:11 ERROR upstream=orders-svc "
                f"status=502 latency_ms={780 + d_ * 37} trace=a1f{d_ * 4}\n"
                for d_ in range(1, 9)),
        encoding="utf-8")
    (d / "export-sample.csv").write_text(
        "record_id,type,created_at,subject,status\n"
        "44100,ticket,2026-05-02,Login issue,closed\n"
        "44118,ticket,2026-05-14,Billing query,closed\n",
        encoding="utf-8")
    docx("Reproduction notes",
         ["1. Request a reset link.", "2. Open it within 30 seconds.",
          "3. Observe: 'This link has expired.'",
          "Environment: Chrome 141, Windows 11."],
         d / "repro-notes.docx")
    big_zip(d / "diagnostics-bundle.zip", 1.5)
    return {f.name: f.stat().st_size for f in sorted(d.iterdir())}
