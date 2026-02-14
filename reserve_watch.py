import os, time, json, re, traceback
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright

# ====== 네 조건 ======
TARGET_DATE = "2026-02-19"
PEOPLE = 2
MIN_TIME_24 = "19:00"     # 19:00 이후만
POLL_SECONDS = 30
# ====================

# ====== 중단 감지/알림 ======
HEARTBEAT_EVERY = 10 * 60     # 10분마다 "살아있음" 알림
RESTART_COOLDOWN = 10         # 크래시 후 재시작 대기(초)
# ===========================

RESERVE_URL = r"https://www.google.com/maps/reserve/v/dine/c/AWbymhwDCQE?source=pa&opi=89978449&hl=ko-KR&gei=OxaQad3iOaPR2roP7PuhsAU&sourceurl=https://www.google.com/search?q%3D%25EB%25B9%2599%25EC%2584%25A4%25EC%259D%2598%25EB%25AC%25B8%26oq%3D%25EB%25B9%2599%25EC%2584%25A4%25EC%259D%2598%25EB%25AC%25B8%26gs_lcrp%3DEgZjaHJvbWUqBggAEEUYOzIGCAAQRRg7MgYIARBFGDsyBggCEEUYPTIGCAMQRRg90gEIMjg0MGowajeoAgiwAgE%26sourceid%3Dchrome%26ie%3DUTF-8"

STATE_FILE = f"state_slots_{TARGET_DATE}_{PEOPLE}p_{MIN_TIME_24.replace(':','')}.json"

TG_TOKEN = os.environ.get("TG_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

def tg_send(text: str):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    # raise 안 걸어도 되게(중단 알림이 또 중단되는 거 방지)
    try:
        requests.post(url, data={"chat_id": TG_CHAT_ID, "text": text}, timeout=15)
    except:
        pass

def load_seen():
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("seen", []))
    except:
        return set()

def save_seen(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"seen": sorted(seen)}, f, ensure_ascii=False, indent=2)

TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")

def to_minutes(t24: str) -> int:
    h, m = t24.split(":")
    return int(h) * 60 + int(m)

MIN_MINUTES = to_minutes(MIN_TIME_24)

def normalize_to_24h(raw: str):
    raw = " ".join((raw or "").split())
    m = TIME_RE.search(raw)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))

    if "오전" in raw:
        if hh == 12:
            hh = 0
    elif "오후" in raw:
        if hh != 12:
            hh += 12

    if hh < 0 or hh > 23:
        return None
    return f"{hh:02d}:{mm:02d}"

def extract_time_buttons_24(page):
    out = set()
    btns = page.get_by_role("button")
    try:
        for i in range(min(btns.count(), 500)):
            t = (btns.nth(i).inner_text(timeout=300) or "").strip()
            t24 = normalize_to_24h(t)
            if t24:
                out.add(t24)
    except:
        pass
    return sorted(out)

def try_set_people(page):
    try:
        page.get_by_role("button", name=re.compile(r"\d+\s*명")).first.click(timeout=3000)
        time.sleep(0.2)
    except:
        pass
    try:
        page.get_by_role("option", name=re.compile(rf"^{PEOPLE}\s*명$")).click(timeout=3000)
        return
    except:
        pass
    try:
        page.get_by_role("button", name=re.compile(rf"^{PEOPLE}\s*명$")).click(timeout=3000)
        return
    except:
        pass

def try_set_date(page):
    try:
        loc = page.locator('input[type="date"]')
        if loc.count() > 0:
            loc.first.fill(TARGET_DATE, timeout=3000)
            return
    except:
        pass

    # 달력 열기 시도
    opened = False
    try:
        page.get_by_role("button", name=re.compile(r"\d+\s*월\s*\d+\s*일")).first.click(timeout=3000)
        opened = True
        time.sleep(0.2)
    except:
        pass
    if not opened:
        try:
            page.get_by_role("button", name=re.compile(r"날짜|일자|Date", re.IGNORECASE)).first.click(timeout=3000)
            opened = True
            time.sleep(0.2)
        except:
            pass

    day = datetime.fromisoformat(TARGET_DATE).day
    try:
        page.get_by_role("gridcell", name=re.compile(rf"^{day}$")).first.click(timeout=3000)
        return
    except:
        pass
    try:
        page.get_by_role("button", name=re.compile(rf"^{day}$")).first.click(timeout=3000)
        return
    except:
        pass

def run_monitor_once():
    """Playwright 세션 1회 실행(무한루프는 바깥 watchdog에서)."""
    label = f"{TARGET_DATE} / {PEOPLE}명 / {MIN_TIME_24}~"
    seen = load_seen()

    with sync_playwright() as p:
        browser = None
        page = None

        def restart_browser():
            nonlocal browser, page
            try:
                if browser:
                    browser.close()
            except:
                pass
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox"]
            )
            page = browser.new_page(locale="ko-KR")

        restart_browser()

        last_heartbeat = 0

        tg_send(f"✅ 감시 시작\n조건: {label}")

        while True:
            now = time.time()

            # 주기적 생존 알림
            if now - last_heartbeat >= HEARTBEAT_EVERY:
                tg_send(f"💓 감시 중(정상)\n조건: {label}")
                last_heartbeat = now

            try:
                page.goto(RESERVE_URL, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)

                try_set_people(page)
                try_set_date(page)
                page.wait_for_timeout(1500)

                times_24 = extract_time_buttons_24(page)
                evening = sorted([t for t in times_24 if to_minutes(t) >= MIN_MINUTES])
                new_times = [t for t in evening if t not in seen]

                if new_times:
                    tg_send("🟢 새 예약 가능 슬롯\n" + label + "\n" + ", ".join(new_times) + f"\n\n{RESERVE_URL}")
                    seen |= set(new_times)
                    save_seen(seen)

            except Exception as e:
                # 페이지 크래시/세션 꼬임 → 브라우저만 재시작
                tg_send(f"⚠️ 페이지/브라우저 에러: {type(e).__name__}: {e}\n(브라우저 재시작)")
                restart_browser()

            time.sleep(POLL_SECONDS)

def main_watchdog():
    if not TG_TOKEN or not TG_CHAT_ID:
        raise SystemExit("TG_TOKEN / TG_CHAT_ID 환경변수부터 설정해야 함")

    while True:
        try:
            run_monitor_once()
        except Exception as e:
            # 파이썬 프로세스 레벨로 뻗을만한 에러 → 알림 후 재시작
            tb = traceback.format_exc(limit=2)
            tg_send(f"🚨 감시 중단됨(프로그램 크래시)\n{type(e).__name__}: {e}\n재시작함\n{tb}")
            time.sleep(RESTART_COOLDOWN)

if __name__ == "__main__":
    main_watchdog()
