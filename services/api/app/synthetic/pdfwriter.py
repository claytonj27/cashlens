"""A tiny PDF writer for synthetic statement fixtures: positioned Helvetica text, no dependencies.

Output is deliberately plain (uncompressed content streams, standard font) so
fixtures stay small, readable in a text editor and deterministic. It exists
only to build test inputs; the application never writes PDFs.
"""

from dataclasses import dataclass, field


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@dataclass
class Page:
    width: float = 612
    height: float = 792
    ops: list[str] = field(default_factory=list)

    def text(self, x: float, y: float, value: str, size: float = 9, bold: bool = False) -> None:
        font = "/F2" if bold else "/F1"
        self.ops.append(f"BT {font} {size} Tf 1 0 0 1 {x:.1f} {y:.1f} Tm ({_escape(value)}) Tj ET")

    def text_right(self, right_x: float, y: float, value: str, size: float = 9) -> None:
        """Right-align by estimating Helvetica width (0.5 em per character suits digits)."""
        width = len(value) * size * 0.5
        self.text(right_x - width, y, value, size)


@dataclass
class Document:
    pages: list[Page] = field(default_factory=list)
    encrypted_marker: bool = False

    def page(self) -> Page:
        page = Page()
        self.pages.append(page)
        return page

    def render(self) -> bytes:
        objects: list[bytes] = []

        def add(body: str | bytes) -> int:
            objects.append(body.encode("latin-1") if isinstance(body, str) else body)
            return len(objects)

        font1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        font2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
        pages_id = len(objects) + 1 + 2 * len(self.pages)  # reserved after page and content objects
        page_ids: list[int] = []
        for page in self.pages:
            stream = "\n".join(page.ops).encode("latin-1")
            content = add(
                b"<< /Length "
                + str(len(stream)).encode()
                + b" >>\nstream\n"
                + stream
                + b"\nendstream"
            )
            page_ids.append(
                add(
                    f"<< /Type /Page /Parent {pages_id} 0 R "
                    f"/MediaBox [0 0 {page.width:.0f} {page.height:.0f}] "
                    f"/Resources << /Font << /F1 {font1} 0 R /F2 {font2} 0 R >> >> "
                    f"/Contents {content} 0 R >>"
                )
            )
        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        pages_obj = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>")
        assert pages_obj == pages_id
        catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")
        encrypt = None
        if self.encrypted_marker:
            # A standard-security dictionary readers treat as encryption, without real keys;
            # readers refuse to open it without a password, which is exactly the fixture's purpose.
            encrypt = add(
                "<< /Filter /Standard /V 1 /R 2 /Length 40 /P -1 /O <"
                + "00" * 32
                + "> /U <"
                + "00" * 32
                + "> >>"
            )
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        trailer = f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R"
        if encrypt:
            trailer += f" /Encrypt {encrypt} 0 R /ID [<{'00' * 16}> <{'00' * 16}>]"
        trailer += f" >>\nstartxref\n{xref}\n%%EOF\n"
        out += trailer.encode()
        return bytes(out)
