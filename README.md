# Packing Slip

`packing_slip.py` turns an invoice PDF (the Wave invoice layout) into a packing slip: the invoice's ship-to
(or bill-to) and invoice number, then each line's item and quantity ordered with an empty **Qty Packed** box to
fill in, a total, and lines for packed by, date, number of boxes and received by. Descriptions and prices are left off,
and Powder Coating lines are never listed (`SKIP_ITEMS` in `packing_slip.py`).

Run the browser UI to read an invoice into a table you can edit before exporting:

```
python3 slip_ui.py                 # opens http://127.0.0.1:8766/
```

Drop an invoice PDF on the page: the invoice number, date, ship-to and items fill in. Edit any of it, add,
remove or reorder lines, and optionally enter Qty packed (highlighted when short or over), ship date and
packed by, then **Export PDF** (saved to Downloads). Whatever you entered is pre-filled in the PDF's form
fields, and they stay editable in Preview. Invoices are read locally and never leave the machine.

Or from the command line:

```
python3 packing_slip.py ~/Downloads/Invoice_220_2026-10-14.pdf        # -> Packing Slip 220.pdf next to it
python3 packing_slip.py invoices/*.pdf -o "Packing slips"             # several, into a folder
```

The Qty Packed boxes, total, Ship Date and sign-off lines are fillable form fields: type in them in Preview
(or Acrobat, or a browser) and save.

The logo at the top left of each page is `logo.png` in this folder: replace it with any 8-bit PNG
(transparency is kept), or delete it for no logo.

Python 3 standard library only. Tests: `python3 test/test_packing_slip.py`
