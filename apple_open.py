# -*- coding: utf-8 -*-
"""APPLE vendor open profile (UFS+ TOOL OPEN menu).
Contract: PROFILE_NAME + run(ctx). Supports Target MAWB (ctx.only_mawbs / ctx.is_target).
"""
import os
import re
import time
from datetime import datetime

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

PROFILE_NAME = "APPLE"
PROFILE_ID = "apple"

_OpenContext = object  # type hint only (the host passes the real ctx)


def _open_extract_service_type_from_text(raw_text):
    if not raw_text:
        return None, None
    raw_str = str(raw_text).strip()
    if not raw_str:
        return None, None

    clean = re.sub(r'[^A-Z0-9]', '', raw_str.upper())

    m_after = re.search(r'(?:SERVICE|SERIVCE|SRVICE|SERVC|SVC|SRV)(.*)', clean)
    if m_after:
        rest = m_after.group(1)
        if any(k in rest for k in ["AIR", "AP", "AS"]):
            return "AS", "AS"
        elif "AD" in rest:
            return "AD", "AD"

    m_pre = re.search(r'(.*?)(?:SERVICE|SERIVCE|SRVICE|SERVC|SVC|SRV)', clean)
    if m_pre:
        pre = m_pre.group(1)
        if any(k in pre for k in ["AIR", "AP", "AS"]):
            return "AS", "AS"
        elif "AD" in pre:
            return "AD", "AD"

    upper_raw = raw_str.upper()
    if any(w in upper_raw for w in ["SERVICE", "SERIVCE", "SVC"]):
        if any(k in upper_raw for k in ["AIR", "AP", "AS"]):
            if "AD" in upper_raw and not any(k in upper_raw for k in ["AIR", "AP", "AS"]):
                return "AD", "AD"
            return "AS", "AS"
        elif "AD" in upper_raw:
            return "AD", "AD"

    return None, None


def _open_check_pup_date_from_ui(driver, hawb_no="", log_fn=None):
    if log_fn:
        log_fn(f"[INFO] HAWB {hawb_no}: checking PUP milestone")
    unique_parsed_dates = {}
    found_pup_date_strings = []

    try:
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.XPATH, "//input[@name='milestone']"))
            )
        except Exception:
            pass

        milestone_inputs = driver.find_elements(By.XPATH, "//input[@name='milestone']")
        for minp in milestone_inputs:
            try:
                m_val = (minp.get_attribute("value") or driver.execute_script("return arguments[0].value;", minp) or "").strip().upper()
                if m_val == "PUP":
                    row_el = minp.find_element(By.XPATH, "./ancestor::tr[contains(@class, 'k-master-row') or @role='row']")
                    date_input = None
                    td2_inputs = row_el.find_elements(By.XPATH, "./td[2]//input")
                    if td2_inputs:
                        date_input = td2_inputs[0]
                    if not date_input:
                        pickers = row_el.find_elements(By.CSS_SELECTOR, "div.kwe_dateTimePicker input")
                        if pickers:
                            date_input = pickers[0]
                    if not date_input:
                        holders = row_el.find_elements(By.CSS_SELECTOR, "input[placeholder*='dd/MM/yyyy']")
                        if holders:
                            date_input = holders[0]

                    raw_val = ""
                    if date_input:
                        raw_val = (date_input.get_attribute("value") or driver.execute_script("return arguments[0].value;", date_input) or "").strip()

                    if raw_val:
                        found_pup_date_strings.append(raw_val)
                        parts = raw_val.split()
                        if parts:
                            d_m_y = parts[0].split("/")
                            if len(d_m_y) == 3:
                                p_day = int(d_m_y[0])
                                p_month = int(d_m_y[1])
                                unique_parsed_dates[(p_month, p_day)] = f"{p_month:02d}-{p_day:02d}"
            except Exception:
                continue
    except Exception as e:
        if log_fn:
            log_fn(f"[ERROR] Milestone lookup failed: {e}")

    recognized_date_str = ""
    parsed_month = None
    parsed_day = None
    status = "ok"

    if len(unique_parsed_dates) == 0:
        status = "omitted"
        if log_fn:
            log_fn("[WARN] PUP DATE omitted")
    elif len(unique_parsed_dates) > 1:
        status = "conflicting"
        recognized_date_str = ", ".join(list(unique_parsed_dates.values()))
        if log_fn:
            log_fn(f"[WARN] Conflicting PUP DATE ({recognized_date_str})")
    else:
        (parsed_month, parsed_day), recognized_date_str = list(unique_parsed_dates.items())[0]
        if log_fn:
            log_fn(f"[INFO] PUP DATE {recognized_date_str}")

    return recognized_date_str, parsed_month, parsed_day, status


def _open_extract_hawbs_from_data(data, mawb_no):
    hawb_list = []

    def _search(obj):
        if isinstance(obj, dict):
            tx_name = str(obj.get("transactionName", "")).strip()
            tx_type = str(obj.get("transactionType", "")).strip()
            tx_number = str(obj.get("transactionNumber", "")).strip()

            if ("Customer Waybill" in tx_name or "House" in tx_type) and tx_number:
                if tx_number != mawb_no and tx_number not in hawb_list:
                    hawb_list.append(tx_number)

            for k in ["hwbNo", "hawbNo", "houseWaybillNo"]:
                val = obj.get(k)
                if isinstance(val, str) and val.strip() and val.strip() != mawb_no:
                    if val.strip() not in hawb_list:
                        hawb_list.append(val.strip())

            for v in obj.values():
                _search(v)
        elif isinstance(obj, list):
            for elem in obj:
                _search(elem)

    _search(data)
    return hawb_list


def _open_check_apple_in_hawb_data(data):
    if not data:
        return False, ""
    found_cnee = ""
    is_apple = False

    def _search(obj):
        nonlocal found_cnee, is_apple
        if isinstance(obj, dict):
            for k, v in obj.items():
                if ("cnee" in str(k).lower() or "consignee" in str(k).lower()) and isinstance(v, str) and v.strip():
                    found_cnee = v.strip()
                    if "APPLE" in found_cnee.upper():
                        is_apple = True
                        return
            for v in obj.values():
                _search(v)
                if is_apple:
                    return
        elif isinstance(obj, list):
            for elem in obj:
                _search(elem)
                if is_apple:
                    return

    _search(data)
    return is_apple, found_cnee


def _open_find_matching_excel_sheet(excel_path, pup_month, pup_day, log_fn=None):
    import openpyxl
    import gc
    if not os.path.exists(excel_path):
        if log_fn:
            log_fn(f"[ERROR] Excel file not found: {excel_path}")
        return None

    sheet_names = []
    try:
        wb = openpyxl.load_workbook(excel_path, read_only=True)
        sheet_names = wb.sheetnames
        wb.close()
    except Exception:
        excel = None
        wb = None
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            excel = win32com.client.DispatchEx("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            wb = excel.Workbooks.Open(excel_path, ReadOnly=True)
            sheet_names = [s.Name for s in wb.Worksheets]
        except Exception as e:
            if log_fn:
                log_fn(f"[ERROR] Failed to read Excel sheet list: {e}")
            return None
        finally:
            if wb:
                try: wb.Close(SaveChanges=False)
                except Exception: pass
                del wb
            if excel:
                try: excel.Quit()
                except Exception: pass
                del excel
            gc.collect()
            try: pythoncom.CoUninitialize()
            except Exception: pass

    target = (int(pup_month), int(pup_day))
    pattern = re.compile(r"(\d{1,2})\.(\d{1,2})\s*~\s*(\d{1,2})\.(\d{1,2})")

    matches = []
    for name in reversed(sheet_names):
        m = pattern.search(name.strip())
        if m:
            sm, sd, em, ed = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            start_t = (sm, sd)
            end_t = (em, ed)
            if start_t <= end_t:
                if start_t <= target <= end_t:
                    matches.append(name)
            else:
                if target >= start_t or target <= end_t:
                    matches.append(name)
    # Several sheets may overlap the PUP date; sheet_names is scanned newest-first, so matches[0] is the latest one.
    return matches[0] if matches else None


def _open_calculate_excel_rates(excel_path, sheet_name, chargeable_wt, origin="PVG", log_fn=None):
    import openpyxl
    import gc
    from decimal import Decimal, ROUND_HALF_UP, ROUND_UP, ROUND_DOWN, ROUND_CEILING, ROUND_FLOOR
    from openpyxl.utils import column_index_from_string, get_column_letter

    abs_path = os.path.abspath(excel_path)
    if not os.path.exists(abs_path):
        if log_fn:
            log_fn(f"[ERROR] Excel file does not exist: {abs_path}")
        return None

    try:
        wt_val = float(str(chargeable_wt).replace(",", "").strip())
    except Exception as e:
        if log_fn:
            log_fn(f"[ERROR] Chargeable Wt conversion failed ({chargeable_wt}): {e}")
        return None

    origin_upper = str(origin).upper().strip()
    row = 16 if origin_upper == "HAN" else 24
    input_cell = f"B{row}"

    bad = {}    # cell -> reason (empty / Excel error / non-numeric). Never silently treated as a real 0.
    stale = {}  # fallback evaluator only: cells that could not be calculated

    def _to_float(v, cell=""):
        reason = None
        out = 0.0
        if v is None or (isinstance(v, str) and not v.strip()):
            reason = "empty"
        elif isinstance(v, bool):
            out = float(v)
        elif isinstance(v, int) and -2146826288 <= v <= -2146826246:
            reason = f"Excel error code {v}"
        elif isinstance(v, (int, float)):
            out = float(v)
        else:
            s = str(v).strip()
            if s.startswith("#"):
                reason = f"Excel error {s}"
            else:
                try:
                    out = float(re.sub(r"[,$\s]", "", s))
                except Exception:
                    reason = f"non-numeric {s!r}"
        if reason:
            bad[cell or "?"] = reason
            return 0.0
        return out

    excel = None
    wb = None
    co_init = False
    try:
        import win32com.client
        import pythoncom
        try:
            pythoncom.CoInitialize()
            co_init = True
        except Exception:
            pass

        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        try:
            excel.AskToUpdateLinks = False
            excel.EnableEvents = False
        except Exception:
            pass

        wb = excel.Workbooks.Open(abs_path, UpdateLinks=0, ReadOnly=True, IgnoreReadOnlyRecommended=True)
        target_ws = None
        for s in wb.Worksheets:
            if s.Name == sheet_name:
                target_ws = s
                break
        if not target_ws:
            if log_fn:
                log_fn(f"[ERROR] Sheet '{sheet_name}' not found in Excel")
            return None

        try:
            excel.Calculation = -4105  # xlCalculationAutomatic
        except Exception:
            pass
        try:
            target_ws.EnableCalculation = True
        except Exception:
            pass

        target_ws.Range(input_cell).MergeArea.Cells(1, 1).Value2 = wt_val
        try:
            target_ws.Calculate()
        except Exception:
            pass
        try:
            excel.CalculateFull()
        except Exception:
            excel.Calculate()

        wait_start = time.time()
        try:
            while getattr(excel, "CalculationState", 0) != 0 and (time.time() - wait_start) < 5.0:
                time.sleep(0.1)
        except Exception:
            pass

        # Formula cells never come back as None once calculated; poll the same row until D/E/F/H are filled.
        def _row_ready():
            try:
                return all(target_ws.Range(f"{c}{row}").Value2 is not None for c in ("D", "E", "F", "H"))
            except Exception:
                return True
        _t0 = time.time()
        while not _row_ready() and (time.time() - _t0) < 5.0:
            try:
                target_ws.Calculate()
            except Exception:
                pass
            time.sleep(0.2)

        try:
            _echo = target_ws.Range(input_cell).MergeArea.Cells(1, 1).Value2
            if _echo is None or abs(float(_echo) - wt_val) > 1e-9:
                if log_fn:
                    log_fn(f"[WARN] Excel {input_cell} read-back mismatch (set {wt_val}, got {_echo})")
        except Exception:
            pass

        d_val = _to_float(target_ws.Range(f"D{row}").Value2, f"D{row}")
        e_val = _to_float(target_ws.Range(f"E{row}").Value2, f"E{row}")
        f_val = _to_float(target_ws.Range(f"F{row}").Value2, f"F{row}")
        h_val = _to_float(target_ws.Range(f"H{row}").Value2, f"H{row}")

        if origin_upper == "HAN":
            fcf_as_val = _to_float(target_ws.Range(f"I{row}").Value2, f"I{row}")
            fcf_ad_val = _to_float(target_ws.Range(f"J{row}").Value2, f"J{row}")
            mag1_val, mag2_val = 0.0, 0.0
            fcf_as_cell, fcf_ad_cell = f"I{row}", f"J{row}"
        else:
            mag1_val = _to_float(target_ws.Range(f"I{row}").Value2, f"I{row}")
            mag2_val = _to_float(target_ws.Range(f"J{row}").Value2, f"J{row}")
            fcf_as_val = _to_float(target_ws.Range(f"K{row}").Value2, f"K{row}")
            fcf_ad_val = _to_float(target_ws.Range(f"L{row}").Value2, f"L{row}")
            fcf_as_cell, fcf_ad_cell = f"K{row}", f"L{row}"

        return {
            "SOURCE": "COM", "BAD": bad, "STALE": {}, "origin": origin_upper, "row": row,
            "FRT_AS": d_val, "FRT_AD": e_val, "FSC": f_val, "HAN": h_val,
            "MAG1": mag1_val, "MAG2": mag2_val, "FCF_AS": fcf_as_val, "FCF_AD": fcf_ad_val,
            "FRT_AS_CELL": f"D{row}", "FRT_AD_CELL": f"E{row}",
            "FSC_CELL": f"F{row}", "HAN_CELL": f"H{row}",
            "FCF_AS_CELL": fcf_as_cell, "FCF_AD_CELL": fcf_ad_cell,
        }
    except ImportError:
        if log_fn:
            log_fn("[WARN] pywin32 not available - using openpyxl evaluator (formulas are evaluated in Python)")
    except Exception as com_err:
        if log_fn:
            log_fn(f"[WARN] Excel COM calculation failed, using openpyxl fallback: {com_err}")
    finally:
        if wb:
            try:
                wb.Saved = True
            except Exception:
                pass
            try:
                wb.Close(SaveChanges=False)
            except Exception:
                pass
            del wb
        if excel:
            try:
                excel.Quit()
            except Exception:
                pass
            del excel
        gc.collect()
        if co_init:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    wb = None
    wb_cached = None
    try:
        wb = openpyxl.load_workbook(abs_path, data_only=False)
        if sheet_name not in wb.sheetnames:
            if log_fn:
                log_fn(f"[ERROR] Sheet '{sheet_name}' not found in Excel")
            return None
        try:
            wb_cached = openpyxl.load_workbook(abs_path, data_only=True)
        except Exception:
            wb_cached = None

        overrides = {(sheet_name, row, 2): wt_val}
        memo = {}
        cell_re = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")

        def _parse_coord(ref_str):
            m = cell_re.match(ref_str.strip())
            if not m:
                raise ValueError(f"Invalid cell ref: {ref_str}")
            return int(m.group(2)), column_index_from_string(m.group(1).upper())

        def _num(v):
            if v is None:
                return 0.0
            if isinstance(v, bool):
                return 1.0 if v else 0.0
            if isinstance(v, (int, float)):
                return float(v)
            s = re.sub(r"[,$\s]", "", str(v).strip())
            return float(s) if s else 0.0

        def _is_num(v):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return True
            if isinstance(v, str):
                try:
                    float(re.sub(r"[,$\s]", "", v.strip()))
                    return True
                except Exception:
                    return False
            return False

        def _excel_round(val, digits, mode):
            d = Decimal(str(float(val)))
            n = int(digits)
            if n >= 0:
                q = Decimal("1") if n == 0 else Decimal("1e-" + str(n))
            else:
                q = Decimal("1e" + str(-n))
            return float(d.quantize(q, rounding=mode))

        def _excel_eq(a, b):
            if a is None and b is None:
                return True
            if a is None:
                return b == "" or (_is_num(b) and _num(b) == 0.0)
            if b is None:
                return a == "" or (_is_num(a) and _num(a) == 0.0)
            if isinstance(a, bool) or isinstance(b, bool):
                return bool(a) == bool(b)
            if _is_num(a) and _is_num(b):
                return abs(_num(a) - _num(b)) < 1e-9
            return str(a).strip().lower() == str(b).strip().lower()

        def _excel_cmp(a, b):
            if _excel_eq(a, b):
                return 0
            if (a is None or _is_num(a)) and (b is None or _is_num(b)):
                return -1 if _num(a) < _num(b) else 1
            sa = "" if a is None else str(a).lower()
            sb = "" if b is None else str(b).lower()
            return -1 if sa < sb else 1

        def _match_val(cand, key):
            if isinstance(key, str) and ("*" in key or "?" in key):
                pat = "^" + re.escape(key).replace(r"\*", ".*").replace(r"\?", ".") + "$"
                return bool(re.match(pat, "" if cand is None else str(cand), re.IGNORECASE))
            return _excel_eq(cand, key)

        def _tokenize(formula):
            tokens = []
            i = 0
            n = len(formula)
            while i < n:
                ch = formula[i]
                if ch.isspace():
                    i += 1
                    continue
                if ch == '"':
                    j = i + 1
                    buf = []
                    while j < n:
                        if formula[j] == '"':
                            if j + 1 < n and formula[j + 1] == '"':
                                buf.append('"')
                                j += 2
                            else:
                                j += 1
                                break
                        else:
                            buf.append(formula[j])
                            j += 1
                    tokens.append(("STR", "".join(buf)))
                    i = j
                    continue
                if ch == "'":
                    j = i + 1
                    sbuf = []
                    while j < n:
                        if formula[j] == "'":
                            if j + 1 < n and formula[j + 1] == "'":
                                sbuf.append("'")
                                j += 2
                            else:
                                j += 1
                                break
                        else:
                            sbuf.append(formula[j])
                            j += 1
                    if j < n and formula[j] == "!":
                        j += 1
                        k = j
                        while k < n and (formula[k].isalnum() or formula[k] == "$"):
                            k += 1
                        tokens.append(("CELL", ("".join(sbuf), formula[j:k])))
                        i = k
                        continue
                    raise ValueError(f"Unexpected quoted token in {formula}")
                if ch.isdigit() or (ch == "." and i + 1 < n and formula[i + 1].isdigit()):
                    j = i
                    while j < n and (formula[j].isdigit() or formula[j] == "."):
                        j += 1
                    if j < n and formula[j] in ("e", "E"):
                        k = j + 1
                        if k < n and formula[k] in ("+", "-"):
                            k += 1
                        if k < n and formula[k].isdigit():
                            while k < n and formula[k].isdigit():
                                k += 1
                            j = k
                    tokens.append(("NUM", float(formula[i:j])))
                    i = j
                    continue
                if formula[i:i + 2] in ("<=", ">=", "<>"):
                    tokens.append(("OP", formula[i:i + 2]))
                    i += 2
                    continue
                if ch in "+-*/^&=<>%(),:":
                    tokens.append((ch if ch in "(),:" else "OP", ch))
                    i += 1
                    continue
                if ch.isalpha() or ch in ("$", "_"):
                    j = i
                    while j < n and (formula[j].isalnum() or formula[j] in ("$", "_", ".")):
                        j += 1
                    ident = formula[i:j]
                    if j < n and formula[j] == "!":
                        j += 1
                        k = j
                        while k < n and (formula[k].isalnum() or formula[k] == "$"):
                            k += 1
                        tokens.append(("CELL", (ident, formula[j:k])))
                        i = k
                        continue
                    p = j
                    while p < n and formula[p].isspace():
                        p += 1
                    if p < n and formula[p] == "(":
                        _fn = ident.upper()
                        for _pre in ("_XLFN.", "_XLWS."):
                            if _fn.startswith(_pre):
                                _fn = _fn[len(_pre):]
                        tokens.append(("FUNC", _fn))
                        i = j
                        continue
                    up = ident.upper()
                    if up == "TRUE":
                        tokens.append(("BOOL", True))
                    elif up == "FALSE":
                        tokens.append(("BOOL", False))
                    elif cell_re.match(ident):
                        tokens.append(("CELL", (None, ident)))
                    else:
                        tokens.append(("IDENT", ident))
                    i = j
                    continue
                raise ValueError(f"Unexpected char '{ch}' in {formula}")
            return tokens

        def _eval_formula(formula, cur_sheet, visiting):
            tokens = _tokenize(formula[1:])
            pos = [0]
            let_vars = {}  # LET(name, value, ..., result) local names (case-insensitive)

            def peek(offset=0):
                idx = pos[0] + offset
                return tokens[idx] if idx < len(tokens) else (None, None)

            def consume():
                tok = tokens[pos[0]]
                pos[0] += 1
                return tok

            def parse_expr():
                return parse_cmp()

            def parse_cmp():
                left = parse_concat()
                while peek()[0] == "OP" and peek()[1] in ("=", "<>", "<", ">", "<=", ">="):
                    op = consume()[1]
                    right = parse_concat()
                    if op == "=":
                        left = _excel_eq(left, right)
                    elif op == "<>":
                        left = not _excel_eq(left, right)
                    elif op == "<":
                        left = _excel_cmp(left, right) < 0
                    elif op == ">":
                        left = _excel_cmp(left, right) > 0
                    elif op == "<=":
                        left = _excel_cmp(left, right) <= 0
                    elif op == ">=":
                        left = _excel_cmp(left, right) >= 0
                return left

            def parse_concat():
                left = parse_add()
                while peek()[0] == "OP" and peek()[1] == "&":
                    consume()
                    right = parse_add()
                    ls = "" if left is None else str(left)
                    rs = "" if right is None else str(right)
                    left = ls + rs
                return left

            def parse_add():
                left = parse_mul()
                while peek()[0] == "OP" and peek()[1] in ("+", "-"):
                    op = consume()[1]
                    right = parse_mul()
                    left = (_num(left) + _num(right)) if op == "+" else (_num(left) - _num(right))
                return left

            def parse_mul():
                left = parse_pow()
                while peek()[0] == "OP" and peek()[1] in ("*", "/"):
                    op = consume()[1]
                    right = parse_pow()
                    if op == "*":
                        left = _num(left) * _num(right)
                    else:
                        d = _num(right)
                        if d == 0.0:
                            raise ZeroDivisionError("#DIV/0!")
                        left = _num(left) / d
                return left

            def parse_pow():
                left = parse_unary()
                while peek()[0] == "OP" and peek()[1] == "^":
                    consume()
                    right = parse_unary()
                    left = _num(left) ** _num(right)
                return left

            def parse_unary():
                if peek()[0] == "OP" and peek()[1] in ("+", "-"):
                    op = consume()[1]
                    val = parse_unary()
                    return _num(val) if op == "+" else -_num(val)
                return parse_postfix()

            def parse_postfix():
                val = parse_primary()
                while peek()[0] == "OP" and peek()[1] == "%":
                    consume()
                    val = _num(val) / 100.0
                return val

            def _skip_arg():
                depth = 0
                while pos[0] < len(tokens):
                    t, _ = peek()
                    if t == "(":
                        depth += 1
                    elif t == ")":
                        if depth == 0:
                            break
                        depth -= 1
                    elif t == "," and depth == 0:
                        break
                    consume()

            def _parse_range_or_expr():
                if peek()[0] == "CELL" and peek(1)[0] == ":" and peek(2)[0] == "CELL":
                    _, (s1, ref1) = consume()
                    consume()
                    _, (s2, ref2) = consume()
                    ts = s1 or s2 or cur_sheet
                    r1, c1 = _parse_coord(ref1)
                    r2, c2 = _parse_coord(ref2)
                    if r1 > r2:
                        r1, r2 = r2, r1
                    if c1 > c2:
                        c1, c2 = c2, c1
                    grid = []
                    for rr in range(r1, r2 + 1):
                        grid.append([_get_cell_value(ts, rr, cc, visiting) for cc in range(c1, c2 + 1)])
                    return ("__GRID__", grid)
                return parse_expr()

            def _flatten_nums(args):
                out = []
                for a in args:
                    if isinstance(a, tuple) and len(a) == 2 and a[0] == "__GRID__":
                        for r_arr in a[1]:
                            for v in r_arr:
                                if isinstance(v, (int, float)) and not isinstance(v, bool):
                                    out.append(float(v))
                    else:
                        if a is not None and _is_num(a):
                            out.append(_num(a))
                return out

            def parse_primary():
                t, v = peek()
                if t in ("NUM", "STR", "BOOL"):
                    consume()
                    return v
                if t == "(":
                    consume()
                    val = parse_expr()
                    if peek()[0] == ")":
                        consume()
                    return val
                if t == "IDENT":
                    if v.lower() in let_vars:
                        consume()
                        return let_vars[v.lower()]
                if t == "CELL" and v[0] is None and v[1].lower() in let_vars:
                    consume()
                    return let_vars[v[1].lower()]
                if t == "CELL":
                    _, (s_name, ref) = consume()
                    ts = s_name or cur_sheet
                    rr, cc = _parse_coord(ref)
                    return _get_cell_value(ts, rr, cc, visiting)
                if t == "FUNC":
                    fname = consume()[1]
                    consume()  # '('
                    if fname == "LET":
                        _saved = dict(let_vars)
                        try:
                            while True:
                                _nt, _nv = peek()
                                _is_name = (_nt == "IDENT" or (_nt == "CELL" and _nv[0] is None)) and peek(1)[0] == ","
                                if _is_name:
                                    _name = (_nv if _nt == "IDENT" else _nv[1]).lower()
                                    consume()
                                    consume()  # ','
                                    let_vars[_name] = parse_expr()
                                    if peek()[0] == ",":
                                        consume()
                                else:
                                    res = parse_expr()
                                    break
                            if peek()[0] == ")":
                                consume()
                        finally:
                            let_vars.clear()
                            let_vars.update(_saved)
                        return res
                    if fname == "IF":
                        cond = parse_expr()
                        if peek()[0] == ",":
                            consume()
                        if bool(cond) if isinstance(cond, bool) else (_num(cond) != 0.0):
                            res = parse_expr()
                            if peek()[0] == ",":
                                consume()
                                _skip_arg()
                        else:
                            _skip_arg()
                            if peek()[0] == ",":
                                consume()
                                res = parse_expr()
                            else:
                                res = False
                        if peek()[0] == ")":
                            consume()
                        return res
                    if fname == "IFS":
                        res = None
                        matched = False
                        while peek()[0] not in (")", None):
                            if not matched:
                                cond = parse_expr()
                                if peek()[0] == ",":
                                    consume()
                                if bool(cond) if isinstance(cond, bool) else (_num(cond) != 0.0):
                                    res = parse_expr()
                                    matched = True
                                else:
                                    _skip_arg()
                            else:
                                _skip_arg()
                                if peek()[0] == ",":
                                    consume()
                                    _skip_arg()
                            if peek()[0] == ",":
                                consume()
                        if peek()[0] == ")":
                            consume()
                        return res
                    if fname in ("IFERROR", "IFNA"):
                        try:
                            res = parse_expr()
                            if isinstance(res, str) and res.startswith("#"):
                                raise ValueError(res)
                            if peek()[0] == ",":
                                consume()
                                _skip_arg()
                        except Exception as _ie:
                            _m = str(_ie)
                            if "Unsupported" in _m or "Unexpected" in _m or "Invalid cell ref" in _m:
                                raise
                            _skip_arg()
                            if peek()[0] == ",":
                                consume()
                                res = parse_expr()
                            else:
                                res = 0.0
                        if peek()[0] == ")":
                            consume()
                        return res

                    args = []
                    while peek()[0] not in (")", None):
                        if peek()[0] == ",":
                            args.append(None)
                            consume()
                            continue
                        args.append(_parse_range_or_expr())
                        if peek()[0] == ",":
                            consume()
                    if peek()[0] == ")":
                        consume()

                    if fname == "SUM":
                        return sum(_flatten_nums(args))
                    if fname == "AVERAGE":
                        nums = _flatten_nums(args)
                        return (sum(nums) / len(nums)) if nums else 0.0
                    if fname == "MIN":
                        nums = _flatten_nums(args)
                        return min(nums) if nums else 0.0
                    if fname == "MAX":
                        nums = _flatten_nums(args)
                        return max(nums) if nums else 0.0
                    if fname == "COUNT":
                        return float(len(_flatten_nums(args)))
                    if fname == "COUNTA":
                        cnt = 0
                        for a in args:
                            if isinstance(a, tuple) and len(a) == 2 and a[0] == "__GRID__":
                                cnt += sum(1 for r_arr in a[1] for x in r_arr if x is not None and x != "")
                            elif a is not None and a != "":
                                cnt += 1
                        return float(cnt)
                    if fname == "ROUND":
                        return _excel_round(_num(args[0]), _num(args[1]) if len(args) > 1 else 0, ROUND_HALF_UP)
                    if fname == "ROUNDUP":
                        return _excel_round(_num(args[0]), _num(args[1]) if len(args) > 1 else 0, ROUND_UP)
                    if fname == "ROUNDDOWN":
                        return _excel_round(_num(args[0]), _num(args[1]) if len(args) > 1 else 0, ROUND_DOWN)
                    if fname == "CEILING":
                        sig = _num(args[1]) if len(args) > 1 else 1.0
                        if sig == 0.0:
                            return 0.0
                        return float((Decimal(str(_num(args[0]))) / Decimal(str(sig))).to_integral_value(rounding=ROUND_CEILING) * Decimal(str(sig)))
                    if fname == "FLOOR":
                        sig = _num(args[1]) if len(args) > 1 else 1.0
                        if sig == 0.0:
                            return 0.0
                        return float((Decimal(str(_num(args[0]))) / Decimal(str(sig))).to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(sig)))
                    if fname == "INT":
                        import math
                        return float(math.floor(_num(args[0])))
                    if fname == "TRUNC":
                        return _excel_round(_num(args[0]), _num(args[1]) if len(args) > 1 else 0, ROUND_DOWN)
                    if fname == "ABS":
                        return abs(_num(args[0]))
                    if fname == "MOD":
                        return _num(args[0]) % _num(args[1])
                    if fname == "POWER":
                        return _num(args[0]) ** _num(args[1])
                    if fname == "SQRT":
                        return _num(args[0]) ** 0.5
                    if fname == "AND":
                        return all(bool(a) if isinstance(a, bool) else (_num(a) != 0.0) for a in args)
                    if fname == "OR":
                        return any(bool(a) if isinstance(a, bool) else (_num(a) != 0.0) for a in args)
                    if fname == "NOT":
                        a = args[0]
                        return not (bool(a) if isinstance(a, bool) else (_num(a) != 0.0))
                    if fname == "ISBLANK":
                        return args[0] is None or args[0] == ""
                    if fname == "ISNUMBER":
                        return _is_num(args[0])
                    if fname == "ISERROR":
                        return isinstance(args[0], str) and args[0].startswith("#")
                    if fname == "TRUE":
                        return True
                    if fname == "FALSE":
                        return False
                    if fname == "CHOOSE":
                        idx = int(_num(args[0]))
                        return args[idx] if 1 <= idx < len(args) else 0.0
                    if fname == "SUMIF":
                        r_grid = args[0][1]
                        crit = args[1]
                        s_grid = args[2][1] if len(args) > 2 and isinstance(args[2], tuple) else r_grid
                        op_m = re.match(r"^(<=|>=|<>|<|>|=)(.*)$", str(crit).strip()) if isinstance(crit, str) else None
                        total = 0.0
                        for ri, r_arr in enumerate(r_grid):
                            for ci, cv in enumerate(r_arr):
                                matched = False
                                if op_m:
                                    op_s, rhs_s = op_m.group(1), op_m.group(2)
                                    rhs = float(rhs_s) if _is_num(rhs_s) else rhs_s
                                    if op_s == "=": matched = _excel_eq(cv, rhs)
                                    elif op_s == "<>": matched = not _excel_eq(cv, rhs)
                                    elif op_s == "<": matched = _excel_cmp(cv, rhs) < 0
                                    elif op_s == ">": matched = _excel_cmp(cv, rhs) > 0
                                    elif op_s == "<=": matched = _excel_cmp(cv, rhs) <= 0
                                    elif op_s == ">=": matched = _excel_cmp(cv, rhs) >= 0
                                else:
                                    matched = _match_val(cv, crit)
                                if matched and ri < len(s_grid) and ci < len(s_grid[ri]):
                                    sv = s_grid[ri][ci]
                                    if _is_num(sv):
                                        total += _num(sv)
                        return total
                    if fname == "VLOOKUP":
                        key = args[0]
                        grid = args[1][1]
                        col_idx = int(_num(args[2])) - 1
                        approx = True if len(args) < 4 or args[3] is None else (bool(args[3]) if isinstance(args[3], bool) else (_num(args[3]) != 0.0))
                        if not approx:
                            for r_arr in grid:
                                if _match_val(r_arr[0], key):
                                    return r_arr[col_idx]
                            raise ValueError("#N/A")
                        last_val = None
                        for r_arr in grid:
                            if r_arr[0] is not None and _excel_cmp(r_arr[0], key) <= 0:
                                last_val = r_arr[col_idx]
                            elif r_arr[0] is not None and _excel_cmp(r_arr[0], key) > 0:
                                break
                        if last_val is None:
                            raise ValueError("#N/A")
                        return last_val
                    if fname == "HLOOKUP":
                        key = args[0]
                        grid = args[1][1]
                        row_idx = int(_num(args[2])) - 1
                        approx = True if len(args) < 4 or args[3] is None else (bool(args[3]) if isinstance(args[3], bool) else (_num(args[3]) != 0.0))
                        if not approx:
                            for ci, cv in enumerate(grid[0]):
                                if _match_val(cv, key):
                                    return grid[row_idx][ci]
                            raise ValueError("#N/A")
                        last_val = None
                        for ci, cv in enumerate(grid[0]):
                            if cv is not None and _excel_cmp(cv, key) <= 0:
                                last_val = grid[row_idx][ci]
                            elif cv is not None and _excel_cmp(cv, key) > 0:
                                break
                        if last_val is None:
                            raise ValueError("#N/A")
                        return last_val
                    if fname == "MATCH":
                        key = args[0]
                        grid = args[1][1]
                        flat = [x for r_arr in grid for x in r_arr]
                        mtype = 1 if len(args) < 3 or args[2] is None else int(_num(args[2]))
                        if mtype == 0:
                            for idx, cv in enumerate(flat, start=1):
                                if _match_val(cv, key):
                                    return float(idx)
                            raise ValueError("#N/A")
                        if mtype >= 1:
                            last_idx = None
                            for idx, cv in enumerate(flat, start=1):
                                if cv is not None and _excel_cmp(cv, key) <= 0:
                                    last_idx = idx
                            if last_idx is None:
                                raise ValueError("#N/A")
                            return float(last_idx)
                        last_idx = None
                        for idx, cv in enumerate(flat, start=1):
                            if cv is not None and _excel_cmp(cv, key) >= 0:
                                last_idx = idx
                        if last_idx is None:
                            raise ValueError("#N/A")
                        return float(last_idx)
                    if fname == "INDEX":
                        grid = args[0][1]
                        r_num = int(_num(args[1])) if len(args) > 1 and args[1] is not None else 1
                        c_num = int(_num(args[2])) if len(args) > 2 and args[2] is not None else None
                        if c_num is None:
                            if len(grid) == 1:
                                return grid[0][r_num - 1]
                            if len(grid[0]) == 1:
                                return grid[r_num - 1][0]
                            return grid[r_num - 1][0]
                        return grid[r_num - 1][c_num - 1]
                    raise ValueError(f"Unsupported Excel function: {fname}")
                raise ValueError(f"Unexpected token ({t}, {v}) in {formula}")

            return parse_expr()

        def _get_cell_value(s_name, r_idx, c_idx, visiting=None):
            if visiting is None:
                visiting = set()
            key = (s_name, r_idx, c_idx)
            if key in overrides:
                return overrides[key]
            if key in memo:
                return memo[key]
            if key in visiting:
                return 0.0
            if s_name not in wb.sheetnames:
                return 0.0
            raw = wb[s_name].cell(row=r_idx, column=c_idx).value
            if isinstance(raw, str) and raw.startswith("="):
                visiting.add(key)
                try:
                    val = _eval_formula(raw, s_name, visiting)
                except Exception as _fe:
                    val = wb_cached[s_name].cell(row=r_idx, column=c_idx).value if wb_cached and s_name in wb_cached.sheetnames else None
                    stale[f"{s_name}!{get_column_letter(c_idx)}{r_idx}"] = f"{type(_fe).__name__}: {_fe} (old cached value {val!r} is NOT recalculated)"
                finally:
                    visiting.discard(key)
                if val is None:
                    val = 0.0
                memo[key] = val
                return val
            memo[key] = raw
            return raw

        d_val = _to_float(_get_cell_value(sheet_name, row, 4), f"{get_column_letter(4)}{row}")
        e_val = _to_float(_get_cell_value(sheet_name, row, 5), f"{get_column_letter(5)}{row}")
        f_val = _to_float(_get_cell_value(sheet_name, row, 6), f"{get_column_letter(6)}{row}")
        h_val = _to_float(_get_cell_value(sheet_name, row, 8), f"{get_column_letter(8)}{row}")

        if origin_upper == "HAN":
            fcf_as_val = _to_float(_get_cell_value(sheet_name, row, 9), f"{get_column_letter(9)}{row}")
            fcf_ad_val = _to_float(_get_cell_value(sheet_name, row, 10), f"{get_column_letter(10)}{row}")
            mag1_val, mag2_val = 0.0, 0.0
            fcf_as_cell, fcf_ad_cell = f"I{row}", f"J{row}"
        else:
            mag1_val = _to_float(_get_cell_value(sheet_name, row, 9), f"{get_column_letter(9)}{row}")
            mag2_val = _to_float(_get_cell_value(sheet_name, row, 10), f"{get_column_letter(10)}{row}")
            fcf_as_val = _to_float(_get_cell_value(sheet_name, row, 11), f"{get_column_letter(11)}{row}")
            fcf_ad_val = _to_float(_get_cell_value(sheet_name, row, 12), f"{get_column_letter(12)}{row}")
            fcf_as_cell, fcf_ad_cell = f"K{row}", f"L{row}"

        return {
            "SOURCE": "openpyxl", "BAD": bad, "STALE": stale, "origin": origin_upper, "row": row,
            "FRT_AS": d_val, "FRT_AD": e_val, "FSC": f_val, "HAN": h_val,
            "MAG1": mag1_val, "MAG2": mag2_val, "FCF_AS": fcf_as_val, "FCF_AD": fcf_ad_val,
            "FRT_AS_CELL": f"D{row}", "FRT_AD_CELL": f"E{row}",
            "FSC_CELL": f"F{row}", "HAN_CELL": f"H{row}",
            "FCF_AS_CELL": fcf_as_cell, "FCF_AD_CELL": fcf_ad_cell,
        }
    except Exception as pyxl_err:
        if log_fn:
            log_fn(f"[ERROR] Excel parsing failed: {pyxl_err}")
        return None
    finally:
        if wb_cached:
            try:
                wb_cached.close()
            except Exception:
                pass
        if wb:
            try:
                wb.close()
            except Exception:
                pass


def _open_fmt_amt(val):
    try:
        return f"{float(str(val).replace(',', '').strip()):.2f}"
    except Exception:
        return "" if val is None else str(val).strip()


def _open_parse_hawb_charges_grid(driver):
    charge_rows = driver.find_elements(By.CSS_SELECTOR, "div.charges-grid table.k-grid-table tbody tr.k-master-row")
    parsed_charges = []

    for crow in charge_rows:
        try:
            code_el = crow.find_elements(By.CSS_SELECTOR, "select[name='chargeCode'] ~ span.k-input, td:nth-child(2) span.k-input")
            code_val = code_el[0].text.strip() if code_el else ""

            desc_el = crow.find_elements(By.CSS_SELECTOR, "input[name='chargeDescription']")
            desc_val = (desc_el[0].get_attribute("value") or "").strip() if desc_el else ""

            expimp_el = crow.find_elements(By.CSS_SELECTOR, "select[name='expImp'] ~ span.k-input, td:nth-child(5) span.k-input")
            expimp_val = expimp_el[0].text.strip() if expimp_el else ""

            terms_el = crow.find_elements(By.CSS_SELECTOR, "select[name='terms'] ~ span.k-input, td:nth-child(6) span.k-input")
            terms_val = terms_el[0].text.strip() if terms_el else ""

            wb_amt_el = crow.find_elements(By.CSS_SELECTOR, "input[name='waybillAmount']")
            wb_amt_val = (wb_amt_el[0].get_attribute("value") or "").strip() if wb_amt_el else ""

            wb_curr_el = crow.find_elements(By.CSS_SELECTOR, "select[name='waybillCurr'] ~ span.k-input, td:nth-child(8) span.k-input")
            wb_curr_val = wb_curr_el[0].text.strip() if wb_curr_el else ""

            inv_amt_el = crow.find_elements(By.CSS_SELECTOR, "input[name='invoiceAmount']")
            inv_amt_val = (inv_amt_el[0].get_attribute("value") or "").strip() if inv_amt_el else ""

            inv_curr_el = crow.find_elements(By.CSS_SELECTOR, "select[name='invoiceCurr'] ~ span.k-input, td:nth-child(10) span.k-input")
            inv_curr_val = inv_curr_el[0].text.strip() if inv_curr_el else ""

            act_cost_el = crow.find_elements(By.CSS_SELECTOR, "input[name='actualCost']")
            act_cost_val = (act_cost_el[0].get_attribute("value") or "").strip() if act_cost_el else ""

            cost_curr_el = crow.find_elements(By.CSS_SELECTOR, "select[name='costCurrency'] ~ span.k-input, td:nth-child(12) span.k-input")
            cost_curr_val = cost_curr_el[0].text.strip() if cost_curr_el else ""

            vendor_el = crow.find_elements(By.CSS_SELECTOR, "input[name='vendorId']")
            vendor_val = (vendor_el[0].get_attribute("value") or "").strip() if vendor_el else ""

            print_el = crow.find_elements(By.CSS_SELECTOR, "select[name='print'] ~ span.k-input, td:nth-child(16) span.k-input")
            print_val = print_el[0].text.strip() if print_el else ""

            parsed_charges.append({
                "code": code_val, "desc": desc_val, "exp_imp": expimp_val, "terms": terms_val,
                "wb_amt": wb_amt_val, "wb_curr": wb_curr_val, "inv_amt": inv_amt_val, "inv_curr": inv_curr_val,
                "act_cost": act_cost_val, "cost_curr": cost_curr_val, "vendor_id": vendor_val, "print": print_val,
                "row_element": crow
            })
        except Exception:
            continue
    return parsed_charges


def _open_breakbulk_search_mawb(ctx, mawb_no):
    """Target MAWB: clear any pre-filled ETA, type MAWB into Breakbulk MAWB input (no ETA / IC), and press SEARCH."""
    driver, wait = ctx.driver, ctx.wait

    for d_inp in driver.find_elements(By.CSS_SELECTOR, "input.k-textbox[placeholder='dd/MM/yyyy']"):
        try:
            if not d_inp.is_displayed():
                continue
            cur_val = (d_inp.get_attribute("value") or driver.execute_script("return arguments[0].value;", d_inp) or "").strip()
            if cur_val:
                try:
                    d_inp.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", d_inp)
                time.sleep(0.1)
                d_inp.send_keys(Keys.CONTROL, "a")
                d_inp.send_keys(Keys.BACKSPACE)
                driver.execute_script("""
                    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    if (setter) setter.call(arguments[0], '');
                    else arguments[0].value = '';
                    arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                    arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                """, d_inp)
                d_inp.send_keys(Keys.TAB)
                time.sleep(0.15)
        except Exception:
            continue

    box = None
    for _ in range(3):
        cands = [e for e in driver.find_elements(By.CSS_SELECTOR, "input.form-control.ted-input-text[maxlength='20']")
                 if e.is_displayed() and e.is_enabled()]
        if len(cands) > 1:
            scored = []
            for idx, e in enumerate(cands):
                try:
                    info = driver.execute_script("""
                        var el = arguments[0];
                        var prev = el.previousElementSibling;
                        if (prev && /mawb|master/i.test(prev.innerText || '')) {
                            return [0, (prev.innerText || '').trim().length];
                        }
                        var p = el.parentElement;
                        for (var d = 1; d <= 4 && p; d++) {
                            var txt = (p.innerText || '').trim();
                            if (/mawb|master/i.test(txt)) {
                                return [d, txt.length];
                            }
                            p = p.parentElement;
                        }
                        return [99, 99999];
                    """, e)
                    depth, tlen = info[0], info[1]
                    if depth < 99:
                        scored.append((depth, tlen, idx, e))
                except Exception:
                    pass
            if scored:
                scored.sort(key=lambda x: (x[0], x[1], x[2]))
                box = scored[0][3]
        elif cands:
            box = cands[0]
        if box is None and cands:
            box = cands[0]
        if box is not None:
            break
        time.sleep(1)
    if box is None:
        raise RuntimeError("MAWB input not found on Breakbulk page")

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", box)
    except Exception:
        pass
    try:
        box.click()
    except Exception:
        driver.execute_script("arguments[0].click();", box)
    time.sleep(0.2)
    box.send_keys(Keys.CONTROL, "a")
    box.send_keys(Keys.BACKSPACE)
    time.sleep(0.1)
    if (box.get_attribute("value") or "").strip():
        driver.execute_script("""
            var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            if (setter) setter.call(arguments[0], '');
            else arguments[0].value = '';
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """, box)
        time.sleep(0.1)
    for char in mawb_no:
        box.send_keys(char)
        time.sleep(0.05)
    box.send_keys(Keys.TAB)
    time.sleep(0.3)

    search_btn = None
    for xp in [
        "//button[contains(@class, 'kwe-btn-primary') and contains(translate(normalize-space(.), 'search', 'SEARCH'), 'SEARCH')]",
        "//button[contains(@class, 'kwe-btn-primary') and contains(translate(@value, 'search', 'SEARCH'), 'SEARCH')]",
    ]:
        btns = [b for b in driver.find_elements(By.XPATH, xp) if b.is_displayed() and b.is_enabled()]
        if btns:
            search_btn = btns[0]
            break
    if search_btn is None:
        search_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.kwe-btn-primary")))
    driver.execute_script("arguments[0].click();", search_btn)
    time.sleep(2.5)
    try:
        WebDriverWait(driver, 15).until_not(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image"))
        )
    except Exception:
        pass
    try:
        wait.until(EC.any_of(
            EC.presence_of_element_located((By.CSS_SELECTOR, "tr.k-master-row")),
            EC.presence_of_element_located((By.CSS_SELECTOR, "tr.k-grid-norecords, div.k-grid-norecords"))
        ))
    except Exception:
        pass


def _run_apple_open_profile(ctx: _OpenContext):
    """Run the APPLE open logic on top of the host OpenContext."""
    driver = ctx.driver
    wait = ctx.wait
    only_mode = bool(getattr(ctx, "only_mawbs", None))

    ctx.status("Entering Breakbulk page...")
    ctx.open_breakbulk()
    if ctx.stop_requested():
        return

    ctx.status("Searching Breakbulk ETA range...")
    ctx.log(("[INFO] Breakbulk search (Target MAWB mode)" if only_mode else f"[INFO] Breakbulk search (ETA: {ctx.start_date} ~ {ctx.end_date} | IC)"))

    if not only_mode:
        date_inputs = wait.until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input.k-textbox[placeholder='dd/MM/yyyy']"))
        )

        date_inputs[0].click()
        time.sleep(0.2)
        driver.execute_script("""
            arguments[0].value = '';
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """, date_inputs[0])
        time.sleep(0.2)
        for char in ("" if only_mode else ctx.start_date):
            date_inputs[0].send_keys(char)
            time.sleep(0.05)
        date_inputs[0].send_keys(Keys.TAB)
        time.sleep(0.3)

        date_inputs[1].click()
        time.sleep(0.2)
        driver.execute_script("""
            arguments[0].value = '';
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """, date_inputs[1])
        time.sleep(0.2)
        for char in ("" if only_mode else ctx.end_date):
            date_inputs[1].send_keys(char)
            time.sleep(0.05)
        date_inputs[1].send_keys(Keys.TAB)
        time.sleep(0.3)

        dropdown_wrap = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "span.k-dropdown-wrap")))
        driver.execute_script("arguments[0].click();", dropdown_wrap)
        time.sleep(0.4)
        active_el = driver.switch_to.active_element
        for char in "IC":
            active_el.send_keys(char)
            time.sleep(0.05)
        active_el.send_keys(Keys.TAB)
        time.sleep(0.4)

        search_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.kwe-btn-primary")))
        driver.execute_script("arguments[0].click();", search_btn)
        ctx.log("[INFO] Breakbulk search executed")

        time.sleep(3)
        try:
            WebDriverWait(driver, 15).until_not(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image"))
            )
        except Exception:
            pass
        try:
            wait.until(EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, "tr.k-master-row")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "tr.k-grid-norecords, div.k-grid-norecords"))
            ))
        except Exception:
            pass

    rows = driver.find_elements(By.CSS_SELECTOR, "tr.k-master-row")
    target_origin_mawbs = []
    mawb_origin_map = {}

    for row in rows:
        try:
            origin_port_el = row.find_element(By.CSS_SELECTOR, "td:nth-child(15)")
            origin_port = origin_port_el.text.strip().upper()
            if origin_port not in ["PVG", "HAN"]:
                continue

            arr_cb = row.find_element(By.CSS_SELECTOR, "input[name='acknowledge_alert']")
            doc_cb = row.find_element(By.CSS_SELECTOR, "input[name='docs_received']")
            arr_checked = driver.execute_script("return arguments[0].checked;", arr_cb)
            doc_checked = driver.execute_script("return arguments[0].checked;", doc_cb)

            if only_mode:
                _me = row.find_element(By.CSS_SELECTOR, "td:nth-child(6) span.case-cell")
                _mn = _me.text.strip()
                if _mn and ctx.is_target(_mn) and _mn not in target_origin_mawbs:
                    target_origin_mawbs.append(_mn)
                    mawb_origin_map[_mn] = origin_port
                continue

            if not arr_checked and not doc_checked:
                mawb_element = row.find_element(By.CSS_SELECTOR, "td:nth-child(6) span.case-cell")
                mawb_no = mawb_element.text.strip()
                if mawb_no and mawb_no not in target_origin_mawbs:
                    target_origin_mawbs.append(mawb_no)
                    mawb_origin_map[mawb_no] = origin_port
                    ctx.log(f"[INFO] Candidate MAWB ({origin_port}): {mawb_no}")
        except Exception:
            continue

    if only_mode:
        # Target MAWB: search each MAWB directly (no ETA / IC), read its origin from the grid
        for _t in ctx.only_mawbs:
            if ctx.stop_requested():
                return
            try:
                _open_breakbulk_search_mawb(ctx, _t)
            except Exception as _e:
                ctx.log(f"[ERROR] {_t}: Breakbulk search failed ({_e})")
                continue
            _org = ""
            for _r in driver.find_elements(By.CSS_SELECTOR, "tr.k-master-row"):
                try:
                    _m = _r.find_element(By.CSS_SELECTOR, "td:nth-child(6) span.case-cell").text.strip()
                    if ctx.is_target(_m) and _m.replace("-", "").upper() == _t.replace("-", "").upper():
                        _org = _r.find_element(By.CSS_SELECTOR, "td:nth-child(15)").text.strip().upper()
                        break
                except Exception:
                    continue
            if _org and _org not in ("PVG", "HAN"):
                ctx.log(f"[WARN] {_t}: origin {_org} not supported - skipped")
                continue
            if _t not in target_origin_mawbs:
                target_origin_mawbs.append(_t)
            if _org:
                mawb_origin_map[_t] = _org
    ctx.log(f"[INFO] Candidates (PVG/HAN): {len(target_origin_mawbs)}")

    final_apple_mawbs = []
    for index, mawb_no in enumerate(target_origin_mawbs, start=1):
        if ctx.stop_requested():
            return
        origin_for_mawb = mawb_origin_map.get(mawb_no, "PVG")
        ctx.status(f"Filtering APPLE HAWB ({index}/{len(target_origin_mawbs)}): {mawb_no}")
        main_search_result = ctx.search_waybill(mawb_no, wb_type="main")
        ctx.sleep(0.3)

        if not main_search_result:
            continue
        hawb_list = _open_extract_hawbs_from_data(main_search_result, mawb_no)
        if not hawb_list:
            continue

        mawb_has_apple = False
        matched_info = ""
        for hwb_no in hawb_list:
            if ctx.stop_requested():
                return
            hawb_result = ctx.search_waybill(hwb_no, wb_type="hawb")
            ctx.sleep(0.3)
            is_apple, cnee_name = _open_check_apple_in_hawb_data(hawb_result)
            if is_apple:
                mawb_has_apple = True
                matched_info = f"HAWB: {hwb_no} (Cnee: {cnee_name})"
                ctx.log(f"[INFO] APPLE match {mawb_no} ({origin_for_mawb}) -> {matched_info}")
                break

        if mawb_has_apple:
            final_apple_mawbs.append((mawb_no, origin_for_mawb, matched_info))
        elif only_mode:
            ctx.log(f"[WARN] {mawb_no}: no APPLE HAWB - skipped")

    detected_mawb_list = [item[0] for item in final_apple_mawbs]
    if only_mode:
        target_mawb_list = list(detected_mawb_list)
    else:
        target_mawb_list = [m for m in detected_mawb_list if m not in ctx.exclude_mawbs]
        for m in ctx.add_mawbs:
            if m not in target_mawb_list:
                target_mawb_list.append(m)

    ctx.log(f"[INFO] Final targets: {len(target_mawb_list)}")
    if not target_mawb_list:
        ctx.log("[INFO] No MAWB to process")
        return

    breakbulk_window = driver.current_window_handle
    total_mawbs = len(target_mawb_list)

    for m_idx, target_mawb_no in enumerate(target_mawb_list, start=1):
        if ctx.stop_requested():
            break

        ctx.active_context["mawb"] = target_mawb_no
        ctx.progress(m_idx - 1, total_mawbs, f"Processing MAWB ({m_idx}/{total_mawbs}): {target_mawb_no}")
        ctx.log(f"[INFO] [{m_idx}/{total_mawbs}] Start MAWB {target_mawb_no}")

        mawb_window = None
        target_mawb_origin = mawb_origin_map.get(target_mawb_no, "")

        try:
            driver.switch_to.window(breakbulk_window)
            time.sleep(1)
            if only_mode:
                _open_breakbulk_search_mawb(ctx, target_mawb_no)

            if not target_mawb_origin:
                for r in driver.find_elements(By.CSS_SELECTOR, "tr.k-master-row"):
                    try:
                        m_el = r.find_element(By.CSS_SELECTOR, "td:nth-child(6) span.case-cell")
                        if m_el.text.strip() == target_mawb_no:
                            target_mawb_origin = r.find_element(By.CSS_SELECTOR, "td:nth-child(15)").text.strip().upper()
                            break
                    except Exception:
                        continue
            if not target_mawb_origin or target_mawb_origin not in ["PVG", "HAN"]:
                target_mawb_origin = "PVG"

            ctx.active_context["mawb"] = target_mawb_no
            ctx.active_context["origin"] = target_mawb_origin
            ctx.active_context["hawb"] = ""
            ctx.active_context["pup_date"] = ""

            current_rows = driver.find_elements(By.CSS_SELECTOR, "tr.k-master-row")
            target_row = None
            for r in current_rows:
                try:
                    m_el = r.find_element(By.CSS_SELECTOR, "td:nth-child(6) span.case-cell")
                    if m_el.text.strip() == target_mawb_no:
                        target_row = r
                        break
                except Exception:
                    continue

            if not target_row:
                ctx.log(f"[ERROR] {target_mawb_no}: row not found on Breakbulk page (not returned by Breakbulk search)")
                ctx.add_irr({
                    "pup_date": "", "origin": target_mawb_origin, "mawb": target_mawb_no, "hawb": "",
                    "desc": "Breakbulk row not found", "corr": "",
                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                })
                continue

            arr_cb = target_row.find_element(By.CSS_SELECTOR, "input[name='acknowledge_alert']")
            doc_cb = target_row.find_element(By.CSS_SELECTOR, "input[name='docs_received']")
            arr_checked = driver.execute_script("return arguments[0].checked;", arr_cb)
            doc_checked = driver.execute_script("return arguments[0].checked;", doc_cb)

            if not arr_checked or not doc_checked:
                ctx.log("[INFO] [1/6] Arr/Doc check and Save")
                ctx.safe_check(arr_cb)
                time.sleep(0.2)
                ctx.safe_check(doc_cb)
                time.sleep(0.3)

                save_btn = wait.until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'kwe-btn-primary') and (contains(text(), 'Save') or @value='Save')]"))
                )
                driver.execute_script("arguments[0].click();", save_btn)
                time.sleep(2.5)

                try:
                    WebDriverWait(driver, 10).until_not(
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image"))
                    )
                except Exception:
                    pass

                obdorg_detected = False
                msg_contents = driver.find_elements(By.CSS_SELECTOR, "div.msg-content, div.dialog-body, div.k-window-content")
                for mel in msg_contents:
                    try:
                        if not mel.is_displayed():
                            continue
                        txt = mel.text.strip()
                        if not txt:
                            txt = driver.execute_script("return arguments[0].textContent;", mel).strip()
                        if "OBDORG milestone to proceed" in txt:
                            ctx.log(f"[WARN] '{txt}' -> closing dialog, retrying Arr uncheck")
                            ctx.click_modal_ok(mel)
                            obdorg_detected = True
                            time.sleep(1.5)
                            break
                    except Exception:
                        continue

                if not obdorg_detected:
                    unexpected_err = ctx.handle_unexpected_error_modal(ignore_keywords=["OBDORG milestone to proceed"])
                    if unexpected_err:
                        ctx.add_irr({
                            "pup_date": "", "origin": target_mawb_origin, "mawb": target_mawb_no, "hawb": "",
                            "desc": unexpected_err, "corr": "",
                            "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        continue

                if obdorg_detected:
                    try:
                        WebDriverWait(driver, 10).until_not(
                            EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image, .k-overlay, .modal-backdrop"))
                        )
                    except Exception:
                        pass
                    time.sleep(1)

                    current_rows = driver.find_elements(By.CSS_SELECTOR, "tr.k-master-row")
                    for r in current_rows:
                        try:
                            m_el = r.find_element(By.CSS_SELECTOR, "td:nth-child(6) span.case-cell")
                            if m_el.text.strip() == target_mawb_no:
                                target_row = r
                                break
                        except Exception:
                            continue

                    arr_cb = target_row.find_element(By.CSS_SELECTOR, "input[name='acknowledge_alert']")
                    doc_cb = target_row.find_element(By.CSS_SELECTOR, "input[name='docs_received']")
                    ctx.safe_uncheck(arr_cb)
                    time.sleep(0.2)
                    ctx.safe_check(doc_cb)
                    time.sleep(0.3)

                    save_btn = wait.until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'kwe-btn-primary') and (contains(text(), 'Save') or @value='Save')]"))
                    )
                    driver.execute_script("arguments[0].click();", save_btn)
                    time.sleep(3)

                    try:
                        WebDriverWait(driver, 15).until_not(
                            EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image"))
                        )
                    except Exception:
                        pass

                    unexpected_err = ctx.handle_unexpected_error_modal()
                    if unexpected_err:
                        ctx.add_irr({
                            "pup_date": "", "origin": target_mawb_origin, "mawb": target_mawb_no, "hawb": "",
                            "desc": unexpected_err, "corr": "",
                            "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        continue

                    try:
                        WebDriverWait(driver, 15).until_not(
                            EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image, .k-overlay, .modal-backdrop"))
                        )
                    except Exception:
                        pass
                    time.sleep(1.5)

                try:
                    WebDriverWait(driver, 10).until_not(
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image, .k-overlay, .modal-backdrop"))
                    )
                except Exception:
                    pass

            ctx.log(f"[INFO] [2/6] Shift+click {target_mawb_no}")
            current_rows = driver.find_elements(By.CSS_SELECTOR, "tr.k-master-row")
            mawb_link_element = None
            for r in current_rows:
                try:
                    m_el = r.find_element(By.CSS_SELECTOR, "td:nth-child(6) span.case-cell")
                    if m_el.text.strip() == target_mawb_no:
                        mawb_link_element = r.find_element(By.CSS_SELECTOR, "td a[href*='carrier-waybill']")
                        break
                except Exception:
                    continue

            if not mawb_link_element:
                mawb_link_element = driver.find_element(By.XPATH, f"//a[contains(@href, 'carrier-waybill') and .//span[text()='{target_mawb_no}']]")

            existing_handles = driver.window_handles
            try:
                ActionChains(driver).key_down(Keys.SHIFT).click(mawb_link_element).key_up(Keys.SHIFT).perform()
            except Exception:
                href = mawb_link_element.get_attribute("href")
                driver.execute_script("window.open(arguments[0], '_blank');", href)

            time.sleep(1)
            try:
                WebDriverWait(driver, 8).until(lambda d: len(d.window_handles) > len(existing_handles))
                mawb_window = [w for w in driver.window_handles if w not in existing_handles][0]
                driver.switch_to.window(mawb_window)
            except Exception:
                href = mawb_link_element.get_attribute("href")
                driver.execute_script("window.open(arguments[0], '_blank');", href)
                WebDriverWait(driver, 8).until(lambda d: len(d.window_handles) > len(existing_handles))
                mawb_window = [w for w in driver.window_handles if w not in existing_handles][0]
                driver.switch_to.window(mawb_window)

            ctx.log("[INFO] [3/6] Checking Actual Flight Information")
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.flight-grid")))
            time.sleep(2)

            flight_grids = driver.find_elements(By.CSS_SELECTOR, "div.flight-grid")
            actual_grid = flight_grids[1] if len(flight_grids) >= 2 else flight_grids[0]
            has_flight_info = ctx.check_has_actual_flight_info(actual_grid)

            if not has_flight_info:
                ctx.log("[INFO] No Actual Flight Information -> Create Actuals from Booked")
                arrow_icon = None
                try:
                    arrow_icon = actual_grid.find_element(By.XPATH, "./preceding::button[1]//span[contains(@class, 'k-i-arrow-s')] | ./preceding::div[1]//span[contains(@class, 'k-i-arrow-s')]")
                except Exception:
                    pass
                if not arrow_icon:
                    for arr in driver.find_elements(By.XPATH, "//span[contains(@class, 'k-i-arrow-s')]"):
                        if arr.is_displayed():
                            arrow_icon = arr
                            break
                if not arrow_icon:
                    ctx.add_irr({
                        "pup_date": "", "origin": target_mawb_origin, "mawb": target_mawb_no, "hawb": "",
                        "desc": "Create Actuals arrow icon not found", "corr": "",
                        "open_date": datetime.now().strftime("%m-%d %H:%M")
                    })
                    try: driver.close()
                    except Exception: pass
                    driver.switch_to.window(breakbulk_window)
                    time.sleep(1.5)
                    continue

                driver.execute_script("arguments[0].click();", arrow_icon)
                time.sleep(0.5)
                create_actuals_btn = wait.until(
                    EC.element_to_be_clickable((By.XPATH, "//div[@name='createActualsFromBooked' or contains(text(), 'Create Actuals from Booked')]"))
                )
                driver.execute_script("arguments[0].click();", create_actuals_btn)
                time.sleep(1)

                save_flight_btn = wait.until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'save-btn') and (contains(text(), 'Save') or @value='Save')]"))
                )
                driver.execute_script("arguments[0].click();", save_flight_btn)
                time.sleep(3)

                try:
                    WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                except Exception:
                    pass

                unexpected_err = ctx.handle_unexpected_error_modal()
                if unexpected_err:
                    ctx.add_irr({
                        "pup_date": "", "origin": target_mawb_origin, "mawb": target_mawb_no, "hawb": "",
                        "desc": unexpected_err, "corr": "",
                        "open_date": datetime.now().strftime("%m-%d %H:%M")
                    })
                    try: driver.close()
                    except Exception: pass
                    driver.switch_to.window(breakbulk_window)
                    time.sleep(1.5)
                    continue

                time.sleep(2)
                flight_grids_after = driver.find_elements(By.CSS_SELECTOR, "div.flight-grid")
                actual_grid_after = flight_grids_after[1] if len(flight_grids_after) >= 2 else flight_grids_after[0]
                if not ctx.check_has_actual_flight_info(actual_grid_after):
                    err_msg = ctx.handle_unexpected_error_modal() or "Actual Flight Information creation failed (still empty after save)"
                    ctx.add_irr({
                        "pup_date": "", "origin": target_mawb_origin, "mawb": target_mawb_no, "hawb": "",
                        "desc": err_msg, "corr": "",
                        "open_date": datetime.now().strftime("%m-%d %H:%M")
                    })
                    try: driver.close()
                    except Exception: pass
                    driver.switch_to.window(breakbulk_window)
                    time.sleep(1.5)
                    continue

            ctx.log("[INFO] [4/6] Charges tab")
            charges_tab = wait.until(
                EC.element_to_be_clickable((By.XPATH, "//div[contains(@id, 'tabStripWizard_') and .//span[contains(text(), 'Charges')]] | //span[contains(@class, 'wz-st-name') and text()='Charges']"))
            )
            driver.execute_script("arguments[0].click();", charges_tab)
            time.sleep(3)
            try:
                WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
            except Exception:
                pass

            ctx.log("[INFO] [5/6] Verifying Disposition / Import Bill To ID")
            parsed_hawb_list = []
            mawb_billto_save_error = False
            skipped_completed_count = 0

            while True:
                time.sleep(2)
                try:
                    WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                except Exception:
                    pass

                charges_rows = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tr.k-master-row")))
                parsed_hawb_list.clear()
                skipped_completed_count = 0
                needs_save = False

                for r in charges_rows:
                    try:
                        hawb_link = r.find_element(By.CSS_SELECTOR, "td a[href*='customer-waybill'] span.case-cell")
                        hawb_no = hawb_link.text.strip()
                        if not hawb_no:
                            continue
                    except Exception:
                        continue

                    try:
                        disp_val = ""
                        try:
                            disp_spans = r.find_elements(By.CSS_SELECTOR, "select[name='disposition'] ~ span.k-input, td [id*='disposition'] span.k-input")
                            if disp_spans:
                                disp_val = disp_spans[0].text.strip()
                            else:
                                disp_wraps = r.find_elements(By.XPATH, ".//select[@name='disposition']/ancestor::span[contains(@class, 'k-dropdown')]//span[contains(@class, 'k-input')]")
                                if disp_wraps:
                                    disp_val = disp_wraps[0].text.strip()
                        except Exception:
                            pass

                        if disp_val:
                            ctx.log(f"[INFO] HAWB {hawb_no}: Disposition('{disp_val}') OK")
                            skipped_completed_count += 1
                            continue

                        cnee_name = r.find_element(By.CSS_SELECTOR, "input[name='consigneeName']").get_attribute("value").strip()
                        shipper_name = r.find_element(By.CSS_SELECTOR, "input[name='shipperName']").get_attribute("value").strip()
                        num_pcs = r.find_element(By.CSS_SELECTOR, "input[name='num_pieces']").get_attribute("value").strip()
                        act_wt = r.find_element(By.CSS_SELECTOR, "input[name='total_actual_weight']").get_attribute("value").strip()
                        chg_wt = r.find_element(By.CSS_SELECTOR, "input[name='total_chargeable_weight']").get_attribute("value").strip()

                        billto_input = r.find_element(By.CSS_SELECTOR, "input[name='import_billto_cc_id']")
                        billto_val = (billto_input.get_attribute("value") or driver.execute_script("return arguments[0].value;", billto_input) or "").strip()

                        if billto_val != "" and billto_val != "302154970":
                            ctx.log(f"[INFO] HAWB {hawb_no}: Import Bill To ID('{billto_val}') -> 302154970")
                            try: billto_input.click()
                            except Exception: driver.execute_script("arguments[0].click();", billto_input)
                            time.sleep(0.2)
                            driver.execute_script("arguments[0].value = '';", billto_input)
                            billto_input.send_keys(Keys.CONTROL, "a")
                            billto_input.send_keys(Keys.BACKSPACE)
                            time.sleep(0.1)
                            for char in "302154970":
                                billto_input.send_keys(char)
                                time.sleep(0.1)
                            billto_input.send_keys(Keys.TAB)
                            time.sleep(0.3)
                            try:
                                WebDriverWait(driver, 5).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                            except Exception:
                                pass
                            needs_save = True
                            break

                        parsed_hawb_list.append({
                            "hawb_no": hawb_no, "consignee": cnee_name, "shipper": shipper_name,
                            "pieces": num_pcs, "act_weight": act_wt, "chg_weight": chg_wt, "billto_id": billto_val
                        })
                    except Exception as row_ex:
                        ctx.active_context["hawb"] = hawb_no
                        raise RuntimeError(f"HAWB {hawb_no} Charges row inspection error: {row_ex}")

                if needs_save:
                    save_btn = wait.until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'save-btn') and (contains(text(), 'Save') or @value='Save')]"))
                    )
                    driver.execute_script("arguments[0].click();", save_btn)
                    time.sleep(3)
                    unexpected_err = ctx.handle_unexpected_error_modal()
                    if unexpected_err:
                        ctx.add_irr({
                            "pup_date": "", "origin": target_mawb_origin, "mawb": target_mawb_no, "hawb": "",
                            "desc": unexpected_err, "corr": "",
                            "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        mawb_billto_save_error = True
                        break
                    try:
                        WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                    except Exception:
                        pass
                    continue
                else:
                    break

            if mawb_billto_save_error:
                try: driver.close()
                except Exception: pass
                driver.switch_to.window(breakbulk_window)
                time.sleep(1.5)
                continue

            apple_hawbs = [h for h in parsed_hawb_list if "APPLE" in h["consignee"].upper()]
            if not apple_hawbs:
                if skipped_completed_count == 0:
                    ctx.add_irr({
                        "pup_date": "", "origin": target_mawb_origin, "mawb": target_mawb_no, "hawb": "",
                        "desc": "No APPLE HAWB found in MAWB Charges", "corr": "",
                        "open_date": datetime.now().strftime("%m-%d %H:%M")
                    })
                try: driver.close()
                except Exception: pass
                driver.switch_to.window(breakbulk_window)
                time.sleep(1.5)
                continue

            ctx.log(f"[INFO] [6/6] APPLE HAWBs: {len(apple_hawbs)}")

            for h_index, target_hawb in enumerate(apple_hawbs, start=1):
                if ctx.stop_requested():
                    break
                hawb_window = None
                target_hawb_no = target_hawb["hawb_no"]
                current_origin = target_mawb_origin
                is_pvg = (current_origin == "PVG")
                is_han = (current_origin == "HAN")

                ctx.active_context["hawb"] = target_hawb_no
                ctx.active_context["origin"] = current_origin
                ctx.active_context["pup_date"] = ""

                try:
                    driver.switch_to.window(mawb_window)
                    time.sleep(1)
                    ctx.log(f"[INFO] [{h_index}/{len(apple_hawbs)}] HAWB {target_hawb_no} ({current_origin})")

                    charges_rows = driver.find_elements(By.CSS_SELECTOR, "tr.k-master-row")
                    hawb_link_element = None
                    for r in charges_rows:
                        try:
                            cell = r.find_element(By.CSS_SELECTOR, "td a[href*='customer-waybill'] span.case-cell")
                            if cell.text.strip() == target_hawb_no:
                                hawb_link_element = r.find_element(By.CSS_SELECTOR, "td a[href*='customer-waybill']")
                                break
                        except Exception:
                            continue
                    if not hawb_link_element:
                        hawb_link_element = driver.find_element(By.XPATH, f"//a[contains(@href, 'customer-waybill') and .//span[text()='{target_hawb_no}']]")

                    hawb_handles = driver.window_handles
                    try:
                        ActionChains(driver).key_down(Keys.SHIFT).click(hawb_link_element).key_up(Keys.SHIFT).perform()
                    except Exception:
                        h_href = hawb_link_element.get_attribute("href")
                        driver.execute_script("window.open(arguments[0], '_blank');", h_href)

                    time.sleep(1)
                    try:
                        WebDriverWait(driver, 8).until(lambda d: len(d.window_handles) > len(hawb_handles))
                        hawb_window = [w for w in driver.window_handles if w not in hawb_handles][0]
                        driver.switch_to.window(hawb_window)
                    except Exception:
                        h_href = hawb_link_element.get_attribute("href")
                        driver.execute_script("window.open(arguments[0], '_blank');", h_href)
                        WebDriverWait(driver, 8).until(lambda d: len(d.window_handles) > len(hawb_handles))
                        hawb_window = [w for w in driver.window_handles if w not in hawb_handles][0]
                        driver.switch_to.window(hawb_window)

                    time.sleep(3)
                    try:
                        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
                    except Exception:
                        pass

                    # [HAWB 작업 1] Disposition = kwe -> TAB
                    disposition_dropdown = wait.until(
                        EC.element_to_be_clickable((By.XPATH, "//select[@name='disposition']/ancestor::span[contains(@class, 'k-dropdown')]//span[contains(@class, 'k-dropdown-wrap')] | //span[contains(@id, '__disposition') and contains(@class, 'k-dropdown-wrap')]"))
                    )
                    try: disposition_dropdown.click()
                    except Exception: driver.execute_script("arguments[0].click();", disposition_dropdown)
                    time.sleep(0.5)
                    active_el = driver.switch_to.active_element
                    active_el.send_keys("kwe")
                    time.sleep(0.3)
                    active_el.send_keys(Keys.TAB)
                    time.sleep(0.5)

                    disp_input_text = ""
                    try:
                        disp_input_el = disposition_dropdown.find_element(By.CSS_SELECTOR, "span.k-input")
                        disp_input_text = (disp_input_el.text or driver.execute_script("return arguments[0].textContent;", disp_input_el) or "").strip()
                    except Exception:
                        pass

                    if "KWE" not in disp_input_text.upper():
                        err_msg = f"Disposition 'KWE' selection failed (Current: '{disp_input_text}')"
                        ctx.add_irr({
                            "pup_date": "", "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no,
                            "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    # [HAWB 작업 2] References 탭 & PUP DATE
                    ref_tab = wait.until(
                        EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'wz-st-name') and text()='References'] | //div[contains(@id, 'tabStripWizard_') and .//span[contains(text(), 'References')]]"))
                    )
                    driver.execute_script("arguments[0].click();", ref_tab)
                    time.sleep(2.5)
                    try:
                        WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                    except Exception:
                        pass

                    pup_date_text, pup_month, pup_day, pup_status = _open_check_pup_date_from_ui(driver, target_hawb_no, log_fn=ctx.log)
                    ctx.active_context["pup_date"] = pup_date_text

                    if pup_status == "conflicting":
                        ctx.add_irr({
                            "pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no,
                            "hawb": target_hawb_no, "desc": "Conflicting PUP DATE", "corr": "",
                            "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue
                    elif pup_status == "omitted" or not pup_month or not pup_day:
                        ctx.add_irr({
                            "pup_date": "", "origin": current_origin, "mawb": target_mawb_no,
                            "hawb": target_hawb_no, "desc": "PUP DATE omitted", "corr": "",
                            "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    # [HAWB 작업 3] Service Type (AS/AD) 판별
                    time.sleep(2)
                    service_code = ""
                    matched_ref_text = ""

                    try:
                        js_values = driver.execute_script("""
                            var results = [];
                            try {
                                var grids = window.jQuery ? window.jQuery('.k-grid') : [];
                                for (var g = 0; g < grids.length; g++) {
                                    var grid = window.jQuery(grids[g]).data('kendoGrid');
                                    if (grid && grid.dataSource) {
                                        var data = grid.dataSource.data();
                                        for (var i = 0; i < data.length; i++) {
                                            var row = data[i];
                                            for (var key in row) {
                                                if (row.hasOwnProperty(key) && typeof row[key] === 'string') {
                                                    var s = row[key].trim();
                                                    if (s && (s.toUpperCase().indexOf('SERV') !== -1 || s.toUpperCase().indexOf('SVC') !== -1)) {
                                                        results.push(s);
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            } catch (e) {}
                            return results;
                        """)
                        if js_values:
                            for jv in js_values:
                                _st, s_code = _open_extract_service_type_from_text(jv)
                                if s_code:
                                    service_code = s_code
                                    matched_ref_text = jv
                                    break
                    except Exception:
                        pass

                    if not service_code:
                        for inp in driver.find_elements(By.CSS_SELECTOR, "input[name='ref_num'], input[name='ll_ref_num']"):
                            try:
                                val = (inp.get_attribute("value") or driver.execute_script("return arguments[0].value;", inp) or "").strip()
                                _st, s_code = _open_extract_service_type_from_text(val)
                                if s_code:
                                    service_code = s_code
                                    matched_ref_text = val
                                    break
                            except Exception:
                                continue

                    if not service_code:
                        try:
                            for v_cont in driver.find_elements(By.CSS_SELECTOR, "div.k-virtual-content, div.k-grid-content"):
                                scroll_height = driver.execute_script("return arguments[0].scrollHeight;", v_cont) or 0
                                client_height = driver.execute_script("return arguments[0].clientHeight;", v_cont) or 0
                                if scroll_height > client_height:
                                    step = max(int(client_height * 0.7), 200)
                                    for top in range(0, scroll_height + step, step):
                                        driver.execute_script("arguments[0].scrollTop = arguments[1];", v_cont, top)
                                        time.sleep(0.25)
                                        for inp in driver.find_elements(By.CSS_SELECTOR, "input[name='ref_num'], input[name='ll_ref_num']"):
                                            try:
                                                val = (inp.get_attribute("value") or driver.execute_script("return arguments[0].value;", inp) or "").strip()
                                                _st, s_code = _open_extract_service_type_from_text(val)
                                                if s_code:
                                                    service_code = s_code
                                                    matched_ref_text = val
                                                    break
                                            except Exception:
                                                continue
                                        if service_code:
                                            break
                                    try: driver.execute_script("arguments[0].scrollTop = 0;", v_cont)
                                    except Exception: pass
                                if service_code:
                                    break
                        except Exception:
                            pass

                    if not service_code:
                        try:
                            matches = re.findall(r'value=[\'"]([^\'"]*(?:service|serivce|svc)[^\'"]*)[\'"]', driver.page_source, re.IGNORECASE)
                            for m in matches:
                                _st, s_code = _open_extract_service_type_from_text(m)
                                if s_code:
                                    service_code = s_code
                                    matched_ref_text = m
                                    break
                        except Exception:
                            pass

                    if not service_code:
                        ctx.add_irr({
                            "pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no,
                            "hawb": target_hawb_no, "desc": "Service Type omitted", "corr": "",
                            "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    ctx.log(f"[INFO] Service: '{matched_ref_text}' -> [{service_code}]")

                    # [HAWB 작업 4] Shipment Details -> Chargeable Weight
                    shipment_details_tab = wait.until(
                        EC.element_to_be_clickable((By.XPATH, "//div[contains(@id, 'tabStripWizard_') and .//span[contains(text(), 'Shipment Details')]] | //span[contains(@class, 'wz-st-name') and text()='Shipment Details']"))
                    )
                    driver.execute_script("arguments[0].click();", shipment_details_tab)
                    time.sleep(2)
                    try:
                        WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                    except Exception:
                        pass

                    chargeable_wt_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='chargeableWt']")))
                    chargeable_wt = (chargeable_wt_input.get_attribute("value") or driver.execute_script("return arguments[0].value;", chargeable_wt_input) or "").strip()

                    # [HAWB 작업 5] HAWB Charges 탭
                    hawb_charges_tab = wait.until(
                        EC.element_to_be_clickable((By.XPATH, "//div[contains(@id, 'tabStripWizard_') and .//span[contains(text(), 'Charges')]] | //span[contains(@class, 'wz-st-name') and text()='Charges']"))
                    )
                    driver.execute_script("arguments[0].click();", hawb_charges_tab)
                    time.sleep(2)
                    try:
                        WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                    except Exception:
                        pass
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.charges-grid")))
                    time.sleep(1.5)

                    initial_charges = _open_parse_hawb_charges_grid(driver)
                    present_codes = [c["code"] for c in initial_charges]
                    has_magnerv_initial = ("MAGNERV" in present_codes) if is_pvg else False

                    # [HAWB 작업 6] 엑셀 운임 대조 선검증
                    excel_path = ctx.get_file_path("APPLE RATE DATA.xlsx")
                    matched_sheet = _open_find_matching_excel_sheet(excel_path, pup_month, pup_day, log_fn=ctx.log)
                    if not matched_sheet:
                        ctx.add_irr({
                            "pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no,
                            "hawb": target_hawb_no, "desc": f"No matching Excel sheet found for PUP DATE {pup_date_text}",
                            "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    excel_calc = _open_calculate_excel_rates(excel_path, matched_sheet, chargeable_wt, origin=current_origin, log_fn=ctx.log)
                    if not excel_calc:
                        ctx.add_irr({
                            "pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no,
                            "hawb": target_hawb_no, "desc": f"Failed to calculate Excel rates for sheet '{matched_sheet}'",
                            "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    expected_frtrv = excel_calc["FRT_AS"] if service_code == "AS" else excel_calc["FRT_AD"]
                    target_frt_cell = excel_calc["FRT_AS_CELL"] if service_code == "AS" else excel_calc["FRT_AD_CELL"]
                    expected_fscrv = excel_calc["FSC"]
                    target_fsc_cell = excel_calc["FSC_CELL"]
                    expected_handrv = excel_calc["HAN"]
                    target_han_cell = excel_calc["HAN_CELL"]
                    fcf_cell_used = excel_calc["FCF_AS_CELL"] if service_code == "AS" else excel_calc["FCF_AD_CELL"]
                    fcf_val_used = excel_calc["FCF_AS"] if service_code == "AS" else excel_calc["FCF_AD"]
                    ctx.log(f"[INFO] Excel [{excel_calc.get('SOURCE')}] sheet='{matched_sheet}' wt={chargeable_wt} svc={service_code} | "
                            f"{target_frt_cell}={expected_frtrv} {target_fsc_cell}={expected_fscrv} {target_han_cell}={expected_handrv} {fcf_cell_used}={fcf_val_used}")
                    bad_used = {c: r for c, r in excel_calc.get("BAD", {}).items()
                                if c in (target_frt_cell, target_fsc_cell, target_han_cell, fcf_cell_used)}
                    _unused = {excel_calc["FRT_AD_CELL"] if service_code == "AS" else excel_calc["FRT_AS_CELL"],
                               excel_calc["FCF_AD_CELL"] if service_code == "AS" else excel_calc["FCF_AS_CELL"]}
                    if not (is_pvg and has_magnerv_initial):
                        _unused |= {f"I{excel_calc['row']}", f"J{excel_calc['row']}"}
                    stale_used = {k: v for k, v in excel_calc.get("STALE", {}).items() if k.rsplit("!", 1)[-1] not in _unused}
                    if bad_used or stale_used:
                        parts = [f"{c}: {r}" for c, r in bad_used.items()] + [f"{c}: {r}" for c, r in stale_used.items()]
                        err_msg = f"Excel value could not be read/calculated (sheet '{matched_sheet}'): " + " | ".join(parts)
                        ctx.log(f"[WARN] {target_hawb_no}: {err_msg}")
                        ctx.add_irr({
                            "pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no,
                            "hawb": target_hawb_no, "desc": err_msg, "corr": "",
                            "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    initial_charges_map = {c["code"]: c for c in initial_charges}
                    pre_mismatches = []

                    if "FRTRV" in initial_charges_map:
                        frt_data = initial_charges_map["FRTRV"]
                        wb_val, inv_val = frt_data["wb_amt"], frt_data["inv_amt"]
                        if not ctx.is_amount_equal(wb_val, inv_val):
                            err_msg = f"FRTRV Waybill Amt ({wb_val}) != Invoice Amt ({inv_val})"
                            pre_mismatches.append(err_msg)
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        if not ctx.is_amount_equal(wb_val, expected_frtrv):
                            pre_mismatches.append(f"FRTRV UFS Amt ({wb_val}) != APPLE RATE DATA ({expected_frtrv})")
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"FRTRV ${_open_fmt_amt(wb_val)}", "corr": f"FRTRV ${_open_fmt_amt(expected_frtrv)}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                    else:
                        pre_mismatches.append("FRTRV code missing from initial charges")
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": "FRTRV code missing from initial charges", "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})

                    if "FSCRV" in initial_charges_map:
                        fsc_data = initial_charges_map["FSCRV"]
                        wb_val, inv_val = fsc_data["wb_amt"], fsc_data["inv_amt"]
                        if not ctx.is_amount_equal(wb_val, inv_val):
                            err_msg = f"FSCRV Waybill Amt ({wb_val}) != Invoice Amt ({inv_val})"
                            pre_mismatches.append(err_msg)
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        if not ctx.is_amount_equal(wb_val, expected_fscrv):
                            pre_mismatches.append(f"FSCRV UFS Amt ({wb_val}) != APPLE RATE DATA ({expected_fscrv})")
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"FSCRV ${_open_fmt_amt(wb_val)}", "corr": f"FSCRV ${_open_fmt_amt(expected_fscrv)}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                    else:
                        pre_mismatches.append("FSCRV code missing from initial charges")
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": "FSCRV code missing from initial charges", "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})

                    if is_pvg and has_magnerv_initial:
                        if "MAGNERV" in initial_charges_map:
                            mag_data = initial_charges_map["MAGNERV"]
                            wb_val, inv_val = mag_data["wb_amt"], mag_data["inv_amt"]
                            if not ctx.is_amount_equal(wb_val, inv_val):
                                err_msg = f"MAGNERV Waybill Amt ({wb_val}) != Invoice Amt ({inv_val})"
                                pre_mismatches.append(err_msg)
                                ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                            cand_i, cand_j = excel_calc["MAG1"], excel_calc["MAG2"]
                            if not (ctx.is_amount_equal(wb_val, cand_i) or ctx.is_amount_equal(wb_val, cand_j)):
                                pre_mismatches.append(f"MAGNERV UFS Amt ({wb_val}) != APPLE RATE DATA ({cand_i}) or ({cand_j})")
                                ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"MAGNERV ${_open_fmt_amt(wb_val)}", "corr": f"MAGNERV ${_open_fmt_amt(cand_i)}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        else:
                            pre_mismatches.append("MAGNERV code missing from initial charges")
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": "MAGNERV code missing from initial charges", "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                    elif is_han and "MAGNERV" in initial_charges_map:
                        err_msg = "Unexpected MAGNERV found in HAN shipment (MAGNERV should not exist)"
                        pre_mismatches.append(err_msg)
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})

                    if pre_mismatches:
                        ctx.log(f"[WARN] {target_hawb_no}: pre-check mismatch {pre_mismatches}")
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    # [HAWB 작업 7] HANDRV 수정
                    for crow_data in initial_charges:
                        if crow_data["code"] == "HANDRV":
                            row_el = crow_data["row_element"]
                            terms_wrap = row_el.find_element(By.CSS_SELECTOR, "select[name='terms'] ~ span.k-select, td:nth-child(6) span.k-dropdown-wrap")
                            try: terms_wrap.click()
                            except Exception: driver.execute_script("arguments[0].click();", terms_wrap)
                            time.sleep(0.3)
                            act_el = driver.switch_to.active_element
                            act_el.send_keys("P")
                            time.sleep(0.3)
                            act_el.send_keys(Keys.TAB)
                            time.sleep(0.5)

                            curr_wrap = row_el.find_element(By.CSS_SELECTOR, "select[name='invoiceCurr'] ~ span.k-select, td:nth-child(10) span.k-dropdown-wrap")
                            try: curr_wrap.click()
                            except Exception: driver.execute_script("arguments[0].click();", curr_wrap)
                            time.sleep(0.3)
                            act_el = driver.switch_to.active_element
                            for char in "USD":
                                act_el.send_keys(char)
                                time.sleep(0.1)
                            time.sleep(0.3)
                            act_el.send_keys(Keys.TAB)
                            time.sleep(1)
                            break

                    time.sleep(2)
                    try:
                        WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                    except Exception:
                        pass

                    updated_charges = _open_parse_hawb_charges_grid(driver)
                    charges_map = {c["code"]: c for c in updated_charges}
                    handrv_mismatches = []

                    if "HANDRV" in charges_map:
                        h_data = charges_map["HANDRV"]
                        wb_val, inv_val = h_data["wb_amt"], h_data["inv_amt"]
                        if h_data["terms"] != "Prepaid":
                            err_msg = f"HANDRV Terms mismatch (Current: '{h_data['terms']}', Expected: 'Prepaid')"
                            handrv_mismatches.append(err_msg)
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        if h_data["inv_curr"] != "USD":
                            err_msg = f"HANDRV InvoiceCurr mismatch (Current: '{h_data['inv_curr']}', Expected: 'USD')"
                            handrv_mismatches.append(err_msg)
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        if not ctx.is_amount_equal(wb_val, inv_val):
                            err_msg = f"HANDRV Waybill Amt ({wb_val}) != Invoice Amt ({inv_val})"
                            handrv_mismatches.append(err_msg)
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        if not ctx.is_amount_equal(wb_val, expected_handrv):
                            handrv_mismatches.append(f"HANDRV UFS Amt ({wb_val}) != Excel {target_han_cell} ({expected_handrv})")
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"HANDRV ${_open_fmt_amt(wb_val)}", "corr": f"HANDRV ${_open_fmt_amt(expected_handrv)}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                    else:
                        handrv_mismatches.append("HANDRV code missing from charges")
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": "HANDRV code missing from charges", "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})

                    if handrv_mismatches:
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    # [HAWB 작업 8] Ctrl+S 저장
                    try:
                        ActionChains(driver).key_down(Keys.CONTROL).send_keys("s").key_up(Keys.CONTROL).perform()
                    except Exception:
                        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.CONTROL, "s")

                    time.sleep(3)
                    unexpected_err = ctx.handle_unexpected_error_modal()
                    if unexpected_err:
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": unexpected_err, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    try:
                        WebDriverWait(driver, 20).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                    except Exception:
                        pass

                    expected_all_codes = {"FCFAD", "FCFOAD", "HANDRV", "FRTRV", "FSCRV"}
                    if is_pvg and has_magnerv_initial:
                        expected_all_codes.add("MAGNERV")

                    post_charges = []
                    post_codes = []
                    poll_start = time.time()
                    save_modal_error = None

                    while time.time() - poll_start < 30:
                        try:
                            save_modal_error = ctx.handle_unexpected_error_modal()
                            if save_modal_error:
                                break
                            masks = driver.find_elements(By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")
                            if any(m.is_displayed() for m in masks):
                                time.sleep(1)
                                continue
                            post_charges = _open_parse_hawb_charges_grid(driver)
                            post_codes = [c["code"] for c in post_charges]
                            if expected_all_codes.issubset(set(post_codes)):
                                time.sleep(1.5)
                                post_charges = _open_parse_hawb_charges_grid(driver)
                                post_codes = [c["code"] for c in post_charges]
                                if expected_all_codes.issubset(set(post_codes)):
                                    break
                        except Exception:
                            pass
                        time.sleep(1.5)

                    if save_modal_error:
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": save_modal_error, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    # [HAWB 작업 9] 저장 후 검증
                    post_map = {c["code"]: c for c in post_charges}
                    post_mismatches = []
                    missing_post_codes = expected_all_codes - set(post_codes)
                    extra_post_codes = set(post_codes) - expected_all_codes

                    if len(post_codes) != len(set(post_codes)):
                        dup_codes = sorted(list({c for c in post_codes if post_codes.count(c) > 1}))
                        err_msg = f"Duplicate post-save charge codes found: {dup_codes}"
                        post_mismatches.append(err_msg)
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})

                    if missing_post_codes:
                        err_msg = f"Missing required post-save charge codes: {sorted(list(missing_post_codes))}"
                        post_mismatches.append(err_msg)
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})

                    if extra_post_codes:
                        err_msg = f"Unexpected post-save charge codes found: {sorted(list(extra_post_codes))}"
                        post_mismatches.append(err_msg)
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})

                    expected_fcfad = excel_calc["FCF_AS"] if service_code == "AS" else excel_calc["FCF_AD"]
                    if "FCFAD" in post_map:
                        fcf_data = post_map["FCFAD"]
                        wb_val, inv_val = fcf_data["wb_amt"], fcf_data["inv_amt"]
                        if not ctx.is_amount_equal(wb_val, inv_val):
                            err_msg = f"FCFAD Waybill Amt ({wb_val}) != Invoice Amt ({inv_val})"
                            post_mismatches.append(err_msg)
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        if not ctx.is_amount_equal(wb_val, expected_fcfad):
                            post_mismatches.append("FCFAD mismatch")
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"FCFAD ${_open_fmt_amt(wb_val)}", "corr": f"FCFAD ${_open_fmt_amt(expected_fcfad)}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                    else:
                        post_mismatches.append("FCFAD missing")
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": "FCFAD charge code was not generated after save", "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})

                    if "HANDRV" in post_map and not ctx.is_amount_equal(post_map["HANDRV"]["wb_amt"], expected_handrv):
                        post_mismatches.append("HANDRV final mismatch")
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"HANDRV ${_open_fmt_amt(post_map['HANDRV']['wb_amt'])}", "corr": f"HANDRV ${_open_fmt_amt(expected_handrv)}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                    if "FRTRV" in post_map and not ctx.is_amount_equal(post_map["FRTRV"]["wb_amt"], expected_frtrv):
                        post_mismatches.append("FRTRV final mismatch")
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"FRTRV ${_open_fmt_amt(post_map['FRTRV']['wb_amt'])}", "corr": f"FRTRV ${_open_fmt_amt(expected_frtrv)}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                    if "FSCRV" in post_map and not ctx.is_amount_equal(post_map["FSCRV"]["wb_amt"], expected_fscrv):
                        post_mismatches.append("FSCRV final mismatch")
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"FSCRV ${_open_fmt_amt(post_map['FSCRV']['wb_amt'])}", "corr": f"FSCRV ${_open_fmt_amt(expected_fscrv)}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                    if is_pvg and has_magnerv_initial and "MAGNERV" in post_map:
                        mag_w = post_map["MAGNERV"]["wb_amt"]
                        if not (ctx.is_amount_equal(mag_w, excel_calc["MAG1"]) or ctx.is_amount_equal(mag_w, excel_calc["MAG2"])):
                            post_mismatches.append("MAGNERV final mismatch")
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"MAGNERV ${_open_fmt_amt(mag_w)}", "corr": f"MAGNERV ${_open_fmt_amt(excel_calc['MAG1'])}", "open_date": datetime.now().strftime("%m-%d %H:%M")})

                    if post_mismatches:
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    # [HAWB 작업 10] Create Bill As a Charged Invoice
                    time.sleep(2)
                    try:
                        WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                    except Exception:
                        pass

                    new_btn_xpaths = [
                        "//button[@aria-label='New dropdownbutton']",
                        "//button[contains(@class, 'k-button') and contains(., 'New') and .//span[contains(@class, 'k-i-plus')]]",
                        "//div[contains(@class, 'k-dropdown-button')]//button",
                        "//button[normalize-space(.)='New']"
                    ]
                    target_new_btn = None
                    for xpath in new_btn_xpaths:
                        btns = driver.find_elements(By.XPATH, xpath)
                        if len(btns) >= 2:
                            target_new_btn = btns[1]
                            break
                        elif len(btns) == 1:
                            target_new_btn = btns[0]
                            break
                    if target_new_btn:
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target_new_btn)
                        time.sleep(0.3)
                        driver.execute_script("arguments[0].click();", target_new_btn)

                    time.sleep(1.2)
                    menu_xpaths = [
                        "//li[contains(@class, 'k-item') and contains(text(), 'Create Bill As a Charged Invoice')]",
                        "//li[@role='menuItem' and contains(text(), 'Create Bill As a Charged Invoice')]",
                        "//*[contains(text(), 'Create Bill As a Charged Invoice')]",
                        "//li[contains(., 'Create Bill As a Charged Invoice')]"
                    ]
                    bill_item_clicked = False
                    for _ in range(10):
                        for mxpath in menu_xpaths:
                            try:
                                for it in driver.find_elements(By.XPATH, mxpath):
                                    if it.is_displayed() or it.is_enabled():
                                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", it)
                                        time.sleep(0.2)
                                        try: it.click()
                                        except Exception: driver.execute_script("arguments[0].click();", it)
                                        bill_item_clicked = True
                                        break
                                if bill_item_clicked:
                                    break
                            except Exception:
                                continue
                        if bill_item_clicked:
                            break
                        time.sleep(1)

                    if not bill_item_clicked:
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": "Failed to locate or click 'Create Bill As a Charged Invoice'", "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    time.sleep(3)
                    unexpected_err = ctx.handle_unexpected_error_modal()
                    if unexpected_err:
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": unexpected_err, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    try:
                        WebDriverWait(driver, 25).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                    except Exception:
                        pass
                    time.sleep(2)

                    now = datetime.now()
                    today_date_str = now.strftime("%d/%m/%Y")
                    today_candidates = {today_date_str, f"{now.day}/{now.month}/{now.year}", f"{now.day:02d}/{now.month:02d}/{now.year}"}

                    matched_invoices = []
                    poll_start = time.time()
                    invoice_modal_error = None

                    while time.time() - poll_start < 30:
                        try:
                            invoice_modal_error = ctx.handle_unexpected_error_modal()
                            if invoice_modal_error:
                                break
                            masks = driver.find_elements(By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")
                            if any(m.is_displayed() for m in masks):
                                time.sleep(1)
                                continue
                        except Exception:
                            pass

                        matched_invoices.clear()
                        for r in driver.find_elements(By.CSS_SELECTOR, "tr.k-master-row"):
                            try:
                                inv_no_val = ""
                                inv_no_els = r.find_elements(By.XPATH, ".//a[contains(@href, 'accounting/invoicing') or contains(@href, 'invoicing')]//span[contains(@class, 'case-cell')]")
                                if not inv_no_els:
                                    inv_no_els = r.find_elements(By.CSS_SELECTOR, "a[href*='invoicing'] span.case-cell, a[href*='invoicing'] span")
                                if inv_no_els:
                                    inv_no_val = (inv_no_els[0].text or driver.execute_script("return arguments[0].textContent;", inv_no_els[0]) or "").strip()
                                if not inv_no_val:
                                    continue

                                status_val = ""
                                status_els = r.find_elements(By.XPATH, ".//select[@name='invStatus']/ancestor::*[contains(@class, 'k-dropdown') or contains(@class, 'k-dropdown-wrap')]//span[contains(@class, 'k-input')]")
                                if not status_els:
                                    status_els = r.find_elements(By.CSS_SELECTOR, "select[name='invStatus'] ~ span.k-input, [id*='invStatus'] span.k-input")
                                if status_els:
                                    status_val = (status_els[0].text or driver.execute_script("return arguments[0].textContent;", status_els[0]) or "").strip()

                                imp_exp_val = ""
                                impexp_els = r.find_elements(By.XPATH, ".//select[@name='impExp']/ancestor::*[contains(@class, 'k-dropdown') or contains(@class, 'k-dropdown-wrap')]//span[contains(@class, 'k-input')]")
                                if not impexp_els:
                                    impexp_els = r.find_elements(By.CSS_SELECTOR, "select[name='impExp'] ~ span.k-input, [id*='impExp'] span.k-input")
                                if impexp_els:
                                    imp_exp_val = (impexp_els[0].text or driver.execute_script("return arguments[0].textContent;", impexp_els[0]) or "").strip()

                                inv_date_val = ""
                                date_els = r.find_elements(By.CSS_SELECTOR, "input[name='invoiceDate']")
                                if date_els:
                                    inv_date_val = (date_els[0].get_attribute("value") or driver.execute_script("return arguments[0].value;", date_els[0]) or "").strip()

                                if status_val.upper() == "OPEN" and imp_exp_val.upper() == "IMPORT" and inv_date_val in today_candidates:
                                    matched_invoices.append({"no": inv_no_val, "status": status_val, "imp_exp": imp_exp_val, "date": inv_date_val})
                            except Exception:
                                continue

                        if len(matched_invoices) == 2:
                            break
                        time.sleep(1.5)

                    if invoice_modal_error:
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": invoice_modal_error, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    if len(matched_invoices) != 2:
                        err_msg = f"Invoice verification failed: Expected 2 Open/Import invoices for {today_date_str}, but found {len(matched_invoices)}"
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": err_msg, "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)
                        continue

                    ctx.log(f"[INFO] OPEN done {target_hawb_no} (2 invoices confirmed)")
                    ctx.add_open({
                        "pup_date": pup_date_text, "origin": current_origin,
                        "mawb": target_mawb_no, "hawb": target_hawb_no,
                        "open_date": datetime.now().strftime("%m-%d %H:%M")
                    })
                    try:
                        driver.close()
                        driver.switch_to.window(mawb_window)
                    except Exception:
                        pass
                    time.sleep(2)

                except Exception as h_ex:
                    modal_err = ctx.handle_unexpected_error_modal()
                    err_desc = modal_err if modal_err else f"Exception: {h_ex}"
                    ctx.log(f"[ERROR] HAWB {target_hawb_no}: exception {err_desc}")
                    ctx.add_irr({
                        "pup_date": ctx.active_context.get("pup_date", ""),
                        "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no,
                        "desc": err_desc, "corr": "",
                        "open_date": datetime.now().strftime("%m-%d %H:%M")
                    })
                    try:
                        if hawb_window and hawb_window in driver.window_handles:
                            driver.switch_to.window(hawb_window)
                            driver.close()
                    except Exception:
                        pass
                    try:
                        driver.switch_to.window(mawb_window)
                    except Exception:
                        pass
                    time.sleep(2)

            try: driver.close()
            except Exception: pass
            driver.switch_to.window(breakbulk_window)
            time.sleep(1.5)
            ctx.progress(m_idx, total_mawbs, f"Completed MAWB ({m_idx}/{total_mawbs}): {target_mawb_no}")

        except Exception as m_ex:
            modal_err = ctx.handle_unexpected_error_modal()
            err_desc = modal_err if modal_err else f"Exception: {m_ex}"
            ctx.log(f"[ERROR] MAWB {target_mawb_no}: exception {err_desc}")
            already_logged = any(
                r.get("mawb") == target_mawb_no and r.get("hawb") == ctx.active_context.get("hawb", "")
                for r in (ctx.irr_records + ctx.open_records)
            )
            if not already_logged:
                ctx.add_irr({
                    "pup_date": ctx.active_context.get("pup_date", ""),
                    "origin": ctx.active_context.get("origin") or target_mawb_origin or "PVG",
                    "mawb": target_mawb_no,
                    "hawb": ctx.active_context.get("hawb", ""),
                    "desc": err_desc, "corr": "",
                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                })
            try:
                for wh in list(driver.window_handles):
                    if wh != breakbulk_window:
                        driver.switch_to.window(wh)
                        driver.close()
            except Exception:
                pass
            try:
                driver.switch_to.window(breakbulk_window)
            except Exception:
                pass
            time.sleep(1.5)
            continue


def run(ctx):
    return _run_apple_open_profile(ctx)
