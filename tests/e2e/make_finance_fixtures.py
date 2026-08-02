"""Generate the finance reconciliation e2e fixtures: a PDF bank statement + a CSV ledger.

Run:  uv run python tests/e2e/make_finance_fixtures.py
Writes /tmp/acme_ledger.csv and /tmp/acme_statement.pdf.

Planted discrepancies (a proper month-end bank reconciliation):
  1. Check 1042 — ledger 1,200.00 vs bank 1,250.00   (amount mismatch, $50)
  2. Service Charge 35.00 — on the bank statement, NOT in the ledger (bank-only fee)
  3. Deposit DEP-103 800.00 — in the ledger, NOT yet cleared (deposit in transit)
Everything else matches. Book-vs-bank gap = 800 + 50 + 35 = 885.00; adjusted both
sides tie to 13,258.75. Keys are intentionally messy: ledger "CHK-1042"/"DEP-101"
vs statement "Check 1042"/"Deposit - Globex" (tests entity resolution).
"""

from fpdf import FPDF
from fpdf.enums import XPos, YPos

LEDGER = """date,ref,description,amount
2026-07-02,DEP-101,Customer payment - Globex,2500.00
2026-07-05,CHK-1042,Rent - office,1200.00
2026-07-09,CHK-1043,Supplies - Staples,340.50
2026-07-15,DEP-102,Customer payment - Initech,1800.00
2026-07-22,CHK-1044,Utilities,215.75
2026-07-28,DEP-103,Customer payment - Umbrella,800.00
"""

STATEMENT_ROWS = [
    ("2026-07-02", "Deposit - Globex", "+2,500.00", "12,500.00"),
    ("2026-07-06", "Check 1042", "-1,250.00", "11,250.00"),
    ("2026-07-10", "Check 1043", "-340.50", "10,909.50"),
    ("2026-07-16", "Deposit - Initech", "+1,800.00", "12,709.50"),
    ("2026-07-23", "Check 1044", "-215.75", "12,493.75"),
    ("2026-07-31", "Service Charge", "-35.00", "12,458.75"),
]

_NEXT = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}


def main() -> None:
    with open("/tmp/acme_ledger.csv", "w") as f:
        f.write(LEDGER)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "First National Bank", **_NEXT)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, "Statement of Account - Acme Trading LLC", **_NEXT)
    pdf.cell(0, 7, "Account 0042-1188   Period: 01 Jul 2026 - 31 Jul 2026", **_NEXT)
    pdf.cell(0, 7, "Opening balance: 10,000.00", **_NEXT)
    pdf.ln(4)

    pdf.set_font("Courier", "B", 10)
    pdf.cell(30, 7, "Date", border=1)
    pdf.cell(70, 7, "Description", border=1)
    pdf.cell(40, 7, "Amount", border=1, align="R")
    pdf.cell(40, 7, "Balance", border=1, align="R", **_NEXT)
    pdf.set_font("Courier", "", 10)
    for date, desc, amount, balance in STATEMENT_ROWS:
        pdf.cell(30, 7, date, border=1)
        pdf.cell(70, 7, desc, border=1)
        pdf.cell(40, 7, amount, border=1, align="R")
        pdf.cell(40, 7, balance, border=1, align="R", **_NEXT)

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Closing balance: 12,458.75", **_NEXT)
    pdf.output("/tmp/acme_statement.pdf")
    print("wrote /tmp/acme_ledger.csv and /tmp/acme_statement.pdf")


if __name__ == "__main__":
    main()
