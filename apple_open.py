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

    for name in reversed(sheet_names):
        m = pattern.search(name.strip())
        if m:
            sm, sd, em, ed = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            start_t = (sm, sd)
            end_t = (em, ed)
            if start_t <= end_t:
                if start_t <= target <= end_t:
                    return name
            else:
                if target >= start_t or target <= end_t:
                    return name
    return None


def _open_calculate_excel_rates(excel_path, sheet_name, chargeable_wt, origin="PVG", log_fn=None):
    import openpyxl
    import gc
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

    def _to_float(v):
        return float(v) if v is not None else 0.0

    excel = None
    wb = None
    try:
        import win32com.client
        import pythoncom
        pythoncom.CoInitialize()

        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False

        wb = excel.Workbooks.Open(abs_path, UpdateLinks=0, ReadOnly=True)
        target_ws = None
        for s in wb.Worksheets:
            if s.Name == sheet_name:
                target_ws = s
                break
        if not target_ws:
            if log_fn:
                log_fn(f"[ERROR] Sheet '{sheet_name}' not found in Excel")
            return None

        target_ws.Range(input_cell).Value = wt_val
        excel.Calculate()

        d_val = _to_float(target_ws.Range(f"D{row}").Value)
        e_val = _to_float(target_ws.Range(f"E{row}").Value)
        f_val = _to_float(target_ws.Range(f"F{row}").Value)
        h_val = _to_float(target_ws.Range(f"H{row}").Value)

        if origin_upper == "HAN":
            fcf_as_val = _to_float(target_ws.Range(f"I{row}").Value)
            fcf_ad_val = _to_float(target_ws.Range(f"J{row}").Value)
            mag1_val, mag2_val = 0.0, 0.0
            fcf_as_cell, fcf_ad_cell = f"I{row}", f"J{row}"
        else:
            mag1_val = _to_float(target_ws.Range(f"I{row}").Value)
            mag2_val = _to_float(target_ws.Range(f"J{row}").Value)
            fcf_as_val = _to_float(target_ws.Range(f"K{row}").Value)
            fcf_ad_val = _to_float(target_ws.Range(f"L{row}").Value)
            fcf_as_cell, fcf_ad_cell = f"K{row}", f"L{row}"

        return {
            "origin": origin_upper, "row": row,
            "FRT_AS": d_val, "FRT_AD": e_val, "FSC": f_val, "HAN": h_val,
            "MAG1": mag1_val, "MAG2": mag2_val, "FCF_AS": fcf_as_val, "FCF_AD": fcf_ad_val,
            "FRT_AS_CELL": f"D{row}", "FRT_AD_CELL": f"E{row}",
            "FSC_CELL": f"F{row}", "HAN_CELL": f"H{row}",
            "FCF_AS_CELL": fcf_as_cell, "FCF_AD_CELL": fcf_ad_cell,
        }
    except Exception:
        pass
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

    try:
        wb = openpyxl.load_workbook(abs_path, data_only=False)
        if sheet_name not in wb.sheetnames:
            return None
        ws = wb[sheet_name]

        def _eval_cell(cell_val, weight, c_name):
            if isinstance(cell_val, (int, float)):
                return float(cell_val)
            if isinstance(cell_val, str) and cell_val.startswith("="):
                expr = cell_val[1:].replace(c_name, str(weight)).replace(c_name.lower(), str(weight))
                try:
                    return float(eval(expr, {"__builtins__": None}, {}))
                except Exception:
                    pass
            return 0.0

        d_val = _eval_cell(ws[f"D{row}"].value, wt_val, input_cell)
        e_val = _eval_cell(ws[f"E{row}"].value, wt_val, input_cell)
        f_val = _eval_cell(ws[f"F{row}"].value, wt_val, input_cell)
        h_val = _eval_cell(ws[f"H{row}"].value, wt_val, input_cell)

        if origin_upper == "HAN":
            fcf_as_val = _eval_cell(ws[f"I{row}"].value, wt_val, input_cell)
            fcf_ad_val = _eval_cell(ws[f"J{row}"].value, wt_val, input_cell)
            mag1_val, mag2_val = 0.0, 0.0
            fcf_as_cell, fcf_ad_cell = f"I{row}", f"J{row}"
        else:
            mag1_val = _eval_cell(ws[f"I{row}"].value, wt_val, input_cell)
            mag2_val = _eval_cell(ws[f"J{row}"].value, wt_val, input_cell)
            fcf_as_val = _eval_cell(ws[f"K{row}"].value, wt_val, input_cell)
            fcf_ad_val = _eval_cell(ws[f"L{row}"].value, wt_val, input_cell)
            fcf_as_cell, fcf_ad_cell = f"K{row}", f"L{row}"

        return {
            "origin": origin_upper, "row": row,
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
    """Target MAWB: type the MAWB into the Breakbulk MAWB input (no ETA / IC) and press SEARCH."""
    driver, wait = ctx.driver, ctx.wait
    box = None
    for _ in range(3):
        cands = [e for e in driver.find_elements(By.CSS_SELECTOR, "input.form-control.ted-input-text[maxlength='20']")
                 if e.is_displayed()]
        if len(cands) > 1:
            pref = []
            for e in cands:
                try:
                    ctxt = driver.execute_script(
                        "var p=arguments[0].closest('.form-group,.row,div');"
                        "for(var i=0;i<3&&p&&p.parentElement;i++){if(/mawb|master/i.test(p.innerText||''))return p.innerText;p=p.parentElement;}"
                        "return '';", e) or ""
                    if re.search(r"mawb|master", ctxt, re.I):
                        pref.append(e)
                except Exception:
                    pass
            cands = pref or cands
        if cands:
            box = cands[0]
            break
        time.sleep(1)
    if box is None:
        raise RuntimeError("MAWB input not found on Breakbulk page")
    driver.execute_script("""
        arguments[0].focus();
        arguments[0].value = arguments[1];
        arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
        arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
    """, box, mawb_no)
    time.sleep(0.3)
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
                            pre_mismatches.append(f"FRTRV UFS Amt ({wb_val}) != Excel {target_frt_cell} ({expected_frtrv})")
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"FRTRV ${wb_val}", "corr": f"FRTRV ${expected_frtrv}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
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
                            pre_mismatches.append(f"FSCRV UFS Amt ({wb_val}) != Excel {target_fsc_cell} ({expected_fscrv})")
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"FSCRV ${wb_val}", "corr": f"FSCRV ${expected_fscrv}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
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
                                pre_mismatches.append(f"MAGNERV UFS Amt ({wb_val}) != Excel I24 ({cand_i}) or J24 ({cand_j})")
                                ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"MAGNERV ${wb_val}", "corr": f"MAGNERV ${cand_i}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
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
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"HANDRV ${wb_val}", "corr": f"HANDRV ${expected_handrv}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
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
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"FCFAD ${wb_val}", "corr": f"FCFAD ${expected_fcfad}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                    else:
                        post_mismatches.append("FCFAD missing")
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": "FCFAD charge code was not generated after save", "corr": "", "open_date": datetime.now().strftime("%m-%d %H:%M")})

                    if "HANDRV" in post_map and not ctx.is_amount_equal(post_map["HANDRV"]["wb_amt"], expected_handrv):
                        post_mismatches.append("HANDRV final mismatch")
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"HANDRV ${post_map['HANDRV']['wb_amt']}", "corr": f"HANDRV ${expected_handrv}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                    if "FRTRV" in post_map and not ctx.is_amount_equal(post_map["FRTRV"]["wb_amt"], expected_frtrv):
                        post_mismatches.append("FRTRV final mismatch")
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"FRTRV ${post_map['FRTRV']['wb_amt']}", "corr": f"FRTRV ${expected_frtrv}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                    if "FSCRV" in post_map and not ctx.is_amount_equal(post_map["FSCRV"]["wb_amt"], expected_fscrv):
                        post_mismatches.append("FSCRV final mismatch")
                        ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"FSCRV ${post_map['FSCRV']['wb_amt']}", "corr": f"FSCRV ${expected_fscrv}", "open_date": datetime.now().strftime("%m-%d %H:%M")})
                    if is_pvg and has_magnerv_initial and "MAGNERV" in post_map:
                        mag_w = post_map["MAGNERV"]["wb_amt"]
                        if not (ctx.is_amount_equal(mag_w, excel_calc["MAG1"]) or ctx.is_amount_equal(mag_w, excel_calc["MAG2"])):
                            post_mismatches.append("MAGNERV final mismatch")
                            ctx.add_irr({"pup_date": pup_date_text, "origin": current_origin, "mawb": target_mawb_no, "hawb": target_hawb_no, "desc": f"MAGNERV ${mag_w}", "corr": f"MAGNERV ${excel_calc['MAG1']}", "open_date": datetime.now().strftime("%m-%d %H:%M")})

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
