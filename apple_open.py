import os
import re
import time
import ctypes
import getpass
import traceback
import requests
import subprocess
import gc
import threading
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import openpyxl
from openpyxl.styles import Font, Alignment
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# ==========================================
# UFS 설정 및 API URL (main search test 로직 일치)
# ==========================================
LOGIN_URL = "https://jp.ufsplus.kwe.com/web/"
BREAKBULK_URL = "https://jp.ufsplus.kwe.com/ufs/import/breakbulk"

URL_INDEX_SEARCH = "https://jp.ufsplus.kwe.com/proxy/secure/index/txn/search"
URL_GLOBAL_SEARCH = "https://jp.ufsplus.kwe.com/portal-proxy/secure/api/global/search/"

REQUEST_DELAY = 0.3
TIMEOUT = 20
EXCEL_FILE_NAME = "APPLE RATE DATA.xlsx"


# ==========================================
# 좀비 프로세스 및 브라우저 정리 함수
# ==========================================
def cleanup_processes(driver=None):
    if driver:
        try:
            driver.quit()
        except Exception:
            pass

    try:
        gc.collect()
    except Exception:
        pass

    try:
        subprocess.run(
            "taskkill /f /im chromedriver.exe /t",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        pass

# --- [Chrome 옵션 빌더] ---
def _build_chrome_options() -> Options:
    """
    헤드리스 모드에서 Okta / Microsoft OAuth 봇 탐지 및 렌더링 스로틀링을 우회하기 위한 Chrome 옵션 반환.
    - User-Agent와 Client Hints(Sec-CH-UA)는 실제 설치된 Chrome 버전에 맞추기 위해
      드라이버 생성 직후 _apply_headless_stealth(driver)에서 CDP로 일괄 보정한다.
    """
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    # 봇 탐지 우회: 자동화 제어 플래그 제거
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Windows 헤드리스 + 화면 밖 위치(-2400,-2400) 조합 시 렌더링/JS가 멈추는 스로틀링 방지
    options.add_argument("--disable-features=CalculateNativeWinOcclusion")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-hang-monitor")

    # 팝업 차단 해제 / 로케일 / 표준 데스크톱 해상도 설정
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-infobars")
    options.add_argument("--lang=ko-KR")
    options.add_experimental_option("prefs", {
        "intl.accept_languages": "ko-KR,ko,en-US,en",
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    })
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--window-position=-2400,-2400")
    return options


def _apply_headless_stealth(driver):
    """
    실제 설치된 Chrome 버전에 맞춰 User-Agent와 Sec-CH-UA Client Hints를 일치시키고,
    헤드리스 탐지용 JS 속성을 보정한다.
    """
    try:
        browser_ver = (driver.capabilities or {}).get("browserVersion", "")
        major_ver = browser_ver.split(".")[0] if browser_ver else "136"
        full_ver = browser_ver if browser_ver else f"{major_ver}.0.0.0"

        raw_ua = driver.execute_script("return navigator.userAgent;") or ""
        clean_ua = raw_ua.replace("HeadlessChrome/", "Chrome/").replace("HeadlessChrome", "Chrome")
        if not clean_ua:
            clean_ua = (
                f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{major_ver}.0.0.0 Safari/537.36"
            )

        driver.execute_cdp_cmd("Network.setUserAgentOverride", {
            "userAgent": clean_ua,
            "acceptLanguage": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "platform": "Win32",
            "userAgentMetadata": {
                "brands": [
                    {"brand": "Chromium", "version": major_ver},
                    {"brand": "Google Chrome", "version": major_ver},
                    {"brand": "Not_A Brand", "version": "24"},
                ],
                "fullVersionList": [
                    {"brand": "Chromium", "version": full_ver},
                    {"brand": "Google Chrome", "version": full_ver},
                    {"brand": "Not_A Brand", "version": "24.0.0.0"},
                ],
                "fullVersion": full_ver,
                "platform": "Windows",
                "platformVersion": "10.0.0",
                "architecture": "x86",
                "model": "",
                "mobile": False,
                "bitness": "64",
                "wow64": False,
            },
        })
    except Exception:
        pass

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                    try {
                        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    } catch (e) {}

                    try {
                        if (!navigator.languages || navigator.languages.length === 0) {
                            Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
                        }
                    } catch (e) {}

                    try {
                        if (!navigator.plugins || navigator.plugins.length === 0) {
                            var fakePlugins = [
                                { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                                { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                                { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }
                            ];
                            Object.defineProperty(navigator, 'plugins', { get: () => fakePlugins });
                        }
                    } catch (e) {}

                    try {
                        if (!window.chrome) { window.chrome = {}; }
                        if (!window.chrome.runtime) { window.chrome.runtime = {}; }
                    } catch (e) {}

                    try {
                        if (window.outerHeight === 0 || window.outerWidth === 0) {
                            Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth || 1920 });
                            Object.defineProperty(window, 'outerHeight', { get: () => (window.innerHeight || 1080) + 85 });
                        }
                    } catch (e) {}
                """
            }
        )
    except Exception:
        pass

# ==========================================
# [v4.0 이식] UFS & Microsoft OAuth 자동 로그인 로직
# ==========================================
def _handle_ms_account_selection(driver, email, timeout=15):
    """MS OAuth 계정 선택 창에서 이메일과 일치하는 계정 타일을 클릭한다."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "small[data-bind='text: session.unsafe_displayName']"))
        )
        accounts = driver.find_elements(By.CSS_SELECTOR, "small[data-bind='text: session.unsafe_displayName']")
        for acc in accounts:
            if acc.text.strip().lower() == email.strip().lower():
                try:
                    tile = acc.find_element(By.XPATH, "./ancestor::div[@role='button'][1]")
                except Exception:
                    tile = acc
                driver.execute_script("arguments[0].click();", tile)
                return True
        return False
    except Exception:
        return False


def _handle_ms_password_page(driver, ms_pw, timeout=20):
    """MS OAuth 비밀번호 입력 페이지(#i0118)에서 비밀번호를 입력하고 로그인 버튼을 클릭한다."""
    try:
        pw_field = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.ID, "i0118")))
        driver.execute_script("arguments[0].click();", pw_field)
        time.sleep(0.5)
        pw_field.clear()
        pw_field.send_keys(ms_pw)
        time.sleep(0.5)

        submit_btn = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.ID, "idSIButton9")))
        driver.execute_script("arguments[0].click();", submit_btn)
        return True
    except Exception:
        return False


def _find_sms_element(driver):
    """SMS 2FA 선택 버튼과 전화번호 텍스트를 안정적으로 탐색."""
    SMS_KEYWORDS_KO = ['문자 메시지', '문자메시지', '문자', 'SMS']
    SMS_KEYWORDS_EN = ['text message', 'sms', 'text to']

    def _is_sms_text(txt):
        t = txt.strip()
        for kw in SMS_KEYWORDS_KO:
            if kw in t:
                return True
        tl = t.lower()
        for kw in SMS_KEYWORDS_EN:
            if kw in tl:
                return True
        return False

    def _get_phone(btn):
        try:
            inner = btn.find_element(By.CSS_SELECTOR, 'div[data-bind="text: display"]')
            return inner.text.strip()
        except Exception:
            return btn.text.strip()

    # 1순위: data-value 기반 매칭
    try:
        els = driver.find_elements(By.CSS_SELECTOR, 'div[role="button"][data-value="OneWaySMS"]')
        if els:
            return els[0], _get_phone(els[0])
        els = driver.find_elements(By.CSS_SELECTOR, 'div[role="button"][data-value*="SMS"]')
        if els:
            return els[0], _get_phone(els[0])
    except Exception:
        pass

    # 2순위: Knockout 텍스트 바인딩 및 내부 텍스트 검증
    try:
        divs = driver.find_elements(By.CSS_SELECTOR, 'div[data-bind="text: display"]')
        for d in divs:
            if _is_sms_text(d.text):
                try:
                    btn = d.find_element(By.XPATH, './ancestor::div[@role="button"][1]')
                    return btn, d.text.strip()
                except Exception:
                    return d, d.text.strip()
    except Exception:
        pass

    # 3순위: 상위 role="button" 전체 탐색 및 aria-label 검증
    try:
        all_btns = driver.find_elements(By.CSS_SELECTOR, '[role="button"]')
        for btn in all_btns:
            aria = btn.get_attribute('aria-label') or ''
            if _is_sms_text(aria) or _is_sms_text(btn.text):
                return btn, (aria or btn.text).strip()
    except Exception:
        pass

    return None, ""


def _robust_click(driver, el):
    """KnockoutJS 바인딩 스크립트 강제 트리거를 포함한 초강력 클릭 함수."""
    try:
        driver.execute_script("""
            var el = arguments[0];
            try {
                var data = ko.dataFor(el);
                var ctx  = ko.contextFor(el);
                var target = ctx?.$parent || ctx?.$root;
                if (target) {
                    var handler = target.proof_onClick || target.clickTile || target.submitTile;
                    if (typeof handler === 'function') {
                        handler.call(target, data, {type: 'click'});
                        return;
                    }
                }
            } catch(e) {}
        """, el)
        time.sleep(0.8)
    except Exception:
        pass

    try:
        el.click()
        time.sleep(0.5)
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", el)
            time.sleep(0.5)
        except Exception:
            pass


def _is_ms_2fa_page(driver):
    return bool(
        driver.find_elements(By.ID, "idDiv_SAOTCS_Proofs")
        or driver.find_elements(By.ID, "idDiv_SAOTCS_Title")
        or driver.find_elements(By.CSS_SELECTOR, 'div[data-bind*="otc-request-view"]')
    )


def _handle_ms_skip_mfa_registration(driver):
    """
    MS 업데이트로 OTP 인증 직후 나타나는
    '본인 여부를 확인하는 다른 방법을 설정하는 데 도움을 드리겠습니다.' 화면에서
    '나중에' 링크(#skipMfaRegistrationLink)를 눌러 건너뛴다.
    """
    try:
        links = driver.find_elements(By.ID, "skipMfaRegistrationLink")
        if not links:
            return False
        link = links[0]
        href = (link.get_attribute("href") or "").strip()
        try:
            driver.execute_script("arguments[0].click();", link)
        except Exception:
            driver.get(href or "https://login.microsoftonline.com/common/resume?skipmfaregistration=1")
        return True
    except Exception:
        return False


def _handle_ms_2fa_sms(driver, otp_ui_callback, timeout=20, status_cb=None):
    """MS 2FA SMS 인증 처리"""
    def _st(m):
        if status_cb:
            try:
                status_cb(m)
            except Exception:
                pass
    try:
        _st("Looking for SMS option...")
        sms_btn = None
        phone_text = ""
        deadline = time.time() + timeout
        more_proofs_clicks = 0

        while time.time() < deadline:
            sms_btn, phone_text = _find_sms_element(driver)
            if sms_btn:
                break

            try:
                more_links = driver.find_elements(By.ID, "idA_SAOTCS_ShowMoreProofs")
                if more_links and more_links[0].is_displayed() and more_proofs_clicks < 3:
                    more_proofs_clicks += 1
                    _robust_click(driver, more_links[0])
                    time.sleep(1)
                    continue
            except Exception:
                pass

            time.sleep(0.5)

        if not sms_btn:
            return False

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sms_btn)
        time.sleep(0.5)
        _st("Requesting SMS code...")
        _robust_click(driver, sms_btn)

        try:
            WebDriverWait(driver, 15).until(
                lambda d: bool(d.find_elements(By.ID, "idTxtBx_SAOTCC_OTC"))
            )
        except Exception:
            if not driver.find_elements(By.ID, "idTxtBx_SAOTCC_OTC"):
                return False

        otp_event = threading.Event()
        otp_container = [None]
        _st("Waiting for OTP input...")
        otp_ui_callback(phone_text, otp_event, otp_container)

        if not otp_event.wait(120):
            return False

        _st("Submitting OTP...")
        otp = otp_container[0]
        if not otp:
            return False

        otp_field = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "idTxtBx_SAOTCC_OTC")))
        driver.execute_script("arguments[0].click();", otp_field)
        time.sleep(0.3)
        otp_field.clear()
        otp_field.send_keys(otp)
        time.sleep(0.3)

        submit_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "idSubmit_SAOTCC_Continue")))
        _robust_click(driver, submit_btn)
        return True
    except Exception:
        return False


def _resolve_ms_oauth(driver, email, ms_pw, otp_ui_callback=None, status_cb=None):
    """루프 구조를 통해 상호작용 단계 변화에 유연하게 대응하는 마스터 핸들러."""
    if "login.microsoftonline.com" not in driver.current_url:
        return

    def _st(m):
        if status_cb:
            try:
                status_cb(m)
            except Exception:
                pass

    def _is_landing(d):
        return "jp.ufsplus.kwe.com/web/secure/landing/" in d.current_url

    _st("Signing in with Microsoft...")
    start_time = time.time()
    while time.time() - start_time < 60:
        if _is_landing(driver):
            return

        # 1) 계정 선택 화면인 경우
        if driver.find_elements(By.CSS_SELECTOR, "small[data-bind='text: session.unsafe_displayName']"):
            _st("Selecting account...")
            _handle_ms_account_selection(driver, email)
            time.sleep(1.5)
            continue

        # 2) 비밀번호 입력 화면인 경우
        if driver.find_elements(By.ID, "i0118"):
            _st("Entering password...")
            _handle_ms_password_page(driver, ms_pw)
            time.sleep(1.5)
            continue

        # 3) 2FA SMS 코드 발송 선택 화면인 경우
        if _is_ms_2fa_page(driver) and otp_ui_callback:
            _st("Preparing SMS verification...")
            _handle_ms_2fa_sms(driver, otp_ui_callback, status_cb=status_cb)
            time.sleep(1.5)
            continue

        # 4) MFA 추가 등록 안내 화면인 경우 → '나중에' 클릭
        if _handle_ms_skip_mfa_registration(driver):
            _st("Skipping MFA setup prompt...")
            time.sleep(1.5)
            continue

        # 5) KMSI (로그인 상태 유지) 화면인 경우
        kmsi_btns = driver.find_elements(By.ID, "idSIButton9")
        if kmsi_btns:
            try:
                if "kmsi" in driver.current_url or "Keep me signed in" in driver.page_source or "상태를 유지" in driver.page_source:
                    _st("Confirming stay signed in...")
                    driver.execute_script("arguments[0].click();", kmsi_btns[0])
                    time.sleep(1.5)
                    continue
            except Exception:
                pass

        time.sleep(0.5)


def selenium_do_login(driver, wait, email, ms_pw="", otp_ui_callback=None, status_cb=None):
    """자동화 매크로에서 공통으로 사용하는 v4.0 기반 로그인 흐름 (헤드리스 안정화 및 진단 강화)"""
    def _st(m):
        if status_cb:
            try:
                status_cb(m)
            except Exception:
                pass

    _st("Connecting to UFS+ Login page...")
    driver.get(LOGIN_URL)
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(1)

    login_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.login-button-right")))
    base_handles = set(driver.window_handles)
    try:
        login_btn.click()
    except Exception:
        driver.execute_script("arguments[0].click();", login_btn)

    _st("Entering email...")
    email_field = None
    ms_redirected = False
    deadline = time.time() + 25
    reclicked = False

    while time.time() < deadline:
        # 1) 새 창으로 열린 경우 전환
        cur_handles = driver.window_handles
        if len(cur_handles) > len(base_handles):
            new_win = [w for w in cur_handles if w not in base_handles][0]
            driver.switch_to.window(new_win)

        cur_url = driver.current_url

        # 2) 이미 랜딩 페이지로 진입한 경우
        if "jp.ufsplus.kwe.com/web/secure/landing/" in cur_url:
            return

        # 3) Okta 이메일 입력칸 확인
        found_id = driver.find_elements(By.NAME, "identifier")
        if found_id:
            email_field = found_id[0]
            break

        # 4) Okta를 거치지 않고 바로 MS OAuth로 넘어간 경우
        if "login.microsoftonline.com" in cur_url:
            ms_redirected = True
            break

        # 5) 3초가 지나도 초기 로그인 URL에 머물러 있으면 JS 클릭 1회 재시도
        if not reclicked and (25 - (deadline - time.time())) >= 3.0 and cur_url.rstrip("/") == LOGIN_URL.rstrip("/"):
            reclicked = True
            try:
                btns = driver.find_elements(By.CSS_SELECTOR, "button.login-button-right")
                if btns:
                    driver.execute_script("arguments[0].click();", btns[0])
            except Exception:
                pass

        time.sleep(0.5)

    if not ms_redirected and email_field is None:
        print(f"\n[로그인 진단] Timeout 발생 시 현재 URL   : {driver.current_url}")
        print(f"[로그인 진단] Timeout 발생 시 페이지 Title : {driver.title}")
        print(f"[로그인 진단] 열린 창 개수               : {len(driver.window_handles)}")
        raise RuntimeError(f"LOGIN_TIMEOUT_AT_EMAIL: url={driver.current_url}, title={driver.title}")

    if not ms_redirected:
        email_field.clear()
        email_field.send_keys(email)

        _st("Submitting email...")
        next_btn = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "input.button.button-primary[value='다음']")))
        next_btn.click()
        time.sleep(3)

    if "login.microsoftonline.com" in driver.current_url:
        # MS 이메일 입력 화면(#i0116)이 뜬 경우 처리
        try:
            ms_email_fields = driver.find_elements(By.ID, "i0116")
            if ms_email_fields and ms_email_fields[0].is_displayed():
                ms_email_fields[0].clear()
                ms_email_fields[0].send_keys(email)
                time.sleep(0.3)
                next_ms_btns = driver.find_elements(By.ID, "idSIButton9")
                if next_ms_btns:
                    driver.execute_script("arguments[0].click();", next_ms_btns[0])
                    time.sleep(1.5)
        except Exception:
            pass

        _resolve_ms_oauth(driver, email, ms_pw, otp_ui_callback=otp_ui_callback, status_cb=status_cb)

    _st("Verifying landing page...")
    try:
        WebDriverWait(driver, 35).until(
            lambda d: "jp.ufsplus.kwe.com/web/secure/landing/" in d.current_url)
    except Exception:
        raise RuntimeError("LOGIN_FAILED: login did not complete (OTP/auth required or incorrect credentials)")

# ==========================================
# Waybill 조회 함수 (main search test 로직 그대로 적용)
# ==========================================
def search_waybill(session, wb_no, wb_type="hawb"):
    wb_type = wb_type.lower()

    if wb_type == "mawb":
        api_url = URL_INDEX_SEARCH
        payload = {
            "index": "mwbs",
            "andAttrs": [{"attr": "wbNo", "op": "ct", "value1": wb_no}],
            "orAttrs": [],
            "sortAttr": "execDate",
            "sortOrder": "DESC",
            "impExpInd": "I",
        }
        headers = {
            "Referer": "https://jp.ufsplus.kwe.com/ufs/import/carrier-waybill"
        }
        label = "MAWB (메뉴 조회)"

    elif wb_type == "main":
        api_url = URL_GLOBAL_SEARCH
        payload = {"wbNo": wb_no}
        headers = {
            "Referer": "https://jp.ufsplus.kwe.com/web/secure/landing/",
            "app": "TED",
            "Content-Type": "application/json;charset=UTF-8",
        }
        label = "메인 화면 (Global Search)"

    else:
        api_url = URL_INDEX_SEARCH
        payload = {
            "index": "hwbs",
            "andAttrs": [{"attr": "wbNo", "op": "ct", "value1": wb_no}],
            "orAttrs": [],
            "sortAttr": "execDate",
            "sortOrder": "DESC",
            "impExpInd": "I",
        }
        headers = {
            "Referer": "https://jp.ufsplus.kwe.com/ufs/import/customer-waybill"
        }
        label = "HAWB (메뉴 조회)"

    try:
        start_time = time.perf_counter()
        response = session.post(api_url, json=payload, headers=headers, timeout=TIMEOUT)
        elapsed = time.perf_counter() - start_time

        if response.status_code != 200:
            print(f"[실패] 조회 번호: {wb_no} | HTTP 상태 코드: {response.status_code}")
            if response.status_code == 401:
                print("[인증 오류] 인증이 만료되었거나 쿠키 값이 유효하지 않습니다.")
            return None

        data = response.json()

        print(f"\n{'-' * 50}")
        print(f"조회 유형: {label} | 번호: {wb_no} | 소요 시간: {elapsed:.3f}초")

        if isinstance(data, list):
            results = data
        elif isinstance(data, dict):
            results = data.get("results", data.get("result", data.get("data", [])))
            if isinstance(results, dict):
                results = [results]
        else:
            results = []

        print(f"조회 건수: {len(results) if isinstance(results, list) else 1}건")
        return data

    except Exception as e:
        print(f"[실패] 조회 번호: {wb_no} - 예외 오류가 발생했습니다: {e}")
        return None


# ==========================================
# Service Type (AS/AD) 추출 및 판별 헬퍼 함수
# 소/대문자, 띄어쓰기, 특수문자, 오타('serivce') 무관하게 판별
# ==========================================
def extract_service_type_from_text(raw_text):
    if not raw_text:
        return None, None

    raw_str = str(raw_text).strip()
    if not raw_str:
        return None, None

    # 1) 특수문자 및 공백 제거한 영문/숫자 대문자 문자열
    clean = re.sub(r'[^A-Z0-9]', '', raw_str.upper())

    # 2) 서비스 접두사 키워드 (SERVICE, SERIVCE[오타], SRVICE, SERVC, SVC, SRV) 뒤에 오는 문자열 검사
    m_after = re.search(r'(?:SERVICE|SERIVCE|SRVICE|SERVC|SVC|SRV)(.*)', clean)
    if m_after:
        rest = m_after.group(1)
        if any(k in rest for k in ["AIR", "AP", "AS"]):
            return "AS 건", "AS"
        elif "AD" in rest:
            return "AD 건", "AD"

    # 3) 서비스 접두사 키워드 앞에 오는 문자열 검사 (예: "AS SERVICE", "AD_SERIVCE")
    m_pre = re.search(r'(.*?)(?:SERVICE|SERIVCE|SRVICE|SERVC|SVC|SRV)', clean)
    if m_pre:
        pre = m_pre.group(1)
        if any(k in pre for k in ["AIR", "AP", "AS"]):
            return "AS 건", "AS"
        elif "AD" in pre:
            return "AD 건", "AD"

    # 4) 원본 대문자 문자열 기반 검사 (공백/콜론 등 포함된 경우)
    upper_raw = raw_str.upper()
    if any(w in upper_raw for w in ["SERVICE", "SERIVCE", "SVC"]):
        if any(k in upper_raw for k in ["AIR", "AP", "AS"]):
            if "AD" in upper_raw and not any(k in upper_raw for k in ["AIR", "AP", "AS"]):
                return "AD 건", "AD"
            return "AS 건", "AS"
        elif "AD" in upper_raw:
            return "AD 건", "AD"

    return None, None


# ==========================================
# HAWB References 화면 UI에서 Milestone 'PUP' 행의 PUP DATE 추출 및 검증 함수
# ==========================================
def check_pup_date_from_ui(driver, hawb_no=""):
    print(f"\n[PUP DATE 검증 시작] HAWB ({hawb_no}) References 화면 Milestone 'PUP' 탐색 중...")

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
                                unique_parsed_dates[(p_month, p_day)] = f"{p_month}월 {p_day}일"
            except Exception:
                continue

    except Exception as e:
        print(f" [오류] 화면에서 Milestone 탐색 중 예외 발생: {e}")

    recognized_date_str = ""
    parsed_month = None
    parsed_day = None
    status = "ok"

    if len(unique_parsed_dates) == 0:
        status = "omitted"
        print(" ※ [PUP DATE 인식 실패] Milestone 'PUP' 또는 날짜 정보를 확인하지 못했습니다. (PUP DATE omitted)")
    elif len(unique_parsed_dates) > 1:
        status = "conflicting"
        conflicting_list = list(unique_parsed_dates.values())
        recognized_date_str = ", ".join(conflicting_list)
        print(f" ※ [PUP DATE 인식 실패] 서로 다른 날짜가 2개 이상 감지되었습니다 ({recognized_date_str}). (Conflicting PUP DATE)")
    else:
        (parsed_month, parsed_day), recognized_date_str = list(unique_parsed_dates.items())[0]
        print(f" [PUP DATE 인식 성공] {recognized_date_str}")

    print("-" * 55)
    print(f" ▶ UI 화면 인식 PUP Date 문자열 목록 : {found_pup_date_strings}")
    print(f" ▶ 판정 상태                        : {status}")
    print("-" * 55)

    return recognized_date_str, parsed_month, parsed_day, status

# 하위 호환용 alias
check_pup_date_from_hawb = check_pup_date_from_ui


# ==========================================
# 모달 OK 버튼 클릭 헬퍼 함수
# ==========================================
def click_modal_ok(driver, msg_el=None):
    try:
        if msg_el:
            btns = msg_el.find_elements(
                By.XPATH,
                "./ancestor::div[contains(@class, 'modal-content') or contains(@class, 'modal') or contains(@class, 'k-window') or contains(@class, 'dialog') or contains(@class, 'kwe-alert')]//button"
            )
            for b in btns:
                b_text = b.text.strip().upper()
                if ("OK" in b_text or "확인" in b_text) and b.is_displayed():
                    driver.execute_script("arguments[0].click();", b)
                    return True
    except Exception:
        pass

    btns = driver.find_elements(
        By.XPATH,
        "//button[contains(translate(text(), 'ok', 'OK'), 'OK') or contains(text(), '확인') or @value='OK']"
    )
    for b in btns:
        try:
            if b.is_displayed():
                driver.execute_script("arguments[0].click();", b)
                return True
        except Exception:
            continue
    return False


# ==========================================
# 지정되지 않은 오류 창 감지 -> 문구 수집 및 OK 클릭 헬퍼 함수
# ==========================================
def handle_unexpected_error_modal(driver, ignore_keywords=None):
    """
    OBDORG milestone to proceed 오류와 같은 형식의 지정되지 않은 오류 창이 떴을 때
    오류 문구를 수집하고 OK 버튼을 누른 뒤 해당 오류 문구를 반환한다.
    감지된 오류 창이 없으면 None을 반환한다.
    """
    ignore_keywords = ignore_keywords or []
    msg_contents = driver.find_elements(By.CSS_SELECTOR, "div.msg-content, div.dialog-body, div.k-window-content")

    for mel in msg_contents:
        try:
            if not mel.is_displayed():
                continue

            txt = mel.text.strip()
            if not txt:
                txt = (driver.execute_script("return arguments[0].textContent;", mel) or "").strip()
            if not txt:
                continue

            if any(kw in txt for kw in ignore_keywords):
                continue

            modal_btns = mel.find_elements(
                By.XPATH,
                "./ancestor::div[contains(@class, 'modal-content') or contains(@class, 'modal') or contains(@class, 'k-window') or contains(@class, 'dialog') or contains(@class, 'kwe-alert')]//button"
            )
            has_ok_btn = any(
                b.is_displayed() and (
                    "OK" in b.text.strip().upper()
                    or "확인" in b.text.strip()
                    or (b.get_attribute("value") or "").strip().upper() == "OK"
                )
                for b in modal_btns
            )
            if not has_ok_btn and "msg-content" not in (mel.get_attribute("class") or ""):
                continue

            print(f" [경고] 지정되지 않은 오류 창 감지: '{txt}'")
            print(" ▶ OK 버튼을 클릭하여 닫고 해당 건을 PASS 처리합니다.")
            click_modal_ok(driver, mel)
            time.sleep(1.5)
            return txt
        except Exception:
            continue

    return None


# ==========================================
# Breakbulk 페이지 진입 및 Unknown Error 재진입 핸들러
# ==========================================
def open_breakbulk_page(driver, wait):
    print("[정보] Breakbulk 화면으로 이동 중입니다.")
    max_attempts = 4

    for attempt in range(1, max_attempts + 1):
        driver.get(BREAKBULK_URL)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(2.5)

        msg_elements = driver.find_elements(By.CSS_SELECTOR, "div.msg-content")
        unknown_error_found = False

        for mel in msg_elements:
            try:
                if mel.is_displayed():
                    msg_text = mel.text.strip()
                    if not msg_text:
                        msg_text = driver.execute_script("return arguments[0].textContent;", mel).strip()

                    if "Unknown Error" in msg_text:
                        print(f" [경고] Unknown Error 발생 감지 ('{msg_text}')")
                        print(f" ▶ OK 버튼을 누르고 Breakbulk 재진입을 시도합니다. (시도 {attempt}/{max_attempts})")
                        time.sleep(0.5)
                        click_modal_ok(driver, mel)
                        time.sleep(1.5)
                        unknown_error_found = True
                        break
                    elif msg_text:
                        print(f"\n[시스템 알림 문구 감지] '{msg_text}'")
                        click_modal_ok(driver, mel)
                        time.sleep(1)
            except Exception:
                continue

        if not unknown_error_found:
            print("[성공] Breakbulk 화면에 정상적으로 진입하였습니다.")
            break
        elif attempt == max_attempts:
            print("[주의] Unknown Error 재시도 한도에 도달하여 현재 화면에서 진행합니다.")


# ==========================================
# Global Search 결과에서 콘솔된 HAWB 번호 추출
# ==========================================
def extract_hawbs_from_data(data, mawb_no):
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


# ==========================================
# HAWB 조회 결과에서 cneeName에 APPLE 포함 여부 확인
# ==========================================
def check_apple_in_hawb_data(data):
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


# ==========================================
# 체크박스 안전 체크 / 체크 해제 헬퍼 함수 (강화형)
# ==========================================
def safe_check(driver, cb_element):
    """헤드리스 환경에서도 정확히 checked 상태로 만드는 함수"""
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", cb_element)
    time.sleep(0.1)
    is_checked = driver.execute_script("return arguments[0].checked;", cb_element)
    if not is_checked:
        try:
            cb_id = cb_element.get_attribute("id")
            label = None
            if cb_id:
                labels = driver.find_elements(By.CSS_SELECTOR, f"label[for='{cb_id}']")
                if labels:
                    label = labels[0]
            if not label:
                labels = cb_element.find_elements(By.XPATH, "./following-sibling::label | ../label")
                if labels:
                    label = labels[0]

            if label:
                try:
                    label.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", label)
            else:
                try:
                    cb_element.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", cb_element)
        except Exception:
            driver.execute_script("arguments[0].click();", cb_element)
        time.sleep(0.2)

        if not driver.execute_script("return arguments[0].checked;", cb_element):
            driver.execute_script("""
                var el = arguments[0];
                el.checked = true;
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('input', { bubbles: true }));
            """, cb_element)


def safe_uncheck(driver, cb_element):
    """헤드리스 환경에서도 정확히 unchecked 상태로 만드는 함수 (중복 클릭 방지)"""
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", cb_element)
    time.sleep(0.1)
    is_checked = driver.execute_script("return arguments[0].checked;", cb_element)
    if is_checked:
        try:
            cb_id = cb_element.get_attribute("id")
            label = None
            if cb_id:
                labels = driver.find_elements(By.CSS_SELECTOR, f"label[for='{cb_id}']")
                if labels:
                    label = labels[0]
            if not label:
                labels = cb_element.find_elements(By.XPATH, "./following-sibling::label | ../label")
                if labels:
                    label = labels[0]

            if label:
                try:
                    label.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", label)
            else:
                try:
                    cb_element.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", cb_element)
        except Exception:
            driver.execute_script("arguments[0].click();", cb_element)
        time.sleep(0.2)

        if driver.execute_script("return arguments[0].checked;", cb_element):
            driver.execute_script("""
                var el = arguments[0];
                el.checked = false;
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('input', { bubbles: true }));
            """, cb_element)


# ==========================================
# Actual Flight Information 정확한 검증 헬퍼 함수
# ==========================================
def check_has_actual_flight_info(actual_grid):
    try:
        class_attr = actual_grid.get_attribute("class") or ""
        if "no-data" in class_attr:
            return False

        row_count = actual_grid.get_attribute("aria-rowcount")
        if row_count == "0":
            return False

        norecords = actual_grid.find_elements(By.CSS_SELECTOR, "tr.k-grid-norecords")
        if norecords:
            return False

        if "No records found" in actual_grid.text:
            return False

        master_rows = actual_grid.find_elements(By.CSS_SELECTOR, "table.k-grid-table tbody tr.k-master-row")
        if not master_rows:
            return False

        for mr in master_rows:
            cells = mr.find_elements(By.TAG_NAME, "td")
            if len(cells) >= 6:
                carrier = cells[4].text.strip()
                flight_no = cells[5].text.strip()
                if carrier or flight_no:
                    return True

        return False
    except Exception:
        return False


# ==========================================
# 엑셀 파일 경로 탐색 헬퍼 함수
# ==========================================
def get_excel_file_path(filename=EXCEL_FILE_NAME):
    candidate1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    if os.path.exists(candidate1):
        return candidate1
    candidate2 = os.path.abspath(filename)
    if os.path.exists(candidate2):
        return candidate2
    return candidate1


# ==========================================
# PUP DATE에 맞는 엑셀 시트명 탐색 함수 (최신 시트부터 역순 탐색)
# ==========================================
def find_matching_excel_sheet(excel_path, pup_month, pup_day):
    if not os.path.exists(excel_path):
        print(f"[오류] 엑셀 파일을 찾을 수 없습니다: {excel_path}")
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
            print(f"[오류] 엑셀 시트 목록을 읽는 중 오류 발생: {e}")
            return None
        finally:
            if wb:
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
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

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


# ==========================================
# 엑셀 운임 계산 함수 (Origin: PVG -> B24 입력 / HAN -> B16 입력 및 공통 로직 처리)
# ==========================================
def calculate_excel_rates(excel_path, sheet_name, chargeable_wt, origin="PVG"):
    abs_path = os.path.abspath(excel_path)
    if not os.path.exists(abs_path):
        print(f"[오류] 엑셀 파일이 존재하지 않습니다: {abs_path}")
        return None

    try:
        wt_val = float(str(chargeable_wt).replace(",", "").strip())
    except Exception as e:
        print(f"[오류] Chargeable Wt 변환 실패 ({chargeable_wt}): {e}")
        return None

    origin_upper = str(origin).upper().strip()
    row = 16 if origin_upper == "HAN" else 24
    input_cell = f"B{row}"

    def _to_float(v):
        return float(v) if v is not None else 0.0

    excel = None
    wb = None

    # 1. Windows Excel COM 연동
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
            print(f"[오류] 엑셀에 '{sheet_name}' 시트가 없습니다.")
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
            "origin": origin_upper,
            "row": row,
            "FRT_AS": d_val,
            "FRT_AD": e_val,
            "FSC": f_val,
            "HAN": h_val,
            "MAG1": mag1_val,
            "MAG2": mag2_val,
            "FCF_AS": fcf_as_val,
            "FCF_AD": fcf_ad_val,
            "FRT_AS_CELL": f"D{row}",
            "FRT_AD_CELL": f"E{row}",
            "FSC_CELL": f"F{row}",
            "HAN_CELL": f"H{row}",
            "FCF_AS_CELL": fcf_as_cell,
            "FCF_AD_CELL": fcf_ad_cell,
            "D24_AS": d_val,
            "E24_AD": e_val,
            "F24_FSC": f_val,
            "H24_HAN": h_val,
            "I24_MAG1": mag1_val,
            "J24_MAG2": mag2_val,
            "K24_FCF_AS": fcf_as_val,
            "L24_FCF_AD": fcf_ad_val,
        }
    except Exception as com_err:
        print(f" [안내] Excel COM 연동 불가 ({com_err}) -> openpyxl fallback 실행")
    finally:
        if wb:
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
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    # 2. openpyxl fallback
    try:
        wb = openpyxl.load_workbook(abs_path, data_only=False)
        if sheet_name not in wb.sheetnames:
            print(f"[오류] openpyxl: '{sheet_name}' 시트가 없습니다.")
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
            "origin": origin_upper,
            "row": row,
            "FRT_AS": d_val,
            "FRT_AD": e_val,
            "FSC": f_val,
            "HAN": h_val,
            "MAG1": mag1_val,
            "MAG2": mag2_val,
            "FCF_AS": fcf_as_val,
            "FCF_AD": fcf_ad_val,
            "FRT_AS_CELL": f"D{row}",
            "FRT_AD_CELL": f"E{row}",
            "FSC_CELL": f"F{row}",
            "HAN_CELL": f"H{row}",
            "FCF_AS_CELL": fcf_as_cell,
            "FCF_AD_CELL": fcf_ad_cell,
            "D24_AS": d_val,
            "E24_AD": e_val,
            "F24_FSC": f_val,
            "H24_HAN": h_val,
            "I24_MAG1": mag1_val,
            "J24_MAG2": mag2_val,
            "K24_FCF_AS": fcf_as_val,
            "L24_FCF_AD": fcf_ad_val,
        }
    except Exception as pyxl_err:
        print(f"[오류] 엑셀 파싱 실패: {pyxl_err}")
        return None


# ==========================================
# 금액 일치 정밀 비교 함수 (소수점 둘째 자리까지 끊어서/반올림하여 비교)
# ==========================================
def is_amount_equal(val1, val2, tol=None):
    """
    UFS 값과 엑셀 값 비교 시 소수점 두 번째 자리까지만 끊어서(반올림하여) 비교한다.
    예: UFS $136.0700 vs 엑셀 $136.065 -> 둘 다 136.07로 판정하여 True
    """
    try:
        if val1 is None or val2 is None:
            return False

        def _to_decimal_2(v):
            s = str(v).replace(",", "").strip()
            s = re.sub(r'[^\d.-]', '', s)
            if not s:
                return None
            f = float(s)
            d = Decimal(f"{f:.8f}")
            return d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        d1 = _to_decimal_2(val1)
        d2 = _to_decimal_2(val2)

        if d1 is None or d2 is None:
            return False
        return d1 == d2
    except Exception:
        return False


# ==========================================
# HAWB Charges 그리드 내 행 파싱 함수
# ==========================================
def parse_hawb_charges_grid(driver):
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
                "code": code_val,
                "desc": desc_val,
                "exp_imp": expimp_val,
                "terms": terms_val,
                "wb_amt": wb_amt_val,
                "wb_curr": wb_curr_val,
                "inv_amt": inv_amt_val,
                "inv_curr": inv_curr_val,
                "act_cost": act_cost_val,
                "cost_curr": cost_curr_val,
                "vendor_id": vendor_val,
                "print": print_val,
                "row_element": crow
            })
        except Exception:
            continue

    return parsed_charges


# ==========================================
# OPEN REPORT 및 IRR REPORT 엑셀 생성 함수 (MAWB 및 ORIGIN 행 병합 적용)
# ==========================================
def export_excel_reports(irr_records, open_records):
    irr_records = irr_records or []
    open_records = open_records or []

    try:
        candidate_dirs = [
            os.path.dirname(os.path.abspath(__file__)),
            os.path.join(os.path.expanduser("~"), "Desktop"),
            os.path.expanduser("~"),
            os.environ.get("TEMP", "C:\\Temp")
        ]
        save_dir = None
        for cd in candidate_dirs:
            if cd and os.path.exists(cd) and os.access(cd, os.W_OK):
                save_dir = cd
                break
        if not save_dir:
            save_dir = os.path.dirname(os.path.abspath(__file__))

        now = datetime.now()
        base_filename = f"OPEN REPORT {now.strftime('%m-%d %H-%M')}"
        filename = f"{base_filename}.xlsx"
        full_path = os.path.join(save_dir, filename)

        counter = 1
        while os.path.exists(full_path) and counter <= 100:
            filename = f"{base_filename} ({counter}).xlsx"
            full_path = os.path.join(save_dir, filename)
            counter += 1

        wb = openpyxl.Workbook()

        aptos_font = Font(name="Aptos")
        aptos_font_header = Font(name="Aptos", bold=True)
        center_align = Alignment(horizontal="center", vertical="center")

        # ------------------------------------------
        # 시트 1: IRR REPORT
        # ------------------------------------------
        ws_irr = wb.active
        ws_irr.title = "IRR REPORT"

        irr_headers = ["PUP DATE", "ORIGIN", "MAWB", "HAWB", "Description", "Correction", "OPEN DATE"]
        ws_irr.append(irr_headers)

        for rec in irr_records:
            ws_irr.append([
                rec.get("pup_date", ""),
                rec.get("origin", ""),
                rec.get("mawb", ""),
                rec.get("hawb", ""),
                rec.get("desc", ""),
                rec.get("corr", ""),
                rec.get("open_date", rec.get("date", ""))
            ])

        for row_idx, row in enumerate(ws_irr.iter_rows(min_row=1, max_row=ws_irr.max_row, min_col=1, max_col=7), start=1):
            for cell in row:
                cell.font = aptos_font_header if row_idx == 1 else aptos_font
                cell.alignment = center_align

        for col in ws_irr.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = col[0].column_letter
            ws_irr.column_dimensions[col_letter].width = max(max_len + 5, 14)

        # ------------------------------------------
        # 시트 2: OPEN REPORT
        # ------------------------------------------
        ws_open = wb.create_sheet(title="OPEN REPORT")

        open_headers = ["PUP DATE", "ORIGIN", "MAWB", "HAWB", "OPEN DATE"]
        ws_open.append(open_headers)

        for rec in open_records:
            ws_open.append([
                rec.get("pup_date", ""),
                rec.get("origin", ""),
                rec.get("mawb", ""),
                rec.get("hawb", ""),
                rec.get("open_date", rec.get("date", ""))
            ])

        for row_idx, row in enumerate(ws_open.iter_rows(min_row=1, max_row=ws_open.max_row, min_col=1, max_col=5), start=1):
            for cell in row:
                cell.font = aptos_font_header if row_idx == 1 else aptos_font
                cell.alignment = center_align

        for col in ws_open.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = col[0].column_letter
            ws_open.column_dimensions[col_letter].width = max(max_len + 5, 14)

        # ------------------------------------------
        # IRR 시트 및 OPEN 시트 동일 MAWB(C열) & ORIGIN(B열) 행 병합
        # ------------------------------------------
        def merge_mawb_and_origin(ws):
            if ws.max_row >= 3:
                start_row = 2
                current_mawb = ws.cell(row=2, column=3).value

                for r in range(3, ws.max_row + 1):
                    val = ws.cell(row=r, column=3).value
                    if val == current_mawb and val is not None and str(val).strip() != "":
                        continue
                    else:
                        if (r - 1) > start_row:
                            ws.merge_cells(start_row=start_row, start_column=3, end_row=r - 1, end_column=3)
                            ws.merge_cells(start_row=start_row, start_column=2, end_row=r - 1, end_column=2)
                        start_row = r
                        current_mawb = val

                if ws.max_row > start_row:
                    ws.merge_cells(start_row=start_row, start_column=3, end_row=ws.max_row, end_column=3)
                    ws.merge_cells(start_row=start_row, start_column=2, end_row=ws.max_row, end_column=2)

        merge_mawb_and_origin(ws_irr)
        merge_mawb_and_origin(ws_open)

        try:
            wb.save(full_path)
        except PermissionError:
            alt_filename = f"{base_filename}_{now.strftime('%S')}.xlsx"
            full_path = os.path.join(save_dir, alt_filename)
            wb.save(full_path)

        print("\n" + "=" * 65)
        print("[OPEN REPORT 엑셀 생성 완료]")
        print(f" ▶ 파일 경로      : {full_path}")
        print(f" ▶ IRR 불일치 건수: {len(irr_records)}건")
        print(f" ▶ OPEN 성공 건수 : {len(open_records)}건")
        print("=" * 65)
        return full_path

    except Exception as e:
        print(f"[오류] OPEN REPORT 엑셀 파일 생성 중 오류 발생: {e}")
        traceback.print_exc()
        return None


# ==========================================
# 메인 실행 함수
# ==========================================
def main():
    print("=" * 65)
    print("           APPLE AUTO OPEN")
    print("=" * 65)

    # 0. 계정 정보 입력 (터미널)
    email = input("UFS / MS 계정 Email을 입력하세요: ").strip()
    if not email:
        print("[오류] Email이 입력되지 않았습니다. 프로그램을 종료합니다.")
        return

    ms_pw = getpass.getpass("MS 계정 비밀번호를 입력하세요 (화면에 표시되지 않음): ").strip()
    if not ms_pw:
        print("[오류] 비밀번호가 입력되지 않았습니다. 프로그램을 종료합니다.")
        return

    while True:
        start_eta_raw = input("조회 시작 ETA를 입력하세요 (YYYY-MM-DD): ").strip()
        try:
            start_eta_dt = datetime.strptime(start_eta_raw, "%Y-%m-%d")
            start_target_date = start_eta_dt.strftime("%d/%m/%Y")
            break
        except ValueError:
            print("[오류] 날짜 형식이 올바르지 않습니다. YYYY-MM-DD 형식으로 다시 입력하세요. (예: 2026-11-12)")

    while True:
        end_eta_raw = input("조회 종료 ETA를 입력하세요 (YYYY-MM-DD): ").strip()
        try:
            end_eta_dt = datetime.strptime(end_eta_raw, "%Y-%m-%d")
            if end_eta_dt < start_eta_dt:
                print("[오류] 종료 ETA는 시작 ETA보다 빠를 수 없습니다. 다시 입력하세요.")
                continue
            end_target_date = end_eta_dt.strftime("%d/%m/%Y")
            break
        except ValueError:
            print("[오류] 날짜 형식이 올바르지 않습니다. YYYY-MM-DD 형식으로 다시 입력하세요. (예: 2026-11-12)")

    add_mawbs_raw = input("작업 대상에 추가할 MAWB를 입력하세요 (쉼표 또는 공백으로 구분, 없으면 Enter): ").strip()
    add_mawbs = []
    if add_mawbs_raw:
        for m in re.split(r'[,\s]+', add_mawbs_raw):
            m_clean = m.strip()
            if m_clean and m_clean not in add_mawbs:
                add_mawbs.append(m_clean)

    exclude_mawbs_raw = input("작업 대상에서 제외할 MAWB를 입력하세요 (쉼표 또는 공백으로 구분, 없으면 Enter): ").strip()
    exclude_mawbs = []
    if exclude_mawbs_raw:
        for m in re.split(r'[,\s]+', exclude_mawbs_raw):
            m_clean = m.strip()
            if m_clean and m_clean not in exclude_mawbs:
                exclude_mawbs.append(m_clean)

    print("\n[정보] 헤드리스 크롬 브라우저를 실행하고 자동 로그인을 시작합니다.")

    irr_records = []
    open_records = []
    driver = None
    report_exported = False

    # 작업 중 건드린 모든 항목을 누락 없이 추적하기 위한 컨텍스트
    current_active_context = {
        "mawb": "",
        "hawb": "",
        "origin": "",
        "pup_date": ""
    }

    def trigger_guaranteed_report():
        nonlocal report_exported
        if report_exported:
            return
        # 작업 중단 시 아직 리포트에 등록되지 않은 진행 중 건이 있다면 IRR REPORT에 등록
        if current_active_context["mawb"]:
            already_in = any(
                r.get("mawb") == current_active_context["mawb"] and r.get("hawb") == current_active_context["hawb"]
                for r in (irr_records + open_records)
            )
            if not already_in:
                irr_records.append({
                    "pup_date": current_active_context.get("pup_date", ""),
                    "origin": current_active_context.get("origin", ""),
                    "mawb": current_active_context.get("mawb", ""),
                    "hawb": current_active_context.get("hawb", ""),
                    "desc": "Process interrupted before completion",
                    "corr": "",
                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                })
        export_excel_reports(irr_records, open_records)
        report_exported = True

    try:
        options = _build_chrome_options()
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

        # 헤드리스 봇 탐지 우회 및 UA / Client Hints / JS 지문 보정 적용
        _apply_headless_stealth(driver)

        wait = WebDriverWait(driver, 25)

        # 터미널용 상태 콜백 및 2FA OTP 콜백
        def terminal_status_cb(msg):
            print(f" ▶ [로그인 상태] {msg}")

        def terminal_otp_callback(phone_text, otp_event, otp_container):
            print("\n" + "=" * 55)
            print(f"[2FA SMS 인증 요청 감지] {phone_text}")
            print("=" * 55)
            otp_val = input("휴대폰으로 수신된 OTP 인증번호를 입력하세요: ").strip()
            otp_container[0] = otp_val
            otp_event.set()

        # 1. v4.0 기반 UFS & MS OAuth 자동 로그인 수행
        selenium_do_login(driver, wait, email, ms_pw=ms_pw,
                          otp_ui_callback=terminal_otp_callback,
                          status_cb=terminal_status_cb)
        print("[성공] UFS+ 및 Microsoft OAuth 자동 로그인이 성공적으로 완료되었습니다.\n")

        # 2. Breakbulk 페이지 이동 (Unknown Error 감지 시 재진입 로직 적용)
        open_breakbulk_page(driver, wait)

        # 3. 검색 조건 설정 및 조회 실행
        print("[정보] 검색 조건을 설정하는 중입니다.")

        date_inputs = wait.until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input.k-textbox[placeholder='dd/MM/yyyy']"))
        )

        # 시작일 입력
        date_inputs[0].click()
        time.sleep(0.2)
        driver.execute_script("""
            arguments[0].value = '';
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """, date_inputs[0])
        time.sleep(0.2)
        for char in start_target_date:
            date_inputs[0].send_keys(char)
            time.sleep(0.05)
        date_inputs[0].send_keys(Keys.TAB)
        time.sleep(0.3)

        # 종료일 입력
        date_inputs[1].click()
        time.sleep(0.2)
        driver.execute_script("""
            arguments[0].value = '';
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """, date_inputs[1])
        time.sleep(0.2)
        for char in end_target_date:
            date_inputs[1].send_keys(char)
            time.sleep(0.05)
        date_inputs[1].send_keys(Keys.TAB)
        time.sleep(0.3)

        # IC 선택
        dropdown_wrap = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "span.k-dropdown-wrap"))
        )
        driver.execute_script("arguments[0].click();", dropdown_wrap)
        time.sleep(0.4)

        active_el = driver.switch_to.active_element
        for char in "IC":
            active_el.send_keys(char)
            time.sleep(0.05)
        active_el.send_keys(Keys.TAB)
        time.sleep(0.4)

        # Search 버튼 실행
        search_btn = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.kwe-btn-primary"))
        )
        driver.execute_script("arguments[0].click();", search_btn)
        print("[성공] 조회 버튼이 실행되었습니다.")

        # 4. 조회 결과 분석 (Arr/Doc 체크박스 둘 다 미체크 + Origin Port가 PVG 또는 HAN)
        print("[정보] 조회 결과 로딩 대기 및 조건 검증을 시작합니다.")
        time.sleep(3)

        rows = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tr.k-master-row")))

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

                if not arr_checked and not doc_checked:
                    mawb_element = row.find_element(By.CSS_SELECTOR, "td:nth-child(6) span.case-cell")
                    mawb_no = mawb_element.text.strip()
                    if mawb_no and mawb_no not in target_origin_mawbs:
                        target_origin_mawbs.append(mawb_no)
                        mawb_origin_map[mawb_no] = origin_port
                        print(f"조건 만족 MAWB ({origin_port}): {mawb_no}")
            except Exception:
                continue

        print(f"\n[성공] 1차 필터링 완료: 총 {len(target_origin_mawbs)}개의 조건 만족 MAWB(PVG/HAN)를 확인하였습니다.")
        print(f"대상 MAWB 목록: {target_origin_mawbs}")

        # 5. Cookie 자동 연동 (헤드리스 환경 자동 처리)
        print("\n" + "=" * 60)
        print("UFS API 연계를 위해 브라우저 세션의 Cookie를 자동으로 연동합니다.")
        print("=" * 60)

        browser_cookies = driver.get_cookies()
        cookie = "; ".join([f"{c['name']}={c['value']}" for c in browser_cookies]).strip()

        if not cookie:
            print("[안내] 브라우저 쿠키 자동 추출 불가 -> 직접 입력을 진행합니다.")
            cookie = getpass.getpass("Cookie 값을 입력하여 주십시오 (화면에 표시되지 않음): ").strip()

        if not cookie:
            print("[오류] 쿠키 값이 유효하지 않습니다. 프로그램을 종료합니다.")
            return

        session = requests.Session()
        for c in browser_cookies:
            session.cookies.set(c['name'], c['value'])

        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://jp.ufsplus.kwe.com",
            "Cookie": cookie,
        })
        print(f"[성공] 세션 쿠키 자동 연동 완료 (총 {len(browser_cookies)}개 쿠키 적용)")

        # 6. MAWB를 3번 기능(Global Search)으로 조회 -> 연계 HAWB를 1번 기능(HAWB 메뉴)으로 조회
        print("\n" + "=" * 60)
        print("연계 HAWB 검증 시작 (단 하나의 HAWB라도 Cnee에 APPLE이 있으면 대상 확정)")
        print("=" * 60)

        final_apple_mawbs = []

        for index, mawb_no in enumerate(target_origin_mawbs, start=1):
            origin_for_mawb = mawb_origin_map.get(mawb_no, "PVG")
            print(f"\n▶ [{index}/{len(target_origin_mawbs)}] MAWB 검증 ({origin_for_mawb}): {mawb_no}")

            main_search_result = search_waybill(session, mawb_no, wb_type="main")
            time.sleep(REQUEST_DELAY)

            if not main_search_result:
                print(f"  [안내] {mawb_no}에 대한 Global Search 결과가 없습니다.")
                continue

            hawb_list = extract_hawbs_from_data(main_search_result, mawb_no)

            if not hawb_list:
                print(f"  [안내] {mawb_no}에 콘솔된 HAWB가 확인되지 않았습니다.")
                continue

            print(f"  [발견] {mawb_no}에 콘솔된 HAWB 목록 ({len(hawb_list)}건): {hawb_list}")

            mawb_has_apple = False
            matched_info = ""

            for h_idx, hwb_no in enumerate(hawb_list, start=1):
                hawb_result = search_waybill(session, hwb_no, wb_type="hawb")
                time.sleep(REQUEST_DELAY)

                is_apple, cnee_name = check_apple_in_hawb_data(hawb_result)
                display_cnee = cnee_name if cnee_name else "(Cnee 정보 없음)"

                if is_apple:
                    print(f"    [{h_idx}/{len(hawb_list)}] [APPLE 일치 발견] HAWB: {hwb_no} | Cnee: '{display_cnee}'")
                    mawb_has_apple = True
                    matched_info = f"HAWB: {hwb_no} (Cnee: {display_cnee})"
                    print(f"    ▶ 콘솔된 HAWB 중 APPLE 건이 확인되어 대상 확정 (다음 MAWB로 이동)")
                    break
                else:
                    print(f"    - [{h_idx}/{len(hawb_list)}] HAWB: {hwb_no} | Cnee: '{display_cnee}' (APPLE 불일치)")

            if mawb_has_apple:
                final_apple_mawbs.append((mawb_no, origin_for_mawb, matched_info))
                print(f"  [최종 타겟 확정] MAWB: {mawb_no} (Origin: {origin_for_mawb})")

        # 7. 최종 결과 출력
        print("\n" + "=" * 60)
        print("                    [최종 결과 리포트]")
        print("=" * 60)
        print(f"1. PVG / HAN 미체크 MAWB 총 건수: {len(target_origin_mawbs)}건")
        print(f"2. APPLE HAWB를 보유한 최종 MAWB 건수: {len(final_apple_mawbs)}건")
        print("-" * 60)
        print("■ cneeName에 APPLE이 포함된 최종 MAWB 리스트:")
        if final_apple_mawbs:
            for i, (mawb, orig, info) in enumerate(final_apple_mawbs, start=1):
                print(f"  [{i}] MAWB: {mawb} ({orig})  →  확인된 {info}")
        else:
            print("  (조건을 만족하는 MAWB가 없습니다.)")

        detected_mawb_list = [item[0] for item in final_apple_mawbs]
        target_mawb_list = [m for m in detected_mawb_list if m not in exclude_mawbs]
        for m in add_mawbs:
            if m not in target_mawb_list:
                target_mawb_list.append(m)

        if add_mawbs:
            print(f"■ 작업 대상에 추가할 MAWB ({len(add_mawbs)}건): {add_mawbs}")
        if exclude_mawbs:
            print(f"■ 작업 대상에서 제외할 MAWB ({len(exclude_mawbs)}건): {exclude_mawbs}")
        print(f"■ 최종 작업 진행 MAWB ({len(target_mawb_list)}건): {target_mawb_list}")

        if not target_mawb_list:
            print("  (최종 작업 진행 대상 MAWB가 없습니다.)")
            trigger_guaranteed_report()
            return
        print("=" * 60)

        # ==========================================
        # 8. 조건 만족 MAWB 후속 작업 진행 (전체 건 순차 처리 / 테스트 모드 지원)
        # ==========================================
        # [테스트 모드] 필요 시 아래 주석을 해제하여 사용
        #target_mawb_list = [
        #    "272-76297970",
        #]

        breakbulk_window = driver.current_window_handle

        for m_idx, target_mawb_no in enumerate(target_mawb_list, start=1):
            print(f"\n{'#' * 75}")
            print(f"[{m_idx}/{len(target_mawb_list)}] 타겟 MAWB 작업 시작: {target_mawb_no}")
            print(f"{'#' * 75}")

            mawb_window = None
            target_mawb_origin = mawb_origin_map.get(target_mawb_no, "")

            try:
                driver.switch_to.window(breakbulk_window)
                time.sleep(1)

                # 타겟 MAWB의 ORIGIN 자동 식별
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

                current_active_context["mawb"] = target_mawb_no
                current_active_context["origin"] = target_mawb_origin
                current_active_context["hawb"] = ""
                current_active_context["pup_date"] = ""

                print(f"\n[작업 진행] 타겟 MAWB ({target_mawb_no}) / ORIGIN: [{target_mawb_origin}] 로 작업을 진행합니다.")

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
                    print(f"[오류] Breakbulk 화면에서 {target_mawb_no} 행을 찾을 수 없습니다.")
                    irr_records.append({
                        "pup_date": "",
                        "origin": target_mawb_origin,
                        "mawb": target_mawb_no,
                        "hawb": "",
                        "desc": "Breakbulk row not found",
                        "corr": "",
                        "open_date": datetime.now().strftime("%m-%d %H:%M")
                    })
                    continue

                # 8-1. Arr / Doc 체크박스 상태 확인 및 필요시 체크/저장
                arr_cb = target_row.find_element(By.CSS_SELECTOR, "input[name='acknowledge_alert']")
                doc_cb = target_row.find_element(By.CSS_SELECTOR, "input[name='docs_received']")
                arr_checked = driver.execute_script("return arguments[0].checked;", arr_cb)
                doc_checked = driver.execute_script("return arguments[0].checked;", doc_cb)

                if not arr_checked or not doc_checked:
                    print("[1/6] Arr 체크박스와 Doc 체크박스를 체크하고 저장합니다.")
                    safe_check(driver, arr_cb)
                    time.sleep(0.2)
                    safe_check(driver, doc_cb)
                    time.sleep(0.3)

                    save_btn = wait.until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'kwe-btn-primary') and (contains(text(), 'Save') or @value='Save')]"))
                    )
                    driver.execute_script("arguments[0].click();", save_btn)
                    print(" ▶ Save 완료 대기 중...")
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
                                print(f" [경고] 오류 감지: '{txt}'")
                                print(" ▶ OK 버튼을 클릭하여 닫습니다.")
                                click_modal_ok(driver, mel)
                                obdorg_detected = True
                                time.sleep(1.5)
                                break
                        except Exception:
                            continue

                    if not obdorg_detected:
                        unexpected_err = handle_unexpected_error_modal(driver, ignore_keywords=["OBDORG milestone to proceed"])
                        if unexpected_err:
                            irr_records.append({
                                "pup_date": "",
                                "origin": target_mawb_origin,
                                "mawb": target_mawb_no,
                                "hawb": "",
                                "desc": unexpected_err,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                            continue

                    # Add OBDORG milestone to proceed 오류 발생 시: OK 닫고 Arr 체크 해제 + Doc 체크 확인 후 다시 Save 시도
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

                        print(" ▶ Add OBDORG 오류 후: Arr 체크박스는 해제하고, Doc 체크박스만 체크 상태로 유지하여 Save를 다시 시도합니다.")
                        safe_uncheck(driver, arr_cb)
                        time.sleep(0.2)
                        safe_check(driver, doc_cb)
                        time.sleep(0.3)

                        save_btn = wait.until(
                            EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'kwe-btn-primary') and (contains(text(), 'Save') or @value='Save')]"))
                        )
                        driver.execute_script("arguments[0].click();", save_btn)
                        print(" ▶ Save 완료 대기 중...")
                        time.sleep(3)

                        try:
                            WebDriverWait(driver, 15).until_not(
                                EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image"))
                            )
                        except Exception:
                            pass

                        unexpected_err = handle_unexpected_error_modal(driver)
                        if unexpected_err:
                            irr_records.append({
                                "pup_date": "",
                                "origin": target_mawb_origin,
                                "mawb": target_mawb_no,
                                "hawb": "",
                                "desc": unexpected_err,
                                "corr": "",
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
                else:
                    print("[1/6] Arr 및 Doc 체크박스가 이미 체크되어 있으므로 저장을 건너뛰고 바로 진행합니다.")

                # 8-2. Carrier Waybill No. 링크 Shift + 왼쪽 클릭으로 새 창 열기
                print(f"[2/6] {target_mawb_no} 링크를 Shift+클릭하여 새 창으로 엽니다.")
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

                print(f" ▶ MAWB 새 창으로 포커스 전환 완료: {driver.title}")

                # 8-3. Actual Flight Information 그리드 탐색 및 확인
                print("[3/6] Actual Flight Information 확인 중...")

                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.flight-grid")))
                time.sleep(2)

                flight_grids = driver.find_elements(By.CSS_SELECTOR, "div.flight-grid")
                actual_grid = flight_grids[1] if len(flight_grids) >= 2 else flight_grids[0]

                has_flight_info = check_has_actual_flight_info(actual_grid)

                if has_flight_info:
                    print(" [PASS] Actual Flight Information이 이미 존재합니다.")
                else:
                    print(" ▶ [확인 완료] Actual Flight Information이 없습니다. 생성을 진행합니다.")

                    arrow_icon = None
                    try:
                        arrow_icon = actual_grid.find_element(By.XPATH, "./preceding::button[1]//span[contains(@class, 'k-i-arrow-s')] | ./preceding::div[1]//span[contains(@class, 'k-i-arrow-s')]")
                    except Exception:
                        pass

                    if not arrow_icon:
                        all_arrows = driver.find_elements(By.XPATH, "//span[contains(@class, 'k-i-arrow-s')]")
                        for arr in all_arrows:
                            if arr.is_displayed():
                                arrow_icon = arr
                                break

                    if not arrow_icon:
                        print("[오류] Create Actuals 메뉴 화살표 아이콘을 찾을 수 없습니다.")
                        irr_records.append({
                            "pup_date": "",
                            "origin": target_mawb_origin,
                            "mawb": target_mawb_no,
                            "hawb": "",
                            "desc": "Create Actuals arrow icon not found",
                            "corr": "",
                            "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        try:
                            driver.close()
                        except Exception:
                            pass
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
                    print(" ▶ Save 버튼 클릭 완료. 저장 대기 중...")

                    time.sleep(3)
                    try:
                        WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                    except Exception:
                        pass

                    unexpected_err = handle_unexpected_error_modal(driver)
                    if unexpected_err:
                        irr_records.append({
                            "pup_date": "",
                            "origin": target_mawb_origin,
                            "mawb": target_mawb_no,
                            "hawb": "",
                            "desc": unexpected_err,
                            "corr": "",
                            "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        try:
                            driver.close()
                        except Exception:
                            pass
                        driver.switch_to.window(breakbulk_window)
                        time.sleep(1.5)
                        continue

                    time.sleep(2)
                    flight_grids_after = driver.find_elements(By.CSS_SELECTOR, "div.flight-grid")
                    actual_grid_after = flight_grids_after[1] if len(flight_grids_after) >= 2 else flight_grids_after[0]
                    if not check_has_actual_flight_info(actual_grid_after):
                        err_msg = handle_unexpected_error_modal(driver) or "Actual Flight Information creation failed (still empty after save)"
                        print(f"[오류] {err_msg}")
                        irr_records.append({
                            "pup_date": "",
                            "origin": target_mawb_origin,
                            "mawb": target_mawb_no,
                            "hawb": "",
                            "desc": err_msg,
                            "corr": "",
                            "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        try:
                            driver.close()
                        except Exception:
                            pass
                        driver.switch_to.window(breakbulk_window)
                        time.sleep(1.5)
                        continue

                    print(" [PASS] Actual Flight Information 생성 및 저장 완료 확인!")

                # 8-4. Charges 탭 클릭 및 데이터 로딩 대기
                print("[4/6] Charges 탭을 클릭하여 메뉴로 이동합니다.")
                charges_tab = wait.until(
                    EC.element_to_be_clickable((By.XPATH, "//div[contains(@id, 'tabStripWizard_') and .//span[contains(text(), 'Charges')]] | //span[contains(@class, 'wz-st-name') and text()='Charges']"))
                )
                driver.execute_script("arguments[0].click();", charges_tab)
                print(" ▶ Charges 탭 클릭 완료. 테이블 로딩 대기 중...")

                time.sleep(3)
                try:
                    WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                except Exception:
                    pass

                # 8-5. Charges 테이블에서 Consignee 및 Import Bill To ID(302154970), Disposition 검사/수정
                print("[5/6] Charges 테이블 내 콘솔된 HAWB 목록, Disposition 및 Import Bill To ID를 검증합니다.")

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
                                print(f" ▶ HAWB {hawb_no}: Disposition에 이미 '{disp_val}' 기입됨 -> 완료 건으로 간주하고 PASS")
                                skipped_completed_count += 1
                                continue

                            cnee_input = r.find_element(By.CSS_SELECTOR, "input[name='consigneeName']")
                            cnee_name = cnee_input.get_attribute("value").strip()

                            shipper_input = r.find_element(By.CSS_SELECTOR, "input[name='shipperName']")
                            shipper_name = shipper_input.get_attribute("value").strip()

                            pcs_input = r.find_element(By.CSS_SELECTOR, "input[name='num_pieces']")
                            num_pcs = pcs_input.get_attribute("value").strip()

                            act_wt_input = r.find_element(By.CSS_SELECTOR, "input[name='total_actual_weight']")
                            act_wt = act_wt_input.get_attribute("value").strip()

                            chg_wt_input = r.find_element(By.CSS_SELECTOR, "input[name='total_chargeable_weight']")
                            chg_wt = chg_wt_input.get_attribute("value").strip()

                            billto_input = r.find_element(By.CSS_SELECTOR, "input[name='import_billto_cc_id']")
                            billto_val = (billto_input.get_attribute("value") or driver.execute_script("return arguments[0].value;", billto_input) or "").strip()

                            if billto_val == "" or billto_val == "302154970":
                                print(f" ▶ HAWB: {hawb_no} | Import Bill To ID: '{billto_val}' [PASS]")
                            else:
                                print(f" [주의] HAWB: {hawb_no} | 다른 Import Bill To ID 감지됨: '{billto_val}'")
                                print(f"   -> 302154970 직접 타이핑(자간 0.1s) 및 TAB 후 로딩 대기")
                                try:
                                    billto_input.click()
                                except Exception:
                                    driver.execute_script("arguments[0].click();", billto_input)
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
                                    WebDriverWait(driver, 5).until_not(
                                        EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image"))
                                    )
                                except Exception:
                                    pass

                                needs_save = True
                                break

                            parsed_hawb_list.append({
                                "hawb_no": hawb_no,
                                "consignee": cnee_name,
                                "shipper": shipper_name,
                                "pieces": num_pcs,
                                "act_weight": act_wt,
                                "chg_weight": chg_wt,
                                "billto_id": billto_val
                            })
                        except Exception as row_ex:
                            current_active_context["hawb"] = hawb_no
                            raise RuntimeError(f"HAWB {hawb_no} Charges row inspection error: {row_ex}")

                    if needs_save:
                        print(" ▶ Import Bill To ID 변경 사항 저장을 위해 Save 버튼을 클릭합니다.")
                        save_btn = wait.until(
                            EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'save-btn') and (contains(text(), 'Save') or @value='Save')]"))
                        )
                        driver.execute_script("arguments[0].click();", save_btn)
                        print(" ▶ Save 대기 중...")
                        time.sleep(3)

                        unexpected_err = handle_unexpected_error_modal(driver)
                        if unexpected_err:
                            irr_records.append({
                                "pup_date": "",
                                "origin": target_mawb_origin,
                                "mawb": target_mawb_no,
                                "hawb": "",
                                "desc": unexpected_err,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                            mawb_billto_save_error = True
                            break

                        try:
                            WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                        except Exception:
                            pass
                        print(" ▶ Save 완료. 다시 Consignee 및 Import Bill To ID 검사를 재시행합니다.\n")
                        continue
                    else:
                        print(" [PASS] 모든 미완료 HAWB의 Import Bill To ID가 정상(공란 또는 302154970)임을 확인하였습니다.")
                        break

                if mawb_billto_save_error:
                    try:
                        driver.close()
                    except Exception:
                        pass
                    driver.switch_to.window(breakbulk_window)
                    time.sleep(1.5)
                    continue

                print("\n" + "=" * 65)
                print(f"       [Charges 화면 내 작업 대상 HAWB 인식 결과 ({len(parsed_hawb_list)}건)]")
                print("=" * 65)

                for idx, item in enumerate(parsed_hawb_list, start=1):
                    print(f"[{idx}] HAWB No.        : {item['hawb_no']}")
                    print(f"    - Consignee      : {item['consignee']}")
                    print(f"    - Shipper        : {item['shipper']}")
                    print(f"    - Pieces / Weight: {item['pieces']} pcs | Act {item['act_weight']} kg | Chg {item['chg_weight']} kg")
                    print("-" * 65)

                # 8-6. Consignee에 APPLE이 들어가는 HAWB 선별
                print("\n[6/6] Consignee에 APPLE이 포함된 HAWB 선별 및 순차적 상세 작업 진행")
                apple_hawbs = [h for h in parsed_hawb_list if "APPLE" in h["consignee"].upper()]

                if not apple_hawbs:
                    if skipped_completed_count == 0:
                        print("[오류] 작업 대상인 APPLE HAWB를 찾지 못했습니다. IRR REPORT에 기록합니다.")
                        irr_records.append({
                            "pup_date": "",
                            "origin": target_mawb_origin,
                            "mawb": target_mawb_no,
                            "hawb": "",
                            "desc": "No APPLE HAWB found in MAWB Charges",
                            "corr": "",
                            "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                    else:
                        print("[안내] 작업 대상인 APPLE HAWB가 없습니다. (모두 완료되었거나 미존재)")
                    try:
                        driver.close()
                    except Exception:
                        pass
                    driver.switch_to.window(breakbulk_window)
                    time.sleep(1.5)
                    continue

                print(f" ▶ 선별된 APPLE HAWB ({len(apple_hawbs)}건): {[h['hawb_no'] for h in apple_hawbs]}")

                # ==========================================
                # [반복 루프] 조건에 맞는 HAWB들을 순차적으로 Shift+클릭하여 처리
                # ==========================================
                for h_index, target_hawb in enumerate(apple_hawbs, start=1):
                    hawb_window = None
                    target_hawb_no = target_hawb["hawb_no"]
                    current_origin = target_mawb_origin
                    is_pvg = (current_origin == "PVG")
                    is_han = (current_origin == "HAN")

                    current_active_context["hawb"] = target_hawb_no
                    current_active_context["origin"] = current_origin
                    current_active_context["pup_date"] = ""

                    try:
                        driver.switch_to.window(mawb_window)
                        time.sleep(1)

                        print(f"\n{'=' * 75}")
                        print(f"[{h_index}/{len(apple_hawbs)}] 대상 HAWB ({target_hawb_no}) | ORIGIN: [{current_origin}] Shift+클릭 진행")
                        print(f"{'=' * 75}")

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

                        print(f" ▶ HAWB 창으로 포커스 전환 완료: {driver.title}")
                        time.sleep(3)
                        try:
                            wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
                        except Exception:
                            pass

                        # [HAWB 작업 1] Disposition 설정 (클릭 -> 0.5초 뒤 kwe 입력 -> 0.3초 뒤 tab 적용)
                        print(f"\n[HAWB {target_hawb_no} 작업 1] Disposition 설정 (kwe -> TAB)")
                        disposition_dropdown = wait.until(
                            EC.element_to_be_clickable((By.XPATH, "//select[@name='disposition']/ancestor::span[contains(@class, 'k-dropdown')]//span[contains(@class, 'k-dropdown-wrap')] | //span[contains(@id, '__disposition') and contains(@class, 'k-dropdown-wrap')]"))
                        )

                        try:
                            disposition_dropdown.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", disposition_dropdown)

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
                            print(f" ▶ Disposition 적용 상태: '{disp_input_text}'")
                        except Exception:
                            pass

                        if "KWE" not in disp_input_text.upper():
                            err_msg = f"Disposition 'KWE' selection failed (Current: '{disp_input_text}')"
                            print(f" [경고] {err_msg} -> 저장을 진행하지 않고 IRR REPORT 기재 후 창을 닫습니다.")
                            irr_records.append({
                                "pup_date": "",
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        # [HAWB 작업 2] 3번 References 탭 클릭 및 화면 UI에서 Milestone 'PUP' 행의 PUP DATE 확인
                        print(f"\n[HAWB {target_hawb_no} 작업 2] 3번 References 탭으로 이동합니다.")
                        ref_tab = wait.until(
                            EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'wz-st-name') and text()='References'] | //div[contains(@id, 'tabStripWizard_') and .//span[contains(text(), 'References')]]"))
                        )
                        driver.execute_script("arguments[0].click();", ref_tab)
                        print(" ▶ References 탭 클릭 완료. 테이블 로딩 대기 중...")

                        time.sleep(2.5)
                        try:
                            WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                        except Exception:
                            pass

                        pup_date_text, pup_month, pup_day, pup_status = check_pup_date_from_ui(driver, target_hawb_no)
                        current_active_context["pup_date"] = pup_date_text

                        if pup_status == "conflicting":
                            print(f" [경고] Conflicting PUP DATE 감지 ({pup_date_text}) -> 저장을 진행하지 않고 IRR REPORT 기재 후 창을 닫습니다.")
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": "Conflicting PUP DATE",
                                "corr": "",
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
                            print(" [경고] PUP DATE omitted 감지 -> 저장을 진행하지 않고 IRR REPORT 기재 후 창을 닫습니다.")
                            irr_records.append({
                                "pup_date": "",
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": "PUP DATE omitted",
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        # [HAWB 작업 3] References 테이블 데이터 분석 -> Service: AS/AP/AIR(AS) 또는 AD 판별
                        print(f"\n[HAWB {target_hawb_no} 작업 3] References 데이터 분석 및 서비스 유형(AS/AD) 판별")
                        time.sleep(2)

                        service_type = "미확인 (Service 정보 없음)"
                        service_code = ""
                        matched_ref_text = ""

                        # 1단계: Kendo Grid JS DataSource 직접 조회 시도
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
                                    s_type, s_code = extract_service_type_from_text(jv)
                                    if s_code:
                                        service_type = s_type
                                        service_code = s_code
                                        matched_ref_text = jv
                                        print(f" ▶ [Kendo DataSource 감지] Service 필드값: '{matched_ref_text}'")
                                        break
                        except Exception:
                            pass

                        # 2단계: DOM에 렌더링된 input[name='ref_num'], input[name='ll_ref_num'] 탐색
                        if not service_code:
                            ref_inputs = driver.find_elements(By.CSS_SELECTOR, "input[name='ref_num'], input[name='ll_ref_num']")
                            for inp in ref_inputs:
                                try:
                                    val = (inp.get_attribute("value") or driver.execute_script("return arguments[0].value;", inp) or "").strip()
                                    s_type, s_code = extract_service_type_from_text(val)
                                    if s_code:
                                        service_type = s_type
                                        service_code = s_code
                                        matched_ref_text = val
                                        break
                                except Exception:
                                    continue

                        # 3단계: 가상 스크롤(Virtual Scrolling) 컨테이너 단계별 스크롤링 탐색
                        if not service_code:
                            try:
                                virtual_containers = driver.find_elements(By.CSS_SELECTOR, "div.k-virtual-content, div.k-grid-content")
                                for v_cont in virtual_containers:
                                    scroll_height = driver.execute_script("return arguments[0].scrollHeight;", v_cont) or 0
                                    client_height = driver.execute_script("return arguments[0].clientHeight;", v_cont) or 0

                                    if scroll_height > client_height:
                                        step = max(int(client_height * 0.7), 200)
                                        for top in range(0, scroll_height + step, step):
                                            driver.execute_script("arguments[0].scrollTop = arguments[1];", v_cont, top)
                                            time.sleep(0.25)

                                            current_inputs = driver.find_elements(By.CSS_SELECTOR, "input[name='ref_num'], input[name='ll_ref_num']")
                                            for inp in current_inputs:
                                                try:
                                                    val = (inp.get_attribute("value") or driver.execute_script("return arguments[0].value;", inp) or "").strip()
                                                    s_type, s_code = extract_service_type_from_text(val)
                                                    if s_code:
                                                        service_type = s_type
                                                        service_code = s_code
                                                        matched_ref_text = val
                                                        break
                                                except Exception:
                                                    continue
                                            if service_code:
                                                break

                                        try:
                                            driver.execute_script("arguments[0].scrollTop = 0;", v_cont)
                                        except Exception:
                                            pass

                                    if service_code:
                                        break
                            except Exception:
                                pass

                        # 4단계: DOM 전체 HTML 소스 기반 fallback 탐색
                        if not service_code:
                            try:
                                page_html = driver.page_source
                                matches = re.findall(r'value=[\'"]([^\'"]*(?:service|serivce|svc)[^\'"]*)[\'"]', page_html, re.IGNORECASE)
                                for m in matches:
                                    s_type, s_code = extract_service_type_from_text(m)
                                    if s_code:
                                        service_type = s_type
                                        service_code = s_code
                                        matched_ref_text = m
                                        break
                            except Exception:
                                pass

                        print("-" * 60)
                        print(f" ▶ HAWB: {target_hawb_no} | Service 필드값: '{matched_ref_text}' | 결과: [{service_type}]")
                        print("-" * 60)

                        if not service_code:
                            print(" [경고] Service Type을 확인할 수 없습니다. (Service Type omitted)")
                            print(" ▶ 저장을 진행하지 않고 IRR REPORT 기재 후 창을 닫습니다.")
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": "Service Type omitted",
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        # [HAWB 작업 4] 4번 Shipment Details 탭 클릭 및 Chargeable Weight 확인
                        print(f"\n[HAWB {target_hawb_no} 작업 4] 4번 Shipment Details 탭으로 이동합니다.")
                        shipment_details_tab = wait.until(
                            EC.element_to_be_clickable((By.XPATH, "//div[contains(@id, 'tabStripWizard_') and .//span[contains(text(), 'Shipment Details')]] | //span[contains(@class, 'wz-st-name') and text()='Shipment Details']"))
                        )
                        driver.execute_script("arguments[0].click();", shipment_details_tab)
                        print(" ▶ Shipment Details 탭 클릭 완료. 로딩 대기 중...")

                        time.sleep(2)
                        try:
                            WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                        except Exception:
                            pass

                        chargeable_wt_input = wait.until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='chargeableWt']"))
                        )
                        chargeable_wt = (chargeable_wt_input.get_attribute("value") or driver.execute_script("return arguments[0].value;", chargeable_wt_input) or "").strip()
                        print(f" [Shipment Details 확인] Chargeable Weight: {chargeable_wt}")

                        # [HAWB 작업 5] 5번 Charges 탭 클릭 및 데이터 로딩 대기
                        print(f"\n[HAWB {target_hawb_no} 작업 5] 5번 Charges 탭으로 이동합니다.")
                        hawb_charges_tab = wait.until(
                            EC.element_to_be_clickable((By.XPATH, "//div[contains(@id, 'tabStripWizard_') and .//span[contains(text(), 'Charges')]] | //span[contains(@class, 'wz-st-name') and text()='Charges']"))
                        )
                        driver.execute_script("arguments[0].click();", hawb_charges_tab)
                        print(" ▶ Charges 탭 클릭 완료. 테이블 로딩 대기 중...")

                        time.sleep(2)
                        try:
                            WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                        except Exception:
                            pass

                        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.charges-grid")))
                        time.sleep(1.5)

                        # 최초 차지 목록 파싱
                        initial_charges = parse_hawb_charges_grid(driver)
                        present_codes = [c["code"] for c in initial_charges]
                        print(f" ▶ 최초 화면 인식 Charge Codes: {present_codes}")

                        has_magnerv_initial = ("MAGNERV" in present_codes) if is_pvg else False

                        required_codes = {"HANDRV", "FRTRV", "FSCRV"}
                        missing_codes = required_codes - set(present_codes)

                        if missing_codes:
                            print(f"\n[경고] {current_origin} 필수 Charge Code 중 누락 발생: {missing_codes}")
                        else:
                            extra_info = f" (최초 MAGNERV 존재 여부: {has_magnerv_initial})" if is_pvg else " (HAN 건: MAGNERV 없음 확인)"
                            print(f"\n[성공] 필수 Charge Code (HANDRV, FRTRV, FSCRV) 정상 확인{extra_info}")

                        # ==========================================
                        # HAWB 작업 6: FRTRV, FSCRV, MAGNERV(PVG만) 엑셀 대조 선검증 (소수점 둘째 자리 기준)
                        # ==========================================
                        print(f"\n[HAWB {target_hawb_no} 작업 6] 엑셀 연동 및 선검증 진행 (Origin: {current_origin})")

                        excel_path = get_excel_file_path(EXCEL_FILE_NAME)
                        print(f" ▶ 엑셀 파일 경로: {excel_path}")

                        if not pup_month or not pup_day:
                            err_msg = f"Invalid PUP DATE information ({pup_date_text})"
                            print(f"[경고] {err_msg} -> 저장을 진행하지 않고 IRR REPORT 기재 후 창을 닫습니다.")
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        matched_sheet = find_matching_excel_sheet(excel_path, pup_month, pup_day)
                        print(f" [최신 엑셀 시트 매칭] PUP DATE '{pup_date_text}' -> 매칭 시트명: '{matched_sheet}'")

                        if not matched_sheet:
                            err_msg = f"No matching Excel sheet found for PUP DATE {pup_date_text}"
                            print(f"[오류] {err_msg} -> 저장을 진행하지 않고 IRR REPORT 기재 후 창을 닫습니다.")
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        excel_calc = calculate_excel_rates(excel_path, matched_sheet, chargeable_wt, origin=current_origin)
                        if not excel_calc:
                            err_msg = f"Failed to calculate Excel rates for sheet '{matched_sheet}'"
                            print(f"[오류] {err_msg} -> 저장을 진행하지 않고 IRR REPORT 기재 후 창을 닫습니다.")
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        row_num = excel_calc["row"]
                        print(f"\n--- [엑셀 B{row_num} 입력 ({chargeable_wt}) 계산 결과 ({current_origin})] ---")
                        print(f"  - {excel_calc['FRT_AS_CELL']} (Service: AS FRTRV)  : {excel_calc['FRT_AS']}")
                        print(f"  - {excel_calc['FRT_AD_CELL']} (Service: AD FRTRV)  : {excel_calc['FRT_AD']}")
                        print(f"  - {excel_calc['FSC_CELL']} (FSCRV)              : {excel_calc['FSC']}")
                        print(f"  - {excel_calc['HAN_CELL']} (HANDRV)             : {excel_calc['HAN']}")
                        if is_pvg:
                            print(f"  - I24 / J24 (MAGNERV 후보) : {excel_calc['MAG1']} / {excel_calc['MAG2']}")
                        print(f"  - {excel_calc['FCF_AS_CELL']} (FCF AS) / {excel_calc['FCF_AD_CELL']}(FCF AD) : {excel_calc['FCF_AS']} / {excel_calc['FCF_AD']}")
                        print("------------------------------------------")

                        expected_frtrv = excel_calc["FRT_AS"] if service_code == "AS" else excel_calc["FRT_AD"]
                        target_frt_cell = excel_calc["FRT_AS_CELL"] if service_code == "AS" else excel_calc["FRT_AD_CELL"]
                        expected_fscrv = excel_calc["FSC"]
                        target_fsc_cell = excel_calc["FSC_CELL"]
                        expected_handrv = excel_calc["HAN"]
                        target_han_cell = excel_calc["HAN_CELL"]

                        initial_charges_map = {c["code"]: c for c in initial_charges}
                        pre_mismatches = []

                        # 1) FRTRV 선검증 (소수점 둘째 자리 기준)
                        if "FRTRV" in initial_charges_map:
                            frt_data = initial_charges_map["FRTRV"]
                            wb_val = frt_data["wb_amt"]
                            inv_val = frt_data["inv_amt"]
                            if not is_amount_equal(wb_val, inv_val):
                                err_msg = f"FRTRV Waybill Amt ({wb_val}) != Invoice Amt ({inv_val})"
                                pre_mismatches.append(err_msg)
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": err_msg,
                                    "corr": "",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                            if not is_amount_equal(wb_val, expected_frtrv):
                                pre_mismatches.append(f"FRTRV UFS Amt ({wb_val}) != Excel {target_frt_cell} ({expected_frtrv})")
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": f"FRTRV ${wb_val}",
                                    "corr": f"FRTRV ${expected_frtrv}",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                        else:
                            err_msg = "FRTRV code missing from initial charges"
                            pre_mismatches.append(err_msg)
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })

                        # 2) FSCRV 선검증 (소수점 둘째 자리 기준)
                        if "FSCRV" in initial_charges_map:
                            fsc_data = initial_charges_map["FSCRV"]
                            wb_val = fsc_data["wb_amt"]
                            inv_val = fsc_data["inv_amt"]
                            if not is_amount_equal(wb_val, inv_val):
                                err_msg = f"FSCRV Waybill Amt ({wb_val}) != Invoice Amt ({inv_val})"
                                pre_mismatches.append(err_msg)
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": err_msg,
                                    "corr": "",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                            if not is_amount_equal(wb_val, expected_fscrv):
                                pre_mismatches.append(f"FSCRV UFS Amt ({wb_val}) != Excel {target_fsc_cell} ({expected_fscrv})")
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": f"FSCRV ${wb_val}",
                                    "corr": f"FSCRV ${expected_fscrv}",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                        else:
                            err_msg = "FSCRV code missing from initial charges"
                            pre_mismatches.append(err_msg)
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })

                        # 3) MAGNERV 선검증 (소수점 둘째 자리 기준)
                        if is_pvg and has_magnerv_initial:
                            if "MAGNERV" in initial_charges_map:
                                mag_data = initial_charges_map["MAGNERV"]
                                wb_val = mag_data["wb_amt"]
                                inv_val = mag_data["inv_amt"]
                                if not is_amount_equal(wb_val, inv_val):
                                    err_msg = f"MAGNERV Waybill Amt ({wb_val}) != Invoice Amt ({inv_val})"
                                    pre_mismatches.append(err_msg)
                                    irr_records.append({
                                        "pup_date": pup_date_text,
                                        "origin": current_origin,
                                        "mawb": target_mawb_no,
                                        "hawb": target_hawb_no,
                                        "desc": err_msg,
                                        "corr": "",
                                        "open_date": datetime.now().strftime("%m-%d %H:%M")
                                    })
                                cand_i = excel_calc["MAG1"]
                                cand_j = excel_calc["MAG2"]
                                if not (is_amount_equal(wb_val, cand_i) or is_amount_equal(wb_val, cand_j)):
                                    err_msg = f"MAGNERV UFS Amt ({wb_val}) does not match Excel I24 ({cand_i}) or J24 ({cand_j})"
                                    pre_mismatches.append(err_msg)
                                    irr_records.append({
                                        "pup_date": pup_date_text,
                                        "origin": current_origin,
                                        "mawb": target_mawb_no,
                                        "hawb": target_hawb_no,
                                        "desc": f"MAGNERV ${wb_val}",
                                        "corr": f"MAGNERV ${cand_i}",
                                        "open_date": datetime.now().strftime("%m-%d %H:%M")
                                    })
                            else:
                                err_msg = "MAGNERV code missing from initial charges"
                                pre_mismatches.append(err_msg)
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": err_msg,
                                    "corr": "",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                        elif is_han and "MAGNERV" in initial_charges_map:
                            err_msg = "Unexpected MAGNERV found in HAN shipment (MAGNERV should not exist)"
                            pre_mismatches.append(err_msg)
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })

                        # 선검증 결과 판정: 불일치 시 HANDRV 수정 없이 창 닫고 PASS
                        print("\n" + "=" * 65)
                        print(f"    [HAWB {target_hawb_no} 선검증 결과 (Origin: {current_origin})]")
                        print("=" * 65)

                        if pre_mismatches:
                            print(" [선검증 실패 - 불일치 항목 발견] HANDRV 수정을 진행하지 않고 창을 닫고 PASS 처리합니다.")
                            for m_idx_err, mis in enumerate(pre_mismatches, start=1):
                                print(f"  ({m_idx_err}) {mis}")
                            print("-" * 65)
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        print(" [선검증 완료 - ALL PASS] 선검증 항목 100% 일치 확인!")
                        print(" ▶ HANDRV 수정 작업을 시작합니다.")

                        # ==========================================
                        # HAWB 작업 7: 선검증 통과 후 HANDRV 드롭다운 수정 및 대조
                        # ==========================================
                        print(f"\n[HAWB {target_hawb_no} 작업 7] HANDRV 드롭다운 수정 (Terms -> Prepaid / InvoiceCurr -> USD)")

                        for crow_data in initial_charges:
                            if crow_data["code"] == "HANDRV":
                                row_el = crow_data["row_element"]

                                # 1) Terms 드롭다운 -> 클릭 -> 0.3초 -> P -> 0.3초 -> TAB -> Prepaid
                                terms_wrap = row_el.find_element(By.CSS_SELECTOR, "select[name='terms'] ~ span.k-select, td:nth-child(6) span.k-dropdown-wrap")
                                try:
                                    terms_wrap.click()
                                except Exception:
                                    driver.execute_script("arguments[0].click();", terms_wrap)

                                time.sleep(0.3)
                                act_el = driver.switch_to.active_element
                                act_el.send_keys("P")
                                time.sleep(0.3)
                                act_el.send_keys(Keys.TAB)
                                time.sleep(0.5)

                                # 2) InvoiceCurr 드롭다운 -> 클릭 -> 0.3초 -> USD (자간 0.1초) -> 0.3초 -> TAB -> USD
                                curr_wrap = row_el.find_element(By.CSS_SELECTOR, "select[name='invoiceCurr'] ~ span.k-select, td:nth-child(10) span.k-dropdown-wrap")
                                try:
                                    curr_wrap.click()
                                except Exception:
                                    driver.execute_script("arguments[0].click();", curr_wrap)

                                time.sleep(0.3)
                                act_el = driver.switch_to.active_element
                                for char in "USD":
                                    act_el.send_keys(char)
                                    time.sleep(0.1)
                                time.sleep(0.3)
                                act_el.send_keys(Keys.TAB)
                                time.sleep(1)

                                print(" ▶ HANDRV: Terms('Prepaid') 및 InvoiceCurr('USD') 키 입력 적용 완료.")
                                break

                        # 변경 후 화면 재계산 및 로딩 대기
                        time.sleep(2)
                        try:
                            WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")))
                        except Exception:
                            pass

                        updated_charges = parse_hawb_charges_grid(driver)
                        charges_map = {c["code"]: c for c in updated_charges}
                        handrv_mismatches = []

                        # HANDRV 대조 검증 (소수점 둘째 자리 기준)
                        if "HANDRV" in charges_map:
                            h_data = charges_map["HANDRV"]
                            wb_val = h_data["wb_amt"]
                            inv_val = h_data["inv_amt"]
                            if h_data["terms"] != "Prepaid":
                                err_msg = f"HANDRV Terms mismatch (Current: '{h_data['terms']}', Expected: 'Prepaid')"
                                handrv_mismatches.append(err_msg)
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": err_msg,
                                    "corr": "",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                            if h_data["inv_curr"] != "USD":
                                err_msg = f"HANDRV InvoiceCurr mismatch (Current: '{h_data['inv_curr']}', Expected: 'USD')"
                                handrv_mismatches.append(err_msg)
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": err_msg,
                                    "corr": "",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                            if not is_amount_equal(wb_val, inv_val):
                                err_msg = f"HANDRV Waybill Amt ({wb_val}) != Invoice Amt ({inv_val})"
                                handrv_mismatches.append(err_msg)
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": err_msg,
                                    "corr": "",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                            if not is_amount_equal(wb_val, expected_handrv):
                                handrv_mismatches.append(f"HANDRV UFS Amt ({wb_val}) != Excel {target_han_cell} ({expected_handrv})")
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": f"HANDRV ${wb_val}",
                                    "corr": f"HANDRV ${expected_handrv}",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                        else:
                            err_msg = "HANDRV code missing from charges"
                            handrv_mismatches.append(err_msg)
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })

                        if handrv_mismatches:
                            print(" [HANDRV 검증 실패 - 불일치 항목 발견] 저장하지 않고 PASS 처리합니다.")
                            for m_idx_err, mis in enumerate(handrv_mismatches, start=1):
                                print(f"  ({m_idx_err}) {mis}")
                            print("-" * 65)
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        print(" [HANDRV 검증 완료 - ALL PASS] 정상 확인되었습니다.")

                        # [HAWB 작업 8] Ctrl+S 입력으로 저장 및 신규 Charge 생성 완전 로딩 대기
                        print(f"\n[HAWB {target_hawb_no} 작업 8] Ctrl+S 를 눌러 저장을 실행합니다.")
                        try:
                            ActionChains(driver).key_down(Keys.CONTROL).send_keys("s").key_up(Keys.CONTROL).perform()
                        except Exception:
                            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.CONTROL, "s")

                        print(" ▶ 저장 완료 및 신규 Charge(FCFAD, FCFOAD) 생성 로딩 대기 중...")
                        time.sleep(3)

                        unexpected_err = handle_unexpected_error_modal(driver)
                        if unexpected_err:
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": unexpected_err,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
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

                        # 신규 생성 Charge 폴링 대기
                        print(" ▶ 신규 생성 Charge(FCFAD, FCFOAD) 및 그리드 렌더링 완료 상태를 확인합니다...")
                        expected_all_codes = {"FCFAD", "FCFOAD", "HANDRV", "FRTRV", "FSCRV"}
                        if is_pvg and has_magnerv_initial:
                            expected_all_codes.add("MAGNERV")

                        post_charges = []
                        post_codes = []
                        poll_timeout = 30
                        poll_start = time.time()
                        save_modal_error = None

                        while time.time() - poll_start < poll_timeout:
                            try:
                                save_modal_error = handle_unexpected_error_modal(driver)
                                if save_modal_error:
                                    break

                                masks = driver.find_elements(By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")
                                if any(m.is_displayed() for m in masks):
                                    time.sleep(1)
                                    continue

                                post_charges = parse_hawb_charges_grid(driver)
                                post_codes = [c["code"] for c in post_charges]

                                if expected_all_codes.issubset(set(post_codes)):
                                    time.sleep(1.5)
                                    post_charges = parse_hawb_charges_grid(driver)
                                    post_codes = [c["code"] for c in post_charges]
                                    if expected_all_codes.issubset(set(post_codes)):
                                        print(f" ▶ 그리드 완전 로딩 완료 확인! (소요 시간: {time.time() - poll_start:.1f}초)")
                                        break
                            except Exception:
                                pass
                            time.sleep(1.5)

                        if save_modal_error:
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": save_modal_error,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        # [HAWB 작업 9] 저장 후 생성된 Charge 목록 재인식 및 FCFAD 정밀 검증
                        print(f"\n[HAWB {target_hawb_no} 작업 9] 저장 후 생성된 차지 목록 및 FCFAD 정밀 검증")
                        print(f" ▶ 저장 후 최종 인식된 Charge Codes: {post_codes}")

                        post_map = {c["code"]: c for c in post_charges}
                        post_mismatches = []

                        missing_post_codes = expected_all_codes - set(post_codes)
                        extra_post_codes = set(post_codes) - expected_all_codes

                        if len(post_codes) != len(set(post_codes)):
                            dup_codes = sorted(list({c for c in post_codes if post_codes.count(c) > 1}))
                            err_msg = f"Duplicate post-save charge codes found: {dup_codes}"
                            post_mismatches.append(err_msg)
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })

                        if missing_post_codes:
                            err_msg = f"Missing required post-save charge codes: {sorted(list(missing_post_codes))}"
                            post_mismatches.append(err_msg)
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                        if extra_post_codes:
                            err_msg = f"Unexpected post-save charge codes found: {sorted(list(extra_post_codes))}"
                            post_mismatches.append(err_msg)
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })

                        expected_fcfad = excel_calc["FCF_AS"] if service_code == "AS" else excel_calc["FCF_AD"]
                        target_fcf_cell = excel_calc["FCF_AS_CELL"] if service_code == "AS" else excel_calc["FCF_AD_CELL"]

                        if "FCFAD" in post_map:
                            fcf_data = post_map["FCFAD"]
                            wb_val = fcf_data["wb_amt"]
                            inv_val = fcf_data["inv_amt"]
                            if not is_amount_equal(wb_val, inv_val):
                                err_msg = f"FCFAD Waybill Amt ({wb_val}) != Invoice Amt ({inv_val})"
                                post_mismatches.append(err_msg)
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": err_msg,
                                    "corr": "",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                            if not is_amount_equal(wb_val, expected_fcfad):
                                post_mismatches.append(f"FCFAD UFS Amt ({wb_val}) != Excel {target_fcf_cell}({service_code}) ({expected_fcfad})")
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": f"FCFAD ${wb_val}",
                                    "corr": f"FCFAD ${expected_fcfad}",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                        else:
                            err_msg = "FCFAD charge code was not generated after save"
                            post_mismatches.append(err_msg)
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })

                        # UFS VALUE와 엑셀 계산 최종 재대조 (소수점 둘째 자리 기준)
                        if "HANDRV" in post_map:
                            if not is_amount_equal(post_map["HANDRV"]["wb_amt"], expected_handrv):
                                post_mismatches.append(f"Final match failure: HANDRV UFS Amt ({post_map['HANDRV']['wb_amt']}) != Excel {target_han_cell} ({expected_handrv})")
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": f"HANDRV ${post_map['HANDRV']['wb_amt']}",
                                    "corr": f"HANDRV ${expected_handrv}",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                        if "FRTRV" in post_map:
                            if not is_amount_equal(post_map["FRTRV"]["wb_amt"], expected_frtrv):
                                post_mismatches.append(f"Final match failure: FRTRV UFS Amt ({post_map['FRTRV']['wb_amt']}) != Excel {target_frt_cell} ({expected_frtrv})")
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": f"FRTRV ${post_map['FRTRV']['wb_amt']}",
                                    "corr": f"FRTRV ${expected_frtrv}",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                        if "FSCRV" in post_map:
                            if not is_amount_equal(post_map["FSCRV"]["wb_amt"], expected_fscrv):
                                post_mismatches.append(f"Final match failure: FSCRV UFS Amt ({post_map['FSCRV']['wb_amt']}) != Excel {target_fsc_cell} ({expected_fscrv})")
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": f"FSCRV ${post_map['FSCRV']['wb_amt']}",
                                    "corr": f"FSCRV ${expected_fscrv}",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })
                        if is_pvg and has_magnerv_initial and "MAGNERV" in post_map:
                            mag_w = post_map["MAGNERV"]["wb_amt"]
                            if not (is_amount_equal(mag_w, excel_calc["MAG1"]) or is_amount_equal(mag_w, excel_calc["MAG2"])):
                                post_mismatches.append(f"Final match failure: MAGNERV UFS Amt ({mag_w}) != Excel I24/J24")
                                irr_records.append({
                                    "pup_date": pup_date_text,
                                    "origin": current_origin,
                                    "mawb": target_mawb_no,
                                    "hawb": target_hawb_no,
                                    "desc": f"MAGNERV ${mag_w}",
                                    "corr": f"MAGNERV ${excel_calc['MAG1']}",
                                    "open_date": datetime.now().strftime("%m-%d %H:%M")
                                })

                        print("\n" + "=" * 65)
                        print(f"    [HAWB {target_hawb_no} 저장 후 최종 대조 및 Charge 검증 결과]")
                        print("=" * 65)

                        if post_mismatches:
                            print(" [최종 검증 실패] 불일치 항목이 발생하여 창을 닫고 다음 HAWB로 이동합니다.")
                            for p_idx, p_mis in enumerate(post_mismatches, start=1):
                                print(f"  ({p_idx}) {p_mis}")
                            print("-" * 65)
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        print(" [최종 검증 완료 - ALL PASS] 모든 Charge 및 UFS VALUE가 엑셀 계산과 100% 일치합니다!")
                        print(f"    - FCFAD : {expected_fcfad} ({target_fcf_cell})")

                        # [HAWB 작업 10] New 드롭다운 -> 'Create Bill As a Charged Invoice' 실행
                        print(f"\n[HAWB {target_hawb_no} 작업 10] New 드롭다운 -> 'Create Bill As a Charged Invoice' 실행")

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
                                print(f" ▶ 발견된 {len(btns)}개의 New 버튼 중 두 번째(Invoice용) 버튼을 정확히 선택하였습니다.")
                                break
                            elif len(btns) == 1:
                                target_new_btn = btns[0]
                                break

                        if target_new_btn:
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target_new_btn)
                            time.sleep(0.3)
                            driver.execute_script("arguments[0].click();", target_new_btn)
                            print(" ▶ 두 번째 New 버튼 클릭 완료.")
                        else:
                            print(" [안내] New 버튼을 명시적으로 클릭하지 못했습니다. 메뉴 텍스트 직접 탐색을 시도합니다.")

                        time.sleep(1.2)

                        menu_xpaths = [
                            "//li[contains(@class, 'k-item') and contains(text(), 'Create Bill As a Charged Invoice')]",
                            "//li[@role='menuItem' and contains(text(), 'Create Bill As a Charged Invoice')]",
                            "//*[contains(text(), 'Create Bill As a Charged Invoice')]",
                            "//li[contains(., 'Create Bill As a Charged Invoice')]"
                        ]

                        bill_item_clicked = False
                        for attempt in range(10):
                            for mxpath in menu_xpaths:
                                try:
                                    items = driver.find_elements(By.XPATH, mxpath)
                                    for it in items:
                                        if it.is_displayed() or it.is_enabled():
                                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", it)
                                            time.sleep(0.2)
                                            try:
                                                it.click()
                                            except Exception:
                                                driver.execute_script("arguments[0].click();", it)
                                            print(" ▶ 'Create Bill As a Charged Invoice' 텍스트 확인 및 클릭 성공!")
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
                            err_msg = "Failed to locate or click 'Create Bill As a Charged Invoice'"
                            print(f" [오류] {err_msg}. 창을 닫고 다음 HAWB로 이동합니다.")
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        print(" ▶ 생성 및 저장 로딩 완료 대기 중...")
                        time.sleep(3)

                        unexpected_err = handle_unexpected_error_modal(driver)
                        if unexpected_err:
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": unexpected_err,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
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

                        # 생성된 Invoice 검증
                        now = datetime.now()
                        today_date_str = now.strftime("%d/%m/%Y")
                        today_candidates = {
                            today_date_str,
                            f"{now.day}/{now.month}/{now.year}",
                            f"{now.day:02d}/{now.month:02d}/{now.year}"
                        }

                        print(f"\n[HAWB {target_hawb_no} 보강 검증] 생성된 Invoice 확인 중 (Status: Open / Imp/Exp: Import / Date: {today_date_str} / 총 2건)...")

                        matched_invoices = []
                        poll_timeout = 30
                        poll_start = time.time()
                        invoice_modal_error = None

                        while time.time() - poll_start < poll_timeout:
                            try:
                                invoice_modal_error = handle_unexpected_error_modal(driver)
                                if invoice_modal_error:
                                    break

                                masks = driver.find_elements(By.CSS_SELECTOR, ".k-loading-mask, .k-loading-image")
                                if any(m.is_displayed() for m in masks):
                                    time.sleep(1)
                                    continue
                            except Exception:
                                pass

                            matched_invoices.clear()
                            all_master_rows = driver.find_elements(By.CSS_SELECTOR, "tr.k-master-row")

                            for r in all_master_rows:
                                try:
                                    inv_no_val = ""
                                    inv_no_els = r.find_elements(By.XPATH, ".//a[contains(@href, 'accounting/invoicing') or contains(@href, 'invoicing')]//span[contains(@class, 'case-cell')]")
                                    if not inv_no_els:
                                        inv_no_els = r.find_elements(By.CSS_SELECTOR, "a[href*='invoicing'] span.case-cell, a[href*='invoicing'] span")
                                    if inv_no_els:
                                        inv_no_val = inv_no_els[0].text.strip()
                                        if not inv_no_val:
                                            inv_no_val = driver.execute_script("return arguments[0].textContent;", inv_no_els[0]).strip()

                                    if not inv_no_val:
                                        continue

                                    status_val = ""
                                    status_els = r.find_elements(By.XPATH, ".//select[@name='invStatus']/ancestor::*[contains(@class, 'k-dropdown') or contains(@class, 'k-dropdown-wrap')]//span[contains(@class, 'k-input')]")
                                    if not status_els:
                                        status_els = r.find_elements(By.CSS_SELECTOR, "select[name='invStatus'] ~ span.k-input, [id*='invStatus'] span.k-input")
                                    if status_els:
                                        status_val = status_els[0].text.strip()
                                        if not status_val:
                                            status_val = driver.execute_script("return arguments[0].textContent;", status_els[0]).strip()

                                    imp_exp_val = ""
                                    impexp_els = r.find_elements(By.XPATH, ".//select[@name='impExp']/ancestor::*[contains(@class, 'k-dropdown') or contains(@class, 'k-dropdown-wrap')]//span[contains(@class, 'k-input')]")
                                    if not impexp_els:
                                        impexp_els = r.find_elements(By.CSS_SELECTOR, "select[name='impExp'] ~ span.k-input, [id*='impExp'] span.k-input")
                                    if impexp_els:
                                        imp_exp_val = impexp_els[0].text.strip()
                                        if not imp_exp_val:
                                            imp_exp_val = driver.execute_script("return arguments[0].textContent;", impexp_els[0]).strip()

                                    inv_date_val = ""
                                    date_els = r.find_elements(By.CSS_SELECTOR, "input[name='invoiceDate']")
                                    if date_els:
                                        inv_date_val = (date_els[0].get_attribute("value") or driver.execute_script("return arguments[0].value;", date_els[0]) or "").strip()

                                    if status_val.upper() == "OPEN" and imp_exp_val.upper() == "IMPORT" and inv_date_val in today_candidates:
                                        matched_invoices.append({
                                            "no": inv_no_val,
                                            "status": status_val,
                                            "imp_exp": imp_exp_val,
                                            "date": inv_date_val
                                        })
                                except Exception:
                                    continue

                            if len(matched_invoices) == 2:
                                break
                            time.sleep(1.5)

                        if invoice_modal_error:
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": invoice_modal_error,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        if len(matched_invoices) != 2:
                            err_msg = f"Invoice verification failed: Expected 2 Open/Import invoices for {today_date_str}, but found {len(matched_invoices)}"
                            print(f" [오류] {err_msg}. 창을 닫고 다음 HAWB로 이동합니다.")
                            irr_records.append({
                                "pup_date": pup_date_text,
                                "origin": current_origin,
                                "mawb": target_mawb_no,
                                "hawb": target_hawb_no,
                                "desc": err_msg,
                                "corr": "",
                                "open_date": datetime.now().strftime("%m-%d %H:%M")
                            })
                            try:
                                driver.close()
                                driver.switch_to.window(mawb_window)
                            except Exception:
                                pass
                            time.sleep(2)
                            continue

                        print(f" ▶ [확인 성공] 조건 만족 Invoice 2건 정상 확인! (소요 시간: {time.time() - poll_start:.1f}초)")
                        for inv_i, inv_data in enumerate(matched_invoices, start=1):
                            print(f"    [{inv_i}] Invoice No: {inv_data['no']} | Status: {inv_data['status']} | Imp/Exp: {inv_data['imp_exp']} | Date: {inv_data['date']}")

                        print(f"\n[최종 완료] HAWB ({target_hawb_no}) 모든 처리 완료! 창을 닫고 MAWB로 복귀합니다.")
                        open_records.append({
                            "pup_date": pup_date_text,
                            "origin": current_origin,
                            "mawb": target_mawb_no,
                            "hawb": target_hawb_no,
                            "open_date": datetime.now().strftime("%m-%d %H:%M")
                        })
                        try:
                            driver.close()
                            driver.switch_to.window(mawb_window)
                        except Exception:
                            pass
                        time.sleep(2)

                    except Exception as h_ex:
                        modal_err = handle_unexpected_error_modal(driver)
                        err_desc = modal_err if modal_err else f"Exception: {h_ex}"
                        print(f"[오류] HAWB ({target_hawb_no}) 처리 중 예외 발생: {err_desc}")
                        irr_records.append({
                            "pup_date": current_active_context.get("pup_date", ""),
                            "origin": current_origin,
                            "mawb": target_mawb_no,
                            "hawb": target_hawb_no,
                            "desc": err_desc,
                            "corr": "",
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

                print("\n" + "=" * 70)
                print(f" [작업 완료] 지정된 MAWB ({target_mawb_no}) [{target_mawb_origin}] 내 모든 대상 HAWB 처리가 완료되었습니다!")
                print("=" * 70)

                try:
                    driver.close()
                except Exception:
                    pass
                driver.switch_to.window(breakbulk_window)
                time.sleep(1.5)

            except Exception as m_ex:
                modal_err = handle_unexpected_error_modal(driver)
                err_desc = modal_err if modal_err else f"Exception: {m_ex}"
                print(f"[오류] MAWB ({target_mawb_no}) 처리 중 예외 발생: {err_desc}")
                already_logged = any(
                    r.get("mawb") == target_mawb_no and r.get("hawb") == current_active_context.get("hawb", "")
                    for r in (irr_records + open_records)
                )
                if not already_logged:
                    irr_records.append({
                        "pup_date": current_active_context.get("pup_date", ""),
                        "origin": current_active_context.get("origin") or target_mawb_origin or "PVG",
                        "mawb": target_mawb_no,
                        "hawb": current_active_context.get("hawb", ""),
                        "desc": err_desc,
                        "corr": "",
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

        # 작업 루프 정상 종료 시 컨텍스트 초기화 (마지막 완료/스킵 건이 중단 건으로 오인되지 않도록 처리)
        current_active_context["mawb"] = ""
        current_active_context["hawb"] = ""
        current_active_context["origin"] = ""
        current_active_context["pup_date"] = ""

        # 9. 정상 종료 시 리포트 엑셀 생성
        trigger_guaranteed_report()
        input("\n[완료] 프로그램이 정상 완료되었습니다. 종료하려면 엔터를 누르세요...")

    except KeyboardInterrupt:
        print("\n[정보] 사용자에 의해 프로그램이 중단되었습니다. 리포트를 안전하게 생성합니다.")
        trigger_guaranteed_report()
    except Exception as e:
        print(f"\n[오류] 예외가 발생했습니다: {e}")
        traceback.print_exc()
        trigger_guaranteed_report()
        input("\n종료하려면 엔터를 누르세요...")
    finally:
        trigger_guaranteed_report()
        cleanup_processes(driver)


if __name__ == "__main__":
    main()