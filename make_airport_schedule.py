import datetime
import os
import qrcode
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XlImage

OUT_XLSX = os.path.join(os.path.dirname(__file__), "Airport_Pickup_Schedule.xlsx")
TMP_DIR = os.path.join(os.path.dirname(__file__), "_qr_tmp")
os.makedirs(TMP_DIR, exist_ok=True)

wb = Workbook()

# ---------- Sheet 1: Instructions ----------
ws0 = wb.active
ws0.title = "Instructions"
ws0["A1"] = "Airport Pickup Schedule - Template"
ws0["A1"].font = Font(bold=True, size=14)
lines = [
    "How to use:",
    "1. Go to the 'Airport Pickup Schedule' sheet.",
    "2. Each passenger row has its own QR code (QR Code column).",
    "3. Passenger scans their row's QR code to get a code, then writes it in the",
    "   'Code' column of that same row.",
    "4. Enter each passenger's details (Name, Date of Departure, Flight Number, etc.)",
    "   on the row under the correct pickup time.",
    "5. Recommended Time of Pickup groups passengers by their pickup slot.",
    "6. Optional columns can be added/removed as needed.",
    "",
    "Columns:",
    "A - Recommended Time of Pickup",
    "B - NAME",
    "C - DATE OF DEPARTURE",
    "D - FLIGHT NUMBER",
    "E - Pickup Location",
    "F - Contact Number",
    "G - Vehicle",
    "H - Driver",
    "I - Remarks",
    "J - Pickup Status (Pending / Picked Up / Cancelled)",
    "K - QR Code (scan to get code)",
    "L - Code (filled by passenger after scanning QR)",
]
for i, t in enumerate(lines, start=3):
    ws0.cell(row=i, column=1, value=t)
ws0.column_dimensions["A"].width = 95

# ---------- Sheet 2: Schedule ----------
ws = wb.create_sheet("Airport Pickup Schedule")

thin = Side(style="thin", color="BFBFBF")
border = Border(left=thin, right=thin, top=thin, bottom=thin)
header_fill = PatternFill("solid", fgColor="D9E2F3")
header_font = Font(bold=True, size=10)

headers = [
    "Recommended Time\nof Pickup",
    "NAME",
    "DATE OF DEPARTURE",
    "FLIGHT NUMBER",
    "Pickup Location",
    "Contact Number",
    "Vehicle",
    "Driver",
    "Remarks",
    "Pickup Status",
    "QR Code",
    "Code",
]
widths = [16, 24, 18, 16, 22, 16, 14, 16, 24, 14, 13, 14]

ws["A1"] = "AIRPORT PICKUP SCHEDULE"
ws["A1"].font = Font(bold=True, size=14)
ws.merge_cells("A1:L1")
ws["A2"] = "Each passenger row has a QR code: scan it to get your code, then write the code in the Code column. All details stay in this single sheet."
ws["A2"].font = Font(italic=True, size=9, color="595959")
ws.merge_cells("A2:L2")
ws.row_dimensions[2].height = 28

HR = 4
for c, h in enumerate(headers, start=1):
    cell = ws.cell(row=HR, column=c, value=h)
    cell.font = header_font
    cell.fill = header_fill
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = border
ws.row_dimensions[HR].height = 32

for c, w in enumerate(widths, start=1):
    ws.column_dimensions[get_column_letter(c)].width = w

pickup_times = [
    datetime.time(0, 0), datetime.time(2, 0), datetime.time(4, 0),
    datetime.time(6, 0), datetime.time(8, 0), datetime.time(10, 0),
    datetime.time(12, 0), datetime.time(13, 0), datetime.time(15, 0),
    datetime.time(17, 0), datetime.time(18, 0), datetime.time(20, 0),
]

row = HR + 1
PASSENGERS_PER_SLOT = 3
code_counter = 1
for t in pickup_times:
    start = row
    tc = ws.cell(row=row, column=1, value=t)
    tc.number_format = "h:mm AM/PM"
    tc.alignment = Alignment(horizontal="right", vertical="center")
    tc.font = Font(bold=True, size=10)
    tc.border = border
    for _ in range(PASSENGERS_PER_SLOT):
        # generate unique code + QR for this passenger row
        code = f"PKP-{code_counter:04d}"
        qr_path = os.path.join(TMP_DIR, f"qr_{code}.png")
        q = qrcode.QRCode(version=1, box_size=4, border=1)
        q.add_data(code)
        q.make(fit=True)
        q.make_image(fill_color="black", back_color="white").save(qr_path)

        # borders for all cells
        for c in range(2, 13):
            ws.cell(row=row, column=c).border = border
        # QR image in column K
        qi = XlImage(qr_path)
        qi.width = 48
        qi.height = 48
        ws.add_image(qi, f"K{row}")
        # Code value pre-filled? Leave Code column (L) empty for passenger to fill.
        # But put the expected code as a hint in remarks? No. Leave blank.
        ws.row_dimensions[row].height = 52
        code_counter += 1
        row += 1
    ws.merge_cells(start_row=start, start_column=1, end_row=row - 1, end_column=1)
    ws.cell(row=start, column=1).alignment = Alignment(horizontal="right", vertical="center")
    ws.cell(row=start, column=1).border = border

last_row = row - 1
for r in range(HR, last_row + 1):
    for c in range(1, 13):
        ws.cell(row=r, column=c).border = border

ws.freeze_panes = "B5"

wb.save(OUT_XLSX)
print("Saved", OUT_XLSX, "with", code_counter - 1, "QR codes")

# cleanup temp pngs
for f in os.listdir(TMP_DIR):
    try:
        os.remove(os.path.join(TMP_DIR, f))
    except OSError:
        pass
try:
    os.rmdir(TMP_DIR)
except OSError:
    pass