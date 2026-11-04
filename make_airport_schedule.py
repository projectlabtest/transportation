import datetime
import os
import qrcode
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XlImage

OUT_XLSX = os.path.join(os.path.dirname(__file__), "Airport_Pickup_Schedule.xlsx")
QR_PNG = os.path.join(os.path.dirname(__file__), "_qr_airport.png")

# One QR code the passenger scans to open the fill-up form.
qr = qrcode.QRCode(version=3, box_size=8, border=2)
qr.add_data("LHC Transport - Airport Pickup Form: http://transport.local/airport-pickup")
qr.make(fit=True)
qr.make_image(fill_color="black", back_color="white").save(QR_PNG)

wb = Workbook()

# ---------- Sheet 1: Instructions ----------
ws0 = wb.active
ws0.title = "Instructions"
ws0["A1"] = "Airport Pickup Schedule - Template"
ws0["A1"].font = Font(bold=True, size=14)
lines = [
    "How to use:",
    "1. Go to the 'Airport Pickup Schedule' sheet.",
    "2. Passenger scans the single QR code (top of the sheet) to open the fill-up form.",
    "3. Passenger fills up the code, then writes it in the 'Code' column of their row.",
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
    "K - Code (filled by passenger after scanning QR)",
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
    "Code",
]
widths = [16, 24, 18, 16, 22, 16, 14, 16, 24, 14, 14]

ws["A1"] = "AIRPORT PICKUP SCHEDULE"
ws["A1"].font = Font(bold=True, size=14)
ws.merge_cells("A1:K1")

ws["A2"] = "SCAN THIS QR CODE TO FILL UP THE FORM:"
ws["A2"].font = Font(bold=True, size=10)
ws["B2"] = ""  # placeholder
# Place QR image at top-right area (column K) spanning rows 2-6
qr_img = XlImage(QR_PNG)
qr_img.width = 110
qr_img.height = 110
ws.add_image(qr_img, "K2")

ws["A3"] = "Passenger scans the QR code, fills up the code, then writes it in the Code column. All details stay in this single sheet."
ws["A3"].font = Font(italic=True, size=9, color="595959")
ws.merge_cells("A3:J3")
ws.row_dimensions[2].height = 118
ws.row_dimensions[3].height = 26

HR = 5
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
for t in pickup_times:
    start = row
    tc = ws.cell(row=row, column=1, value=t)
    tc.number_format = "h:mm AM/PM"
    tc.alignment = Alignment(horizontal="right", vertical="center")
    tc.font = Font(bold=True, size=10)
    tc.border = border
    for _ in range(PASSENGERS_PER_SLOT):
        for c in range(2, 12):
            ws.cell(row=row, column=c).border = border
        ws.row_dimensions[row].height = 18
        row += 1
    ws.merge_cells(start_row=start, start_column=1, end_row=row - 1, end_column=1)
    ws.cell(row=start, column=1).alignment = Alignment(horizontal="right", vertical="center")
    ws.cell(row=start, column=1).border = border

last_row = row - 1
for r in range(HR, last_row + 1):
    for c in range(1, 12):
        ws.cell(row=r, column=c).border = border

ws.freeze_panes = "B6"

wb.save(OUT_XLSX)
print("Saved", OUT_XLSX)

try:
    os.remove(QR_PNG)
except OSError:
    pass