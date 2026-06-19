# Sample invoices

Two PDFs with the **same content** so they're a controlled pair for
testing both extraction paths the agent supports:

| File | Has text layer? | Extraction path the agent will take |
|---|---|---|
| `invoice-text.pdf`    | yes  | `pdftotext input.pdf -` → done |
| `invoice-scanned.pdf` | no   | `pdftoppm` rasterise → `tesseract` OCR → done |

Both contain the same invoice for `Contoso Office Supplies, Inc.` →
`Fabrikam Engineering, LLC`, 4 line items, subtotal **$3,774.94**,
tax **$328.42** (8.7% WA), total **$4,103.36**.

Drop either one in `/<your input folder>/` to test. The two
versions extract to the same JSON, so you can validate the OCR path
gives matching numbers.

To regenerate (e.g., to change the values), edit + re-run
[`../generate_invoices.py`](../generate_invoices.py).
