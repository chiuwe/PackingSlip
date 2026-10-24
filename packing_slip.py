"""
packing_slip.py - make a packing slip PDF from an invoice PDF (the Wave invoice layout).

    python3 packing_slip.py Invoice_220_2026-10-14.pdf
    python3 packing_slip.py invoices/*.pdf -o "Packing slips"

The slip lists each invoice line's item and quantity ordered, with an empty
"Qty Packed" box to fill in by hand. Descriptions (material, Bent, Pemnut ...) and
prices are left off. It is written next to the invoice as "Packing Slip <number>.pdf"
unless -o names a file (one invoice) or a folder.

How the invoice is read: each item starts with a bold line at the left edge of the
Items column, and its quantity is the number on the same baseline under the
Quantity heading. The customer comes from the SHIP TO block when the invoice has
one, otherwise BILL TO.
Standard library only.
"""
import argparse
import os
import re
import sys
import zlib

PAGE_W, PAGE_H, MARGIN = 612, 792, 36  # letter portrait
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")  # top-left of each page, if present
LOGO_SIZE = 72


# --------------------------------------------------------------------------
# Writing a PDF (Helvetica and Helvetica-Bold, compressed streams)
# --------------------------------------------------------------------------
# Adobe font metrics for ASCII 32-126 (1/1000 em), so right-aligned and centered text lands exactly
HELVETICA = [int(w) for w in """
278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556
278 278 584 584 584 556 1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 667 778 722 667
611 722 667 944 667 667 611 278 278 278 469 556 333 556 556 500 556 556 278 556 556 222 222 500 222 833
556 556 556 556 333 500 278 556 500 722 500 500 500 334 260 334 584""".split()]
HELVETICA_BOLD = [int(w) for w in """
278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556
333 333 584 584 584 611 975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 667 778 722 667
611 722 667 944 667 667 611 333 278 333 584 556 333 556 611 556 611 556 333 611 611 278 278 556 278 889
611 611 611 611 389 556 333 611 556 778 556 556 500 389 280 389 584""".split()]


def text_width(s, size, bold=False):
    """Width of s in Helvetica (or Helvetica-Bold) at size points."""
    widths = HELVETICA_BOLD if bold else HELVETICA
    return sum(widths[ord(ch) - 32] if 32 <= ord(ch) <= 126 else 556 for ch in s) * size / 1000


def fit(s, size, width, bold=False):
    if text_width(s, size, bold) <= width:
        return s
    while s and text_width(s + "...", size, bold) > width:
        s = s[:-1]
    return s + "..."


def pdf_str(s):
    b = s.encode("cp1252", errors="replace")
    return "(" + b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").decode("latin-1") + ")"


class Png:
    """An 8-bit, non-interlaced PNG split into PDF image data: color (gray or RGB) and an
    optional alpha channel, each zlib-compressed. Palette and 16-bit PNGs are refused."""

    def __init__(self, path):
        with open(path, "rb") as f:
            data = f.read()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("%s: not a PNG file" % path)
        i, idat = 8, bytearray()
        while i < len(data):
            n, kind = int.from_bytes(data[i:i + 4], "big"), data[i + 4:i + 8]
            body = data[i + 8:i + 8 + n]
            if kind == b"IHDR":
                self.w, self.h = int.from_bytes(body[:4], "big"), int.from_bytes(body[4:8], "big")
                depth, ctype, interlace = body[8], body[9], body[12]
            elif kind == b"IDAT":
                idat += body
            i += 12 + n
        if depth != 8 or interlace or ctype not in (0, 2, 4, 6):
            raise ValueError("%s: use an 8-bit, non-interlaced gray or RGB(A) PNG" % path)
        self.colors = 3 if ctype in (2, 6) else 1
        alpha = ctype in (4, 6)
        if not alpha:  # PNG rows are already in PDF's PNG-predictor format
            self.color, self.alpha, self.predicted = bytes(idat), None, True
            return
        bpp = self.colors + 1
        rows = unfilter(zlib.decompress(bytes(idat)), self.w * bpp, bpp, self.h)
        mv = memoryview(rows)
        color = bytearray(self.w * self.h * self.colors)
        for c in range(self.colors):
            color[c::self.colors] = mv[c::bpp]
        self.color = zlib.compress(bytes(color))
        self.alpha = zlib.compress(bytes(mv[self.colors::bpp]))
        self.predicted = False


def unfilter(raw, stride, bpp, height):
    """PNG scanline filters undone: the raw pixel bytes, rows back to back."""
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ft, line = raw[pos], bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            line = bytearray((a + b) & 0xFF for a, b in zip(line, prev))
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b, c = prev[i], prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out


class Page:
    def __init__(self):
        self.ops = []
        self.fields = []  # fillable text fields: (name, x, y, w, h, font size, align 0 left / 1 center, value)
        self.images = {}  # resource name: Png

    def image(self, png, x, y, w, h):
        name = "Im%d" % (id(png) % 100000)
        self.images[name] = png
        self.ops.append("q %.2f 0 0 %.2f %.2f %.2f cm /%s Do Q" % (w, h, x, y, name))

    def field(self, name, x, y, w, h, size=11, align=0, value=""):
        self.fields.append((name, x, y, w, h, size, align, value))

    def text(self, x, y, s, size=9, bold=False, align="left", gray=0.0):
        if align == "right":
            x -= text_width(s, size, bold)
        elif align == "center":
            x -= text_width(s, size, bold) / 2
        self.ops.append("%.3g g BT /%s %g Tf %.2f %.2f Td %s Tj ET" % (
            gray, "F2" if bold else "F1", size, x, y, pdf_str(s)))

    def line(self, x1, y1, x2, y2, width=0.5, gray=0.0):
        self.ops.append("%.3g G %g w %.2f %.2f m %.2f %.2f l S" % (gray, width, x1, y1, x2, y2))

    def rect(self, x, y, w, h, width=0.75, fill=None):
        if fill is not None:
            self.ops.append("%.3g g %.2f %.2f %.2f %.2f re f" % (fill, x, y, w, h))
        else:
            self.ops.append("0 G %g w %.2f %.2f %.2f %.2f re S" % (width, x, y, w, h))


def pdf_bytes(pages):
    """A PDF file from Page objects, with an AcroForm when pages have text fields
    (fillable in Preview, Acrobat and browsers)."""
    objs = [None, None,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    kids, fields, written = [], [], {}  # written: id(Png) -> image object number
    for pg in pages:
        xobjs = []
        for name, png in pg.images.items():
            if id(png) not in written:
                cs = "/DeviceRGB" if png.colors == 3 else "/DeviceGray"
                smask = ""
                if png.alpha:
                    objs.append(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
                                b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n"
                                % (png.w, png.h, len(png.alpha)) + png.alpha + b"\nendstream")
                    smask = " /SMask %d 0 R" % len(objs)
                parms = (" /DecodeParms << /Predictor 15 /Colors %d /BitsPerComponent 8 /Columns %d >>"
                         % (png.colors, png.w)) if png.predicted else ""
                objs.append(("<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s "
                             "/BitsPerComponent 8 /Filter /FlateDecode%s%s /Length %d >>\nstream\n"
                             % (png.w, png.h, cs, parms, smask, len(png.color))).encode() + png.color + b"\nendstream")
                written[id(png)] = len(objs)
            xobjs.append("/%s %d 0 R" % (name, written[id(png)]))
        data = zlib.compress("\n".join(pg.ops).encode("latin-1"))
        objs.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
        content, page = len(objs), len(objs) + 1
        objs.append(None)
        annots = []
        for name, x, y, w, h, size, align, value in pg.fields:
            # the appearance viewers draw until the field is edited: its value, or nothing
            ap = "/Tx BMC\n"
            if value:
                tx = (w - text_width(value, size)) / 2 if align == 1 else 2
                ap += "q BT /Helv %g Tf 0 g %.2f %.2f Td %s Tj ET Q\n" % (size, tx, (h - size * 0.7) / 2, pdf_str(value))
            ap = (ap + "EMC\n").encode("latin-1")
            objs.append(b"<< /Type /XObject /Subtype /Form /BBox [0 0 %.2f %.2f] "
                        b"/Resources << /Font << /Helv 3 0 R >> >> /Length %d >>\nstream\n" % (w, h, len(ap))
                        + ap + b"\nendstream")
            objs.append(("<< /Type /Annot /Subtype /Widget /FT /Tx /T %s /Rect [%.2f %.2f %.2f %.2f] /F 4 /P %d 0 R "
                         "/DA (/Helv %g Tf 0 g) /Q %d /MK << >>%s /AP << /N %d 0 R >> >>"
                         % (pdf_str(name), x, y, x + w, y + h, page, size, align,
                            " /V %s" % pdf_str(value) if value else "", len(objs))).encode("latin-1"))
            annots.append(len(objs))
        fields += annots
        objs[page - 1] = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                          "/Resources << /Font << /F1 3 0 R /F2 4 0 R >>%s >>%s >>"
                          % (PAGE_W, PAGE_H, content,
                             " /XObject << %s >>" % " ".join(xobjs) if xobjs else "",
                             " /Annots [%s]" % " ".join("%d 0 R" % a for a in annots) if annots else "")).encode()
        kids.append(page)
    form = (" /AcroForm << /Fields [%s] /NeedAppearances true /DA (/Helv 0 Tf 0 g) "
            "/DR << /Font << /Helv 3 0 R >> >> >>" % " ".join("%d 0 R" % f for f in fields)) if fields else ""
    objs[0] = ("<< /Type /Catalog /Pages 2 0 R%s >>" % form).encode()
    objs[1] = ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % k for k in kids), len(kids))).encode()
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


# --------------------------------------------------------------------------
# Reading text out of a PDF (uncompressed xref, Flate streams, ToUnicode fonts)
# --------------------------------------------------------------------------
TOKEN = re.compile(rb"""
    (?P<ws>\s+|%[^\r\n]*)
  | (?P<hex><[0-9A-Fa-f\s]*>)
  | (?P<dict><<|>>)
  | (?P<arr>[\[\]])
  | (?P<name>/[^\s/\[\]()<>{}%]*)
  | (?P<num>[+-]?(?:\d+\.?\d*|\.\d+))
  | (?P<lit>\()
  | (?P<op>[^\s/\[\]()<>{}%]+)
""", re.X)
ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def literal(data, i):
    """A (...) string starting just after the '(' at data[i]: (bytes, index after ')')."""
    out, depth = bytearray(), 1
    while i < len(data):
        c = data[i:i + 1]
        if c == b"\\":
            n = data[i + 1:i + 2]
            m = re.match(rb"[0-7]{1,3}", data[i + 1:i + 4])
            if m:
                out.append(int(m.group(), 8) & 0xFF)
                i += 1 + len(m.group())
                continue
            out += ESCAPES.get(n, b"" if n in b"\r\n" else n)
            i += 2
            continue
        if c == b"(":
            depth += 1
        elif c == b")":
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
        out += c
        i += 1
    return bytes(out), i


def tokens(data):
    """Content-stream tokens: ('str', bytes), ('num', float), ('name', str), ('op', str), '[' / ']'."""
    i = 0
    while i < len(data):
        m = TOKEN.match(data, i)
        if not m:
            i += 1
            continue
        kind, i = m.lastgroup, m.end()
        if kind == "ws" or kind == "dict":
            continue
        if kind == "hex":
            h = re.sub(rb"\s", b"", m.group()[1:-1])
            yield ("str", bytes.fromhex((h + b"0" * (len(h) % 2)).decode()))
        elif kind == "lit":
            s, i = literal(data, i)
            yield ("str", s)
        elif kind == "arr":
            yield m.group().decode()
        elif kind == "num":
            yield ("num", float(m.group()))
        elif kind == "name":
            yield ("name", m.group()[1:].decode("latin-1"))
        else:
            yield ("op", m.group().decode("latin-1"))


class PdfText:
    """Positioned text runs of each page: [(x, y, text, bold), ...]."""

    def __init__(self, data):
        self.objs = {int(m.group(1)): m.group(2)
                     for m in re.finditer(rb"(\d+)\s+\d+\s+obj\b(.*?)\bendobj", data, re.S)}
        if not self.objs:
            raise ValueError("no PDF objects found (not a PDF, or a compressed object stream)")
        self.fonts = {}

    def dict_of(self, n):
        b = self.objs.get(n, b"")
        s = b.find(b"stream")
        return b[:s] if s >= 0 else b

    def stream(self, n):
        b = self.objs.get(n, b"")
        s = b.find(b"stream")
        if s < 0:
            return b""
        raw = b[s + 6:b.rfind(b"endstream")]
        raw = raw[2:] if raw.startswith(b"\r\n") else raw[1:] if raw[:1] in b"\r\n" else raw
        if b"FlateDecode" in b[:s]:
            try:
                return zlib.decompressobj().decompress(raw)
            except zlib.error:
                return b""
        return raw

    @staticmethod
    def ref(text, key):
        m = re.search(rb"/" + key + rb"\s+(\d+)\s+\d+\s+R", text)
        return int(m.group(1)) if m else None

    def pages(self):
        root = self.ref(b"".join(self.dict_of(n) for n in self.objs if b"/Catalog" in self.dict_of(n)), b"Pages")
        out = []

        def walk(n, inherited):
            d = self.dict_of(n)
            res = self.ref(d, b"Resources") or inherited
            if re.search(rb"/Resources\s*<<", d):
                res = n
            if re.search(rb"/Type\s*/Pages\b", d):
                for k in re.findall(rb"(\d+)\s+\d+\s+R", re.search(rb"/Kids\s*\[(.*?)\]", d, re.S).group(1)):
                    walk(int(k), res)
            else:
                out.append((n, res))

        if root:
            walk(root, None)
        return out

    def font_map(self, res_obj):
        """{font resource name: (to-unicode decode function, is bold)} for a page."""
        d = self.dict_of(res_obj) if res_obj else b""
        m = re.search(rb"/Font\s*<<(.*?)>>", d, re.S)
        if not m and res_obj:
            n = self.ref(d, b"Font")
            m = re.search(rb"<<(.*)>>", self.dict_of(n), re.S) if n else None
        fonts = {}
        for name, n in re.findall(rb"/([^\s/]+)\s+(\d+)\s+\d+\s+R", m.group(1) if m else b""):
            fonts[name.decode("latin-1")] = self.font(int(n))
        return fonts

    def font(self, n):
        if n not in self.fonts:
            d = self.dict_of(n)
            bold = bool(re.search(rb"/BaseFont\s*/\S*(Bold|Black|Heavy)", d))
            cmap = self.to_unicode(self.ref(d, b"ToUnicode"))
            two_byte = b"/Type0" in d
            self.fonts[n] = (cmap, two_byte, bold)
        return self.fonts[n]

    def to_unicode(self, n):
        cmap = {}
        if n is None:
            return cmap
        s = self.stream(n)
        for block in re.findall(rb"beginbfchar(.*?)endbfchar", s, re.S):
            for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
                cmap[int(src, 16)] = bytes.fromhex(dst.decode()).decode("utf-16-be", "replace")
        for block in re.findall(rb"beginbfrange(.*?)endbfrange", s, re.S):
            for lo, hi, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", block):
                lo, hi = int(lo, 16), int(hi, 16)
                if dst.startswith(b"["):
                    for i, d in enumerate(re.findall(rb"<([0-9A-Fa-f]*)>", dst)):
                        cmap[lo + i] = bytes.fromhex(d.decode()).decode("utf-16-be", "replace")
                else:
                    base = bytes.fromhex(dst[1:-1].decode()).decode("utf-16-be", "replace")
                    for c in range(lo, min(hi, lo + 0xFFFF) + 1):
                        cmap[c] = base[:-1] + chr(ord(base[-1]) + c - lo) if base else ""
        return cmap

    @staticmethod
    def decode(s, font):
        cmap, two_byte, _ = font
        if two_byte:
            codes = [int.from_bytes(s[i:i + 2], "big") for i in range(0, len(s) - 1, 2)]
        else:
            codes = list(s)
        if not cmap:
            return s.decode("cp1252", "replace")
        return "".join(cmap.get(c, chr(c) if c < 128 else "") for c in codes)

    def page_runs(self, page, res):
        d = self.dict_of(page)
        m = re.search(rb"/Contents\s*\[(.*?)\]", d, re.S)
        ids = [int(k) for k in re.findall(rb"(\d+)\s+\d+\s+R", m.group(1))] if m else [self.ref(d, b"Contents")]
        data = b"\n".join(self.stream(n) for n in ids if n)
        fonts = self.font_map(res)
        runs, stack = [], []
        font, size, lead = (None, False, False), 10.0, 0.0
        tm = [1, 0, 0, 1, 0, 0]  # text line matrix
        for t in tokens(data):
            if t in ("[", "]"):
                stack.append(t)
                continue
            if t[0] != "op":
                stack.append(t)
                continue
            op = t[1]
            nums = [v for k, v in (x for x in stack if isinstance(x, tuple)) if k == "num"]
            if op == "BT":
                tm = [1, 0, 0, 1, 0, 0]
            elif op == "Tf" and stack and len(stack) >= 2:
                name = stack[-2][1] if isinstance(stack[-2], tuple) else None
                font = fonts.get(name, ({}, False, False))
                size = nums[-1] if nums else size
            elif op == "TL" and nums:
                lead = nums[-1]
            elif op in ("Td", "TD") and len(nums) >= 2:
                tx, ty = nums[-2], nums[-1]
                if op == "TD":
                    lead = -ty
                tm = [tm[0], tm[1], tm[2], tm[3], tm[4] + tx * tm[0] + ty * tm[2], tm[5] + tx * tm[1] + ty * tm[3]]
            elif op == "Tm" and len(nums) >= 6:
                tm = nums[-6:]
            elif op == "T*":
                tm = tm[:4] + [tm[4] - lead * tm[2], tm[5] - lead * tm[3]]
            elif op in ("Tj", "TJ", "'", '"'):
                if op in ("'", '"'):
                    tm = tm[:4] + [tm[4] - lead * tm[2], tm[5] - lead * tm[3]]
                parts = []
                for x in stack:
                    if isinstance(x, tuple) and x[0] == "str":
                        parts.append(self.decode(x[1], font))
                    elif isinstance(x, tuple) and x[0] == "num" and op == "TJ" and x[1] < -200:
                        parts.append(" ")  # a wide kerning gap is a word space
                text = "".join(parts)
                if text.strip():
                    runs.append((round(tm[4], 2), round(tm[5], 2), text, font[2]))
            stack = []
        return runs

    def all_pages(self):
        return [self.page_runs(p, r) for p, r in self.pages()]


# --------------------------------------------------------------------------
# Invoice layout
# --------------------------------------------------------------------------
SKIP_ITEMS = {"powder coating"}  # invoice lines that never go on the packing slip (any case)


def near(a, b, tol=1.5):
    return abs(a - b) <= tol


def parse_invoice(data):
    """{number, po, date, seller: [lines], ship_to: [lines],
    ship_label, items: [(item, qty)]} from invoice PDF bytes."""
    pages = PdfText(data).all_pages()
    if not pages:
        raise ValueError("no pages found")
    first = pages[0]

    def find(runs, text):
        return next((r for r in runs if r[2].strip().upper() == text), None)

    # header fields: bold "Label:" with its value on the same line to the right
    fields = []
    for x, y, s, b in first:
        if b and s.strip().endswith(":"):
            val = sorted((r for r in first if near(r[1], y, 1) and r[0] > x + 5), key=lambda r: r[0])
            if val:
                fields.append((s.strip().rstrip(":"), " ".join(r[2].strip() for r in val)))
    get = dict(fields).get

    # customer block: SHIP TO when there is one, else BILL TO
    head = find(first, "ITEMS")
    table_top = head[1] if head else 0
    blocks = {}
    for label in ("SHIP TO", "BILL TO"):
        r = find(first, label)
        if r:
            nxt = [o[0] for o in first if o[1] <= r[1] + 1 and o[0] > r[0] + 20
                   and o[2].strip().upper() in ("SHIP TO", "BILL TO")]
            right = min(nxt) if nxt else PAGE_W / 2
            lines = sorted((o for o in first if o[1] < r[1] - 1 and o[1] > table_top + 5
                            and r[0] - 2 <= o[0] < right), key=lambda o: -o[1])
            blocks[label] = [o[2].strip() for o in lines]
    ship_label = "SHIP TO" if "SHIP TO" in blocks else "BILL TO"
    ship_to = blocks.get(ship_label, [])

    # seller block: the text above the customer block, except the INVOICE title
    top = max((find(first, k)[1] for k in blocks if find(first, k)), default=table_top)
    seller = [r[2].strip() for r in sorted(first, key=lambda r: -r[1])
              if r[1] > top + 20 and r[2].strip().upper() != "INVOICE"]

    items = []
    for runs in pages:
        h = find(runs, "ITEMS")
        q = find(runs, "QUANTITY")
        if not h or not q:
            continue
        p = find(runs, "PRICE")
        q_lo, q_hi = q[0] - 60, (p[0] - 10 if p else q[0] + 120)
        for x, y, s, b in sorted(runs, key=lambda r: -r[1]):
            if not (b and near(x, h[0]) and y < h[1] - 1 and s.strip().upper() != "ITEMS"):
                continue
            qty = [r for r in runs if near(r[1], y, 1) and q_lo <= r[0] < q_hi
                   and re.fullmatch(r"[\d,]+(\.\d+)?", r[2].strip())]
            if qty:
                n = float(qty[0][2].replace(",", ""))
                items.append((s.strip(), int(n) if n == int(n) else n))
    if not items:
        raise ValueError("no invoice items found (expected an Items / Quantity table)")
    items = [(s, n) for s, n in items if s.lower() not in SKIP_ITEMS]
    if not items:
        raise ValueError("nothing to pack (only %s on the invoice)" % ", ".join(sorted(SKIP_ITEMS)))
    return {"number": get("Invoice Number") or "", "date": get("Invoice Date") or "",
            "po": next((v for k, v in fields if re.match(r"P\.?\s*O\.?\b|Purchase Order", k, re.I)), ""),
            "seller": seller, "ship_to": ship_to, "ship_label": ship_label, "items": items}


# --------------------------------------------------------------------------
# Packing slip PDF
# --------------------------------------------------------------------------
ROW_H = 26
COL_NUM, COL_ITEM, COL_ORD, COL_PACK = MARGIN + 4, MARGIN + 30, 400, 470  # qty columns are right edges / box left
BOX_W = 70


def qty_text(q):
    return "{:,}".format(q) if isinstance(q, int) else "{:,}".format(q).rstrip("0").rstrip(".")


def load_logo(path):
    """The logo as a Png, cached per path and modification time; None when there is no file."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key not in _logos:
        _logos.clear()
        _logos[key] = Png(path)
    return _logos[key]


_logos = {}


def packing_slip_pdf(inv, logo=LOGO):
    """The packing slip for a parse_invoice() result. inv["values"] (optional) pre-fills
    form fields by name: "P.O. Number" (when inv["po"] is blank), "Ship Date", "Qty Packed <n>",
    "Qty Packed Total", "Packed By", "Packed Date", "No. of Boxes", "Received By", "Received Date"."""
    values = {k: str(v) for k, v in (inv.get("values") or {}).items() if v not in (None, "")}
    png = load_logo(logo) if logo else None
    pages, pg = [], None
    y = 0.0
    items = inv["items"]
    title = "Packing Slip" + (" #%s" % inv["number"] if inv["number"] else "")

    def table_head():
        nonlocal y
        pg.rect(MARGIN, y - 7, PAGE_W - 2 * MARGIN, 22, fill=0.88)
        pg.text(COL_NUM, y, "#", size=9, bold=True)
        pg.text(COL_ITEM, y, "Item", size=9, bold=True)
        pg.text(COL_ORD, y, "Qty Ordered", size=9, bold=True, align="right")
        pg.text(COL_PACK + BOX_W / 2, y, "Qty Packed", size=9, bold=True, align="center")
        y -= 7

    def new_page():
        nonlocal pg, y
        pg = Page()
        pages.append(pg)
        if png:  # fit the logo in a LOGO_SIZE square at the top left, keeping its aspect ratio
            k = LOGO_SIZE / max(png.w, png.h)
            pg.image(png, MARGIN, PAGE_H - MARGIN - png.h * k, png.w * k, png.h * k)
        pg.text(PAGE_W - MARGIN, PAGE_H - MARGIN - 22, "PACKING SLIP", size=26, bold=True, align="right")
        sy = PAGE_H - MARGIN - 44
        for i, line in enumerate(inv["seller"][:6]):
            pg.text(PAGE_W - MARGIN, sy - 12 * i, line, size=10 if i else 11, bold=i == 0, align="right")
        y = min(PAGE_H - MARGIN - LOGO_SIZE, sy - 12 * (len(inv["seller"][:6]) - 1)) - 34
        if len(pages) == 1:
            pg.text(MARGIN, y, "SHIP TO", size=9, gray=0.45)
            for i, line in enumerate(inv["ship_to"][:6]):
                pg.text(MARGIN, y - 13 * (i + 1), fit(line, 10, 250, i == 0), size=10, bold=i == 0)
            # printed values, or a fillable line (None) when there is nothing to print
            info = [("Invoice Number", inv["number"]), ("P.O. Number", inv.get("po") or None),
                    ("Invoice Date", inv["date"]), ("Ship Date", None)]
            for i, (k, v) in enumerate(info):
                yy = y - 13 * i
                pg.text(445, yy, k + ":", size=10, bold=True, align="right")
                if v is None:
                    pg.line(452, yy - 2, PAGE_W - MARGIN, yy - 2, gray=0.4)
                    pg.field(k, 452, yy - 3, PAGE_W - MARGIN - 452, 13, size=10, value=values.get(k, ""))
                else:
                    pg.text(452, yy, v, size=10)
            y -= 13 * max(len(inv["ship_to"][:6]), len(info) - 1) + 34
        else:
            pg.text(MARGIN, y + 20, "%s (continued)" % title, size=10, gray=0.35)
        table_head()

    footer_h = 120
    new_page()
    for i, (item, qty) in enumerate(items, 1):
        last = i == len(items)
        if y - ROW_H - (ROW_H + footer_h if last else 0) < MARGIN + 20:
            new_page()
        top = y
        mid = top - ROW_H / 2 - 3
        pg.text(COL_NUM, mid, str(i), size=9, gray=0.45)
        pg.text(COL_ITEM, mid, fit(item, 11, COL_ORD - COL_ITEM - 80, True), size=11, bold=True)
        pg.text(COL_ORD, mid, qty_text(qty), size=12, align="right")
        pg.rect(COL_PACK, top - ROW_H + 4, BOX_W, ROW_H - 8)
        pg.field("Qty Packed %d" % i, COL_PACK + 1, top - ROW_H + 5, BOX_W - 2, ROW_H - 10, size=12, align=1,
                 value=values.get("Qty Packed %d" % i, ""))
        y = top - ROW_H
        pg.line(MARGIN, y, PAGE_W - MARGIN, y, width=0.25, gray=0.7)

    # totals and sign-off
    total = sum(q for _, q in items)
    y -= ROW_H
    pg.text(COL_ITEM, y + 6, "Total (%d line%s)" % (len(items), "" if len(items) == 1 else "s"), size=11, bold=True)
    pg.text(COL_ORD, y + 6, qty_text(total), size=12, bold=True, align="right")
    pg.rect(COL_PACK, y, BOX_W, ROW_H - 8, width=1.25)
    pg.field("Qty Packed Total", COL_PACK + 1, y + 1, BOX_W - 2, ROW_H - 10, size=12, align=1,
             value=values.get("Qty Packed Total", ""))
    for row in ((("Packed by", "Packed By", MARGIN, 200), ("Date", "Packed Date", MARGIN + 260, 120),
                 ("No. of boxes", "No. of Boxes", MARGIN + 430, 110)),
                (("Received by (print and sign)", "Received By", MARGIN, 380),
                 ("Date", "Received Date", MARGIN + 430, 110))):
        y -= 44 if row[0][0] == "Packed by" else 40
        for label, name, x, w in row:
            pg.line(x, y, x + w, y, gray=0.4)
            pg.text(x, y - 11, label, size=8, gray=0.45)
            pg.field(name, x, y + 1, w, 16, value=values.get(name, ""))

    for i, p in enumerate(pages, 1):
        p.text(PAGE_W - MARGIN, MARGIN - 12, "Page %d of %d for %s" % (i, len(pages), title), size=8, gray=0.45,
               align="right")
    return pdf_bytes(pages)


def output_path(src, inv, out, many):
    name = "Packing Slip %s.pdf" % (inv["number"] or os.path.splitext(os.path.basename(src))[0])
    if not out:
        return os.path.join(os.path.dirname(os.path.abspath(src)), name)
    if many or os.path.isdir(out) or out.endswith(os.sep):
        os.makedirs(out, exist_ok=True)
        return os.path.join(out, name)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Make a packing slip PDF from an invoice PDF.")
    ap.add_argument("invoices", nargs="+", help="invoice PDF file(s)")
    ap.add_argument("-o", "--out", help="output file (one invoice) or folder; default: next to each invoice")
    args = ap.parse_args(argv)
    status = 0
    for src in args.invoices:
        try:
            with open(src, "rb") as f:
                inv = parse_invoice(f.read())
        except (OSError, ValueError) as e:
            print("%s: %s" % (src, e), file=sys.stderr)
            status = 1
            continue
        path = output_path(src, inv, args.out, len(args.invoices) > 1)
        with open(path, "wb") as f:
            f.write(packing_slip_pdf(inv))
        print("%s -> %s  (%d items, %s pcs)" % (src, path, len(inv["items"]),
                                               qty_text(sum(q for _, q in inv["items"]))))
    return status


if __name__ == "__main__":
    sys.exit(main())
