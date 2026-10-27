"""Tests for packing_slip.py: items and quantities read from a Wave-style invoice PDF
(hex strings, ToUnicode fonts, items continuing across pages), and the slip PDF lists them.

    python3 test/test_packing_slip.py
"""
import pathlib
import re
import sys
import tempfile
import unittest
import zlib

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
import packing_slip  # noqa: E402
import slip_ui  # noqa: E402
import wave_link  # noqa: E402

CMAP = (b"/CIDInit /ProcSet findresource begin begincmap 1 begincodespacerange <00><ff> endcodespacerange "
        b"1 beginbfrange <20><7e><0020> endbfrange endcmap end")


def invoice_pdf(pages):
    """A PDF laid out like a Wave invoice; pages are lists of (x, y, text, bold)."""
    objs = {3: b"<< /Type /Font /Subtype /TrueType /BaseFont /ab+Sans-Regular /ToUnicode 5 0 R >>",
            4: b"<< /Type /Font /Subtype /TrueType /BaseFont /ab+Sans-Bold /ToUnicode 5 0 R >>",
            5: b"<< /Length %d /Filter [/FlateDecode] >>\nstream\n" % len(zlib.compress(CMAP))
            + zlib.compress(CMAP) + b"\nendstream"}
    kids = []
    for runs in pages:
        ops = "\n".join("BT\n%s %s Td\n/%s 10 Tf\n<%s> Tj\nET" % (x, y, "F2.0" if b else "F1.0", s.encode().hex())
                        for x, y, s, b in runs).encode()
        n = max(objs) + 1
        objs[n] = b"<< /Length %d /Filter [/FlateDecode] >>\nstream\n" % len(zlib.compress(ops)) \
            + zlib.compress(ops) + b"\nendstream"
        objs[n + 1] = (b"<< /Type /Page /Parent 2 0 R /Contents %d 0 R "
                       b"/Resources << /Font << /F1.0 3 0 R\n/F2.0 4 0 R\n>> >> >>" % n)
        kids.append(n + 1)
    objs[1] = b"<< /Type /Catalog\n/Pages 2 0 R\n>>"
    objs[2] = b"<< /Type /Pages /Count %d /Kids [%s] >>" % (len(kids), b" ".join(b"%d 0 R" % k for k in kids))
    return b"%PDF-1.4\n" + b"".join(b"%d 0 obj\n%s\nendobj\n" % (n, objs[n]) for n in sorted(objs))


HEADER = [(21.5, 535, "Items", True), (301.7, 535, "Quantity", True), (466.3, 535, "Price", True),
          (554.4, 535, "Amount", True)]
PAGE1 = [(468, 740, "INVOICE", False), (520, 710, "Acme Fab", True), (447, 698, "1 Main St", False),
         (21.5, 616, "BILL TO", False), (21.5, 604, "Widget Co.", True), (21.5, 591, "Pat Doe", False),
         (404, 618, "Invoice Number:", True), (490, 618, "77", False),
         (395, 610, "P.O./S.O. Number:", True), (490, 610, "PO5781", False),
         (420, 601, "Invoice Date:", True), (490, 601, "May 1, 2026", False),
         (391, 568, "Amount Due (USD):", True), (490, 568, "$0.00", True)] + HEADER + [
    (21.5, 509, "PRD-001 Rev B", True), (21.5, 497, '1/8" Alum', False), (317.3, 509, "14", False),
    (457.6, 509, "$50.00", False), (553.7, 509, "$700.00", False), (21.5, 484, "Bent", False),
    (21.5, 79, "PRD-002", True), (319.1, 79, "1,200", False), (457.6, 79, "$1.00", False),
    (243, 25, "Page 1 of 2 for Invoice #77", False)]
PAGE2 = [(21.5, 597, "Items", True), (301.7, 597, "Quantity", True), (466.3, 597, "Price", True),
         (21.5, 578, "M6 Pemnut", False),  # PRD-002's description continued
         (21.5, 552, "Powder Coating", True), (319.1, 552, "1", False), (453, 552, "$500.00", False),
         (390, 300, "Subtotal:", True), (553, 300, "$2,400.00", False)]


IGNORE = ["Powder Coating"]  # explicit, so tests don't depend on the local config.json


class PackingSlipTest(unittest.TestCase):
    def setUp(self):
        self.inv = packing_slip.parse_invoice(invoice_pdf([PAGE1, PAGE2]), IGNORE)

    def test_items(self):
        self.assertEqual(self.inv["items"], [("PRD-001 Rev B", 14), ("PRD-002", 1200)])  # no Powder Coating
        self.assertEqual(self.inv["ignored"], [("Powder Coating", 1)])

    def test_ignore_patterns(self):
        pdf = invoice_pdf([PAGE1, PAGE2])
        names = lambda ignore: [s for s, _ in packing_slip.parse_invoice(pdf, ignore)["items"]]
        self.assertEqual(names([]), ["PRD-001 Rev B", "PRD-002", "Powder Coating"])
        self.assertEqual(names(["prd-001*", "POWDER  coating"]), ["PRD-002"])  # case, spaces, wildcard
        self.assertEqual(names(["PRD-001"]), ["PRD-001 Rev B", "PRD-002", "Powder Coating"])  # whole name only
        self.assertTrue(packing_slip.ignored("Bracket [A]?", ["bracket [a]?"]))  # only * is special
        self.assertFalse(packing_slip.ignored("Bracket A", ["bracket [a]"]))

    def test_only_powder_coating(self):
        page = HEADER + [(21.5, 509, "POWDER COATING", True), (319.1, 509, "1", False)]
        with self.assertRaisesRegex(ValueError, "nothing to pack"):
            packing_slip.parse_invoice(invoice_pdf([page]), IGNORE)

    def test_header(self):
        self.assertEqual(self.inv["number"], "77")
        self.assertEqual(self.inv["date"], "May 1, 2026")
        self.assertEqual(self.inv["po"], "PO5781")
        self.assertEqual(self.inv["ship_to"], ["Widget Co.", "Pat Doe"])
        self.assertEqual(self.inv["seller"], ["Acme Fab", "1 Main St"])

    def test_slip_reads_back(self):
        pdf = packing_slip.packing_slip_pdf(self.inv)
        self.assertTrue(pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF"))
        text = [r[2] for page in packing_slip.PdfText(pdf).all_pages() for r in page]
        for s in ("PACKING SLIP", "Qty Ordered", "Qty Packed", "PRD-001 Rev B", "14", "1,200", "1,214",
                  "Widget Co.", "77", "P.O. Number:", "PO5781"):
            self.assertIn(s, text)
        for s in ('1/8" Alum', "Bent", "$50.00", "Powder Coating"):  # descriptions, prices, powder coating left off
            self.assertNotIn(s, text)

    def test_fillable_fields(self):
        pdf = packing_slip.packing_slip_pdf(self.inv)
        self.assertIn(b"/AcroForm", pdf)
        names = re.findall(rb"/FT /Tx /T \(([^)]*)\)", pdf)
        self.assertEqual([n.decode() for n in names],
                         ["Ship Date", "Qty Packed 1", "Qty Packed 2", "Qty Packed Total", "Packed By",
                          "Packed Date", "No. of Boxes", "Received By", "Received Date"])

    def test_long_invoice_paginates(self):
        inv = dict(self.inv, items=[("PRD-%03d" % i, 4) for i in range(60)])
        pages = packing_slip.PdfText(packing_slip.packing_slip_pdf(inv)).all_pages()
        self.assertGreater(len(pages), 1)
        text = [r[2] for page in pages for r in page]
        self.assertEqual(sum(t.startswith("PRD-") for t in text), 60)
        self.assertIn("Page %d of %d for Packing Slip #77" % (len(pages), len(pages)), text)

    def test_not_an_invoice(self):
        with self.assertRaises(ValueError):
            packing_slip.parse_invoice(invoice_pdf([[(21.5, 700, "Hello", True)]]), IGNORE)

    def test_po_and_ship_date(self):
        def header(**values):
            pdf = packing_slip.packing_slip_pdf(dict(self.inv, po="", values=values))
            text = [r[2] for r in packing_slip.PdfText(pdf).all_pages()[0]]
            return text, re.findall(rb"/FT /Tx /T \(([^)]*)\)", pdf)
        text, fields = header()  # blank: fillable fields
        self.assertIn(b"P.O. Number", fields)
        self.assertIn(b"Ship Date", fields)
        text, fields = header(**{"P.O. Number": "PO-9", "Ship Date": "October 26, 2026"})  # given: printed text
        self.assertIn("PO-9", text)
        self.assertIn("October 26, 2026", text)
        self.assertNotIn(b"P.O. Number", fields)
        self.assertNotIn(b"Ship Date", fields)

    def test_prefilled_fields(self):
        inv = dict(self.inv, values={"Qty Packed 2": "1,100", "Packed By": "Sam", "Ship Date": ""})
        pdf = packing_slip.packing_slip_pdf(inv)
        self.assertIn(b"/T (Qty Packed 2)", pdf)
        self.assertEqual(sorted(re.findall(rb"/V \(([^)]*)\)", pdf)), [b"1,100", b"Sam"])  # blank stays unfilled


def png_bytes(w, h, pixels, ctype):
    """A PNG of rows of pixel tuples, each row using a different filter (0-4) to cover unfilter()."""
    bpp = len(pixels[0][0])
    raw, prev = bytearray(), bytes(w * bpp)
    for y, row in enumerate(pixels):
        line = bytes(v for px in row for v in px)
        ft = y % 5
        out = bytearray()
        for i, v in enumerate(line):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = [0, a, b, (a + b) >> 1, a if pa <= pb and pa <= pc else b if pb <= pc else c][ft]
            out.append((v - pred) & 0xFF)
        raw += bytes([ft]) + out
        prev = line

    def chunk(kind, body):
        return len(body).to_bytes(4, "big") + kind + body + zlib.crc32(kind + body).to_bytes(4, "big")
    ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, ctype, 0, 0, 0])
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


class LogoTest(unittest.TestCase):
    def write(self, data):
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        f.write(data)
        f.close()
        self.addCleanup(pathlib.Path(f.name).unlink)
        return f.name

    def test_rgba_flattened_onto_white(self):
        pixels = [[((x * 40 + y) % 256, (y * 30) % 256, (x * y * 7) % 256, (x * 50 + y * 9) % 256)
                   for x in range(6)] for y in range(7)]
        png = packing_slip.Png(self.write(png_bytes(6, 7, pixels, 6)))
        self.assertEqual((png.w, png.h, png.colors, png.predicted, png.alpha), (6, 7, 3, False, None))
        over_white = lambda c, a: 255 - ((255 - c) * a + 127) // 255
        self.assertEqual(zlib.decompress(png.color),
                         bytes(over_white(v, px[3]) for row in pixels for px in row for v in px[:3]))

    def test_black_on_transparent_becomes_gray(self):  # the logo: no mask, transparent prints white
        pixels = [[(0, 0, 0, a) for a in (0, 128, 255)]]
        png = packing_slip.Png(self.write(png_bytes(3, 1, pixels, 6)))
        self.assertEqual((png.colors, png.alpha), (1, None))
        self.assertEqual(zlib.decompress(png.color), bytes([255, 127, 0]))

    def test_slip_with_logo(self):
        path = self.write(png_bytes(4, 4, [[(0, 0, 0, 255 * (x == y)) for x in range(4)] for y in range(4)], 6))
        inv = packing_slip.parse_invoice(invoice_pdf([PAGE1, PAGE2]), IGNORE)
        pdf = packing_slip.packing_slip_pdf(inv, logo=path)
        self.assertEqual(len(re.findall(rb"/Subtype /Image", pdf)), 1)  # one image shared by every page
        self.assertNotIn(b"/SMask", pdf)  # no transparency for printers to get wrong
        self.assertNotIn(b"/Subtype /Image", packing_slip.packing_slip_pdf(inv, logo=None))
        self.assertNotIn(b"/Subtype /Image", packing_slip.packing_slip_pdf(inv, logo=path + ".missing"))

    def test_shipped_logo_loads(self):
        png = packing_slip.load_logo(packing_slip.LOGO)
        self.assertIsNotNone(png, "logo.png is missing next to packing_slip.py")
        self.assertEqual(len(zlib.decompress(png.color)), png.w * png.h * png.colors)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = str(pathlib.Path(d.name) / "config.json")

    def test_defaults_when_missing(self):
        self.assertEqual(packing_slip.load_config(self.path)["ignore_items"], ["Powder Coating"])

    def test_save_cleans_and_round_trips(self):
        saved = packing_slip.save_config({"ignore_items": [" Shipping ", "", "shipping", "Powder  Coat*"]}, self.path)
        self.assertEqual(saved["ignore_items"], ["Shipping", "Powder Coat*"])
        self.assertEqual(packing_slip.load_config(self.path), saved)
        packing_slip.save_config({"ignore_items": []}, self.path)
        self.assertEqual(packing_slip.load_config(self.path)["ignore_items"], [])  # empty list is kept

    def test_bad_config(self):
        with self.assertRaises(ValueError):
            packing_slip.save_config({"ignore_items": "Shipping"}, self.path)
        pathlib.Path(self.path).write_text("{not json")
        with self.assertRaisesRegex(ValueError, "config.json"):
            packing_slip.load_config(self.path)

    def test_local_config_is_valid(self):  # whatever is ignored locally, config.json must load
        self.assertIsInstance(packing_slip.load_config()["ignore_items"], list)


class SlipUiTest(unittest.TestCase):
    def test_page_state_to_slip(self):
        inv = slip_ui.slip_from_page({
            "number": " 77 ", "po": " PO5781 ", "date": "May 1", "ship_to": "Widget Co.\n\n  Pat Doe \n", "seller": "Acme",
            "values": {"Ship Date": "May 2"},
            "items": [{"item": "A", "qty": "1,200", "packed": "1200"}, {"item": "  ", "qty": ""},
                      {"item": "B", "qty": "2.50", "packed": ""}]})
        self.assertEqual(inv["number"], "77")
        self.assertEqual(inv["po"], "PO5781")
        self.assertEqual(inv["ship_to"], ["Widget Co.", "Pat Doe"])
        self.assertEqual(inv["items"], [("A", 1200), ("B", 2.5)])  # blank lines dropped
        self.assertEqual(inv["values"], {"Ship Date": "May 2", "Qty Packed 1": "1200", "Qty Packed 2": ""})
        self.assertTrue(packing_slip.packing_slip_pdf(inv).startswith(b"%PDF-"))

    def test_bad_quantity(self):
        with self.assertRaisesRegex(ValueError, "line 1 \\(A\\)"):
            slip_ui.slip_from_page({"items": [{"item": "A", "qty": "four"}]})
        with self.assertRaisesRegex(ValueError, "no items"):
            slip_ui.slip_from_page({"items": []})


class WaveLinkTest(unittest.TestCase):  # no network: only address handling
    PAGE = ("https://next.waveapps.com/a/invoices/4cc6c564-0cf8-4209-99e4-020fd2a12448/public/"
            "2639881692139036610/72f7a247a0fd422cabd3d6b70f7cc16a")

    def test_ids_from_page(self):
        self.assertEqual(wave_link.ids(self.PAGE), ("4cc6c564-0cf8-4209-99e4-020fd2a12448", "2639881692139036610",
                                                   "72f7a247a0fd422cabd3d6b70f7cc16a"))
        self.assertEqual(wave_link.page_url(self.PAGE[len("https://"):]), self.PAGE)  # scheme optional

    def test_only_wave_addresses(self):
        self.assertTrue(wave_link.is_wave_link("https://link.waveapps.com/pdcwcx-9ukyka"))
        self.assertTrue(wave_link.is_wave_link(self.PAGE))
        self.assertTrue(wave_link.is_wave_link("link.waveapps.com/pdcwcx-9ukyka"))
        for bad in ("https://example.com/a/invoices/x", "https://link.waveapps.com.evil.example/x", "invoice.pdf"):
            self.assertFalse(wave_link.is_wave_link(bad))
        with self.assertRaisesRegex(ValueError, "not a Wave invoice link"):
            wave_link.page_url("https://example.com/pdcwcx-9ukyka")
        with self.assertRaisesRegex(ValueError, "not a Wave public invoice page"):
            wave_link.ids("https://next.waveapps.com/a/invoices/")


if __name__ == "__main__":
    unittest.main()
