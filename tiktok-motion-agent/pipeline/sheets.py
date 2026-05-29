import json
import sys

from .config import COLUMNS, STATUS_VALUES, STATUS_COLORS, require_env
from .utils import sheet_col

try:
    import gspread
except Exception:
    gspread = None


def get_sheet():
    if gspread is None:
        raise RuntimeError("gspread is not installed")
    cred_path = require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    spreadsheet_id = require_env("SPREADSHEET_ID")
    gc = gspread.service_account(filename=cred_path)
    return gc.open_by_key(spreadsheet_id).sheet1


def ensure_sheet_header(ws, apply_controls: bool = False):
    existing = ws.row_values(1)
    changed = existing != COLUMNS
    if changed:
        end_col = sheet_col(len(COLUMNS) - 1)
        ws.update(f"A1:{end_col}1", [COLUMNS])
        if len(COLUMNS) < 26:
            clear_start = sheet_col(len(COLUMNS))
            ws.batch_clear([f"{clear_start}1:Z1"])
    if changed or apply_controls:
        sync_sheet_table_columns(ws)
        ensure_sheet_status_controls(ws)


def sync_sheet_table_columns(ws):
    """Keep Google Sheets Table metadata aligned with COLUMNS.

    Google Sheets typed table columns (including dropdowns) are not updated by
    normal header/data-validation calls, so update the table definition too.
    """
    try:
        metadata = ws.spreadsheet.fetch_sheet_metadata()
        table = None
        for sheet in metadata.get("sheets", []):
            if sheet.get("properties", {}).get("sheetId") == ws.id:
                tables = sheet.get("tables") or []
                table = tables[0] if tables else None
                break
        if not table:
            return

        table_range = dict(table.get("range") or {})
        table_range["sheetId"] = ws.id
        table_range.setdefault("startRowIndex", 0)
        table_range.setdefault("startColumnIndex", 0)
        table_range["endColumnIndex"] = len(COLUMNS)
        table_range["endRowIndex"] = max(table_range.get("endRowIndex", 0), max(ws.row_count, 1000))

        old_by_name = {c.get("columnName"): c for c in table.get("columnProperties", [])}
        column_properties = []
        for index, name in enumerate(COLUMNS):
            prop = dict(old_by_name.get(name) or {})
            prop["columnIndex"] = index
            prop["columnName"] = name
            if name == "status":
                prop["columnType"] = "DROPDOWN"
                prop["dataValidationRule"] = {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": s} for s in STATUS_VALUES],
                    }
                }
            column_properties.append(prop)

        ws.spreadsheet.batch_update({
            "requests": [{
                "updateTable": {
                    "table": {
                        "tableId": table["tableId"],
                        "range": table_range,
                        "columnProperties": column_properties,
                    },
                    "fields": "range,columnProperties",
                }
            }]
        })
    except Exception as e:
        print(f"table metadata sync skipped: {e}", file=sys.stderr)


def ensure_sheet_status_controls(ws):
    """Add status dropdown enum and color marks to the Google Sheet.

    The local CSV remains plain CSV; the enum/color UX is applied in Google
    Sheets whenever gspread is available.
    """
    from gspread.utils import ValidationConditionType

    status_col = sheet_col(COLUMNS.index("status"))
    status_range = f"{status_col}2:{status_col}1000"
    try:
        ws.add_validation(
            status_range,
            ValidationConditionType.one_of_list,
            STATUS_VALUES,
            inputMessage="Choose a pipeline status",
            strict=True,
            showCustomUi=True,
        )
    except Exception as e:
        # Some Google Sheets/table typed columns reject classic data validation.
        # Keep formatting working and leave existing dropdowns/chips intact.
        print(f"status validation skipped: {e}", file=sys.stderr)
    apply_status_conditional_colors(ws)
    apply_status_colors(ws)


def status_text_format(status: str):
    return {"foregroundColor": {"red": 0, "green": 0, "blue": 0}, "bold": True}


def apply_status_conditional_colors(ws):
    """Install conditional formatting so status colors stay distinct.

    Direct cell formats can be hard to notice when Google Sheets renders
    dropdown chips. Conditional rules are more durable for future edits.
    """
    sheet_id = ws.id
    status_idx = COLUMNS.index("status")
    requests = []
    # Add rules in reverse at index 0 so final priority follows STATUS_VALUES.
    for status in reversed(STATUS_VALUES):
        fmt = {
            **STATUS_COLORS[status],
            "textFormat": status_text_format(status),
        }
        requests.append({
            "addConditionalFormatRule": {
                "index": 0,
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 1000,
                        "startColumnIndex": status_idx,
                        "endColumnIndex": status_idx + 1,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "TEXT_EQ",
                            "values": [{"userEnteredValue": status}],
                        },
                        "format": fmt,
                    },
                },
            }
        })
    ws.spreadsheet.batch_update({"requests": requests})


def apply_status_colors(ws):
    status_col = sheet_col(COLUMNS.index("status"))
    values = ws.col_values(COLUMNS.index("status") + 1)
    formats = []
    for row_index, status in enumerate(values[1:], start=2):
        if not status:
            continue
        fmt = STATUS_COLORS.get(status)
        if fmt:
            formats.append({"range": f"{status_col}{row_index}", "format": fmt})
    if formats:
        ws.batch_format(formats)


def append_sheet(row: dict):
    from .storage import normalize_row, validate_status
    row = normalize_row(row)
    validate_status(row)
    ws = get_sheet()
    ensure_sheet_header(ws)
    ws.append_row([row.get(c, "") for c in COLUMNS], value_input_option="RAW")


def upsert_sheet(row: dict):
    from .storage import normalize_row, validate_status
    row = normalize_row(row)
    validate_status(row)
    ws = get_sheet()
    ensure_sheet_header(ws)
    values = [row.get(c, "") for c in COLUMNS]
    job_id = row.get("job_id")
    row_index = None
    if job_id:
        for idx, value in enumerate(ws.col_values(COLUMNS.index("job_id") + 1), start=1):
            if idx > 1 and value == job_id:
                row_index = idx
                break
    if row_index:
        end_col = sheet_col(len(COLUMNS) - 1)
        ws.update(f"A{row_index}:{end_col}{row_index}", [values])
    else:
        ws.append_row(values, value_input_option="RAW")
        row_index = len(ws.col_values(1))
    status = row.get("status")
    fmt = STATUS_COLORS.get(status)
    if fmt and row_index:
        status_col = sheet_col(COLUMNS.index("status"))
        ws.format(f"{status_col}{row_index}", fmt)
