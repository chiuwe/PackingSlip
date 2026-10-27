# Packing Slip

`packing_slip.py` turns an invoice PDF (the Wave invoice layout) into a packing slip: the invoice's ship-to
(or bill-to) and invoice number, then each line's item and quantity ordered with an empty **Qty Packed** box to
fill in, a total, and lines for packed by, date, number of boxes and received by. Descriptions and prices are left off,
and so are line items on the ignore list.

## Line items to ignore

`config.json` lists invoice lines that never go on a packing slip (Powder Coating to start). Edit it in the
UI under **Settings**, or by hand:

```json
{ "ignore_items": ["Powder Coating", "Shipping", "Rush fee*"] }
```

Names match ignoring capitals and extra spaces, and `*` matches anything (`Rush fee*` also catches
"Rush fee - 2 day"). When an invoice loads, the UI says which lines it left off; the command line prints
them too. `--config FILE` points the command line at a different file.

## Usage

Run the browser UI to read an invoice into a table you can edit before exporting:

```
python3 slip_ui.py                 # opens http://127.0.0.1:8766/
```

Drop an invoice PDF on the page, or paste a Wave invoice link (the link in Wave's invoice emails, or
the invoice page it opens) into the box under it and click **Load**: the invoice number, date, ship-to and items fill in. Edit any of it, add,
remove or reorder lines, and optionally enter Qty packed (highlighted when short or over), ship date and
packed by, then **Export PDF** (saved to Downloads). Whatever you entered is pre-filled in the PDF's form
fields, and they stay editable in Preview. Invoices are read locally and never leave the machine.

Or from the command line:

```
python3 packing_slip.py ~/Downloads/Invoice_220_2026-10-14.pdf        # -> Packing Slip 220.pdf next to it
python3 packing_slip.py invoices/*.pdf -o "Packing slips"             # several, into a folder
python3 packing_slip.py https://link.waveapps.com/pdcwcx-9ukyka        # a Wave link -> current folder
python3 packing_slip.py invoice.pdf --ship-date "November 3, 2026"     # default: today; "" leaves it blank
```

The ship date is today unless changed (a date picker in the UI). A blank P.O. number or ship date stays
fillable in the PDF.

The Qty Packed boxes, total, Ship Date and sign-off lines are fillable form fields: type in them in Preview
(or Acrobat, or a browser) and save.

The logo at the top left of each page is `logo.png` in this folder: replace it with any 8-bit PNG
(transparency is kept), or delete it for no logo.

Wave links are read by `wave_link.py`, which asks Wave for the same PDF as the invoice page's Download
PDF button. That is the request Wave's own page makes, not a published API, so a change on Wave's side
can break it; downloading the PDF and dropping it in still works then. Only Wave addresses are fetched.

Python 3 standard library only. Tests: `python3 test/test_packing_slip.py`
