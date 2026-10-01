#!/usr/bin/env python3
"""Login to HRX IQ — pehle refresh/session cookies, fail par email/password."""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    InvalidSessionIdException,
    WebDriverException,
)
from pathlib import Path
from datetime import datetime, timedelta
import json
import re
import socket
import time

BASE_URL = "https://www.hrxiq.com"
LOGIN_URL = f"{BASE_URL}/login/"
DASHBOARD_URL = f"{BASE_URL}/dashboard"
COOKIES_FILE = Path(__file__).with_name("hrxiq_cookies.json")

EMAIL = "avkumar@datopic.com"
PASSWORD = "Avanish@2000"

EMAIL_XPATH = "/html/body/div[1]/div/div[2]/div[2]/main/form/div[1]/input"
PASSWORD_XPATH = "/html/body/div[1]/div/div[2]/div[2]/main/form/div[2]/div/input"
LOGIN_BUTTON_XPATH = "/html/body/div[1]/div/div[2]/div[2]/main/form/button"
TIMER_BUTTON_XPATH = (
    "/html/body/div[2]/div[2]/header/nav/div[2]/div/div[2]/button[1]"
)
TIMER_RE = re.compile(r"\b(\d{1,2}:\d{2}:\d{2})\b")
WAIT_BEFORE_REFRESH = 30
# Long wait mein Chrome alive rakhne ke liye ping interval
KEEPALIVE_EVERY_SEC = 5 * 60


def setup_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    # Idle crash / memory pressure kam karne ke liye
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    return webdriver.Chrome(options=options)


def save_cookies(driver):
    cookies = driver.get_cookies()
    COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
    print(f"Cookies saved -> {COOKIES_FILE}")


def load_cookies(driver):
    if not COOKIES_FILE.exists():
        return None
    try:
        cookies = json.loads(COOKIES_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Cookie file read failed: {e}")
        return None

    refresh = next((c for c in cookies if c.get("name") == "pp_refresh_token"), None)
    if not refresh or not refresh.get("value"):
        print("No pp_refresh_token in saved cookies")
        return None

    driver.get(BASE_URL + "/")
    time.sleep(1)

    for cookie in cookies:
        cookie = dict(cookie)
        cookie.pop("sameSite", None)
        if "expiry" in cookie:
            cookie["expiry"] = int(cookie["expiry"])
        try:
            driver.add_cookie(cookie)
        except Exception as e:
            print(f"Skip cookie {cookie.get('name')}: {e}")

    print("Saved cookies (incl. refresh token) loaded")
    return refresh.get("value")


def is_logged_in(driver):
    url = driver.current_url.lower()
    if "/login" in url:
        return False
    login_inputs = driver.find_elements(By.XPATH, EMAIL_XPATH)
    if login_inputs:
        return False
    return "/dashboard" in url or "hrxiq.com" in url


def try_login_with_refresh(driver):
    print("Checking refresh token / saved cookies ...")
    token = load_cookies(driver)
    if not token:
        return False

    driver.get(DASHBOARD_URL)
    time.sleep(3)

    if not is_logged_in(driver):
        print("First check failed, reloading once ...")
        driver.refresh()
        time.sleep(3)

    if is_logged_in(driver):
        print(f"Logged in via refresh token. URL: {driver.current_url}")
        save_cookies(driver)
        return True

    print("Refresh token / cookies invalid or expired")
    return False


def login_with_credentials(driver, wait):
    print(f"Opening {LOGIN_URL} ...")
    driver.get(LOGIN_URL)

    email_input = wait.until(EC.presence_of_element_located((By.XPATH, EMAIL_XPATH)))
    email_input.clear()
    email_input.send_keys(EMAIL)
    print("Email entered")

    password_input = wait.until(EC.presence_of_element_located((By.XPATH, PASSWORD_XPATH)))
    password_input.clear()
    password_input.send_keys(PASSWORD)
    print("Password entered")

    login_button = wait.until(EC.element_to_be_clickable((By.XPATH, LOGIN_BUTTON_XPATH)))
    login_button.click()
    print("Login button clicked")

    time.sleep(5)
    print(f"Current URL after login: {driver.current_url}")

    if is_logged_in(driver) or "/login" not in driver.current_url.lower():
        save_cookies(driver)
        print("Credential login OK")
        return True

    print("Credential login may have failed")
    return False


def do_login(driver, wait):
    ok = try_login_with_refresh(driver)
    if not ok:
        ok = login_with_credentials(driver, wait)
    return ok


class BrowserSession:
    """Chrome band ho jaye to dubara open + login."""

    def __init__(self):
        self.driver = None
        self.wait = None
        self.start_fresh()

    def start_fresh(self):
        self.driver = setup_driver()
        self.wait = WebDriverWait(self.driver, 20)

    def alive(self):
        try:
            _ = self.driver.current_url
            self.driver.execute_script("return 1")
            return True
        except Exception:
            return False

    def close_quietly(self):
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None
        self.wait = None

    def restart(self, reason=""):
        print(f"Browser disconnected ({reason}) — Chrome restart + re-login ...")
        if not internet_ok():
            print("Internet nahi hai — restart abhi nahi, baad mein retry")
            return False
        self.close_quietly()
        try:
            self.start_fresh()
            ok = do_login(self.driver, self.wait)
        except Exception as e:
            print(f"Re-login error (script chalti rahegi): {short_err(e)}")
            return False
        if not ok:
            print("Re-login failed after browser restart")
            return False
        try:
            if "/dashboard" not in self.driver.current_url.lower():
                self.driver.get(DASHBOARD_URL)
                time.sleep(2)
        except Exception as e:
            print(f"Dashboard open failed: {short_err(e)}")
            return False
        print("Browser session recovered")
        return True

    def ensure(self):
        if self.alive():
            return True
        return self.restart("invalid session / closed")


def short_err(exc):
    return str(exc).split("Stacktrace:")[0].strip().replace("\n", " ")[:180]


def internet_ok():
    """DNS/network up hai ya nahi — site open karne se pehle."""
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=5).close()
        return True
    except OSError:
        return False


def wait_for_internet(max_wait=15 * 60):
    """Internet na ho to wait. max_wait ke baad False (loop next slot pe try karega)."""
    if internet_ok():
        return True
    print("Internet disconnected — wait kar raha hoon ...")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        time.sleep(30)
        if internet_ok():
            print("Internet wapas aa gaya")
            return True
        print("Abhi bhi internet nahi — 30s baad dubara ...")
    print("Internet wait timeout — is check ko skip, agla slot try hoga")
    return False


def is_network_error(exc):
    msg = str(exc).lower()
    needles = (
        "err_internet_disconnected",
        "err_name_not_resolved",
        "err_network_changed",
        "err_connection",
        "err_address_unreachable",
        "net::err_",
        "timed out",
        "timeout",
    )
    return any(n in msg for n in needles)


def is_session_error(exc):
    if is_network_error(exc):
        return False
    msg = str(exc).lower()
    if isinstance(exc, InvalidSessionIdException):
        return True
    needles = (
        "invalid session id",
        "session deleted",
        "not connected to devtools",
        "chrome not reachable",
        "no such window",
        "target window already closed",
    )
    return any(n in msg for n in needles)


def button_text(btn):
    text = (btn.text or "").strip()
    if not text:
        text = (btn.get_attribute("innerText") or "").strip()
    return text


def extract_timer(text):
    m = TIMER_RE.search(text or "")
    return m.group(1) if m else None


def read_timer_value(driver, wait):
    wait.until(EC.presence_of_element_located((By.XPATH, TIMER_BUTTON_XPATH)))
    btn = driver.find_element(By.XPATH, TIMER_BUTTON_XPATH)
    return extract_timer(button_text(btn)), button_text(btn)


def next_half_hour_slot(now=None):
    now = now or datetime.now()
    t = now.replace(second=0, microsecond=0)
    if now.minute in (0, 30) and now.second == 0 and now.microsecond == 0:
        return t
    if now.minute < 30:
        return t.replace(minute=30)
    return (t + timedelta(hours=1)).replace(minute=0)


def sleep_until(target, session=None):
    """Wait until target; beech-beech mein Chrome keepalive ping."""
    remaining = (target - datetime.now()).total_seconds()
    if remaining <= 0:
        return
    print(
        f"Next check at {target.strftime('%I:%M %p')} "
        f"(wait ~{int(remaining // 60)} min {int(remaining % 60)} sec) ..."
    )
    last_ping = time.time()
    while True:
        remaining = (target - datetime.now()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30))
        if session and (time.time() - last_ping) >= KEEPALIVE_EVERY_SEC:
            last_ping = time.time()
            try:
                session.driver.execute_script("return 1")
            except Exception:
                print("Keepalive: browser dead (laptop sleep/crash?) — recover on next check")


def check_pause_after_refresh(driver, wait, wait_seconds=WAIT_BEFORE_REFRESH):
    try:
        before, raw = read_timer_value(driver, wait)
    except Exception as e:
        print(f"  First timer read failed: {e}")
        raise

    if not before:
        print(f"  No HH:MM:SS on first read: {raw!r}")
        return False, None

    print(f"  Before: {before} — waiting {wait_seconds}s then refresh ...")
    time.sleep(wait_seconds)

    print("  Refreshing ...")
    driver.refresh()
    time.sleep(2)

    after, raw2 = read_timer_value(driver, wait)
    if not after:
        print(f"  No HH:MM:SS after refresh: {raw2!r}")
        return False, None

    print(f"  After refresh: {after}")

    if after == before:
        print(f"  Same time ({before}) → PAUSED")
        return True, after

    print(f"  Changed {before} -> {after} → RUNNING")
    return False, after


def run_one_pause_check(session, label=None):
    """Ek baar check; session dead ho to recover + retry."""
    now = datetime.now()
    title = label or f"Check @ {now.strftime('%I:%M:%S %p')}"
    print(f"\n=== {title} ===")

    for attempt in range(1, 3):
        try:
            if not session.ensure():
                print("Could not recover browser session")
                return False

            driver, wait = session.driver, session.wait

            driver.get(DASHBOARD_URL)
            time.sleep(2)

            if not is_logged_in(driver):
                print("Session expired — re-login ...")
                if not do_login(driver, wait):
                    print("Re-login failed")
                    return False
                driver.get(DASHBOARD_URL)
                time.sleep(2)

            paused, value = check_pause_after_refresh(driver, wait)
            if paused:
                print(f"PAUSED at {value} — clicking")
                btn = wait.until(
                    EC.element_to_be_clickable((By.XPATH, TIMER_BUTTON_XPATH))
                )
                btn.click()
                print("Clicked.")
                save_cookies(driver)
                return True

            print("Not paused — skip")
            return False

        except Exception as e:
            if is_network_error(e):
                print(f"Network error: {short_err(e)}")
                if wait_for_internet() and attempt < 2:
                    session.restart("network back")
                    continue
                print("Is slot ka check skip — script band nahi hogi")
                return False
            if is_session_error(e) and attempt < 2:
                print(f"Session error on attempt {attempt}: {short_err(e)}")
                session.restart(short_err(e))
                continue
            print(f"Check failed: {short_err(e)}")
            return False

    return False


def check_pause_every_half_hour(session):
    print("Schedule: pehle abhi check, phir har 30 min pe")
    print("Band karne ke liye Ctrl+C dabao.")

    try:
        run_one_pause_check(session, label="Check right after login")
    except Exception as e:
        print(f"Immediate check failed (continue): {short_err(e)}")

    while True:
        slot = next_half_hour_slot()
        sleep_until(slot, session=session)
        try:
            run_one_pause_check(session)
        except Exception as e:
            print(f"Check failed (continue): {short_err(e)}")
        time.sleep(2)


def login():
    session = BrowserSession()

    try:
        ok = do_login(session.driver, session.wait)
        if not ok:
            print("Could not log in — Ctrl+C se band karo.")
            while True:
                time.sleep(60)

        if "/dashboard" not in session.driver.current_url.lower():
            session.driver.get(DASHBOARD_URL)
            time.sleep(2)

        run_one_pause_check(session, label="Check right after login")
    except KeyboardInterrupt:
        print("\nScript user ne band ki (Ctrl+C)")
    except Exception as e:
        print(f"Error (script yahin ruk gayi): {short_err(e)}")
    finally:
        session.close_quietly()
        print("Browser closed")


if __name__ == "__main__":
    login()
