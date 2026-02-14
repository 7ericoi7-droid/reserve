import os, time, json, re, traceback
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright

# ====== 네 조건(3/13로 고정) ======
TARGET_DATE = "2026-03-13"
PEOPLE = 2
MIN_TIME_24 = "19:00"
POLL_SECONDS = 30
# ================================

# ====== 상태/안정화 ======
HEARTBEAT_EVERY = 60 * 60          # 1시간마다 1번만 💓
RESTART_COOLDOWN = 10
HARD_RESTART_EVERY_LOOPS = 12      # 약 6분마다 브라우저 강제 재시작(크래시 방지)
STATE_FILE = f"state_slots_{TARGET_DATE}_{PEOPLE}p_{MIN_TIME_24.replace(':','')}.json"
# =========================

RESERVE_URL = r"https://www.google.com/maps/reserve/v/dine/c/AWbymhwDCQE?source=pa&opi=89978449&hl=ko-KR&gei=OxaQad3iOaPR2roP7PuhsAU&sourceurl=https://www.google.com/search?q%3D%25EB%25B9%2599%25EC%2584%25A4%25EC%259D%2598%25EB%25AC%25B8%26oq%3D%25EB%25B9%2599%25EC%2584%25A4%25EC%259D%2598%25EB%25AC%25B8%26gs_lcrp%3DEgZjaHJvbWUqBggAEEUYOzIGCAAQRRg7MgYIARBFGDsyBggCEEUYPTIGCAMQRRg90gEIMjg0MGowajeoAgiwAgE%26sourceid%3Dchrome%26ie%3DUTF-8"

TG_TOKEN = os.environ.get("TG_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

def tg_send(text: str):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
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
        if hh == 12: hh = 0
    elif "오후" in raw:
        if hh != 12: hh += 12

    if 0 <= hh <= 23:
        return f"{hh:02d}:{mm:02d}"
    return None

def extract_time_buttons_24(page):
    out = set()
    btns = page.get_by_role("button")
    try:
        for i in range(min(btns.count(), 700)):
            t = (btns.nth(i).inner_text(timeout=250) or "").strip()
            t24 = normalize_to_24h(t)
            if t24:
                out.add(t24)
    except:
        pass
    return sorted(out)

def try_set_people(page):
    # 1) "n명" 버튼 열기
    try:
        page.get_by_role("button", name=re.compile(r"\d+\s*명")).first.click(timeout=3000)
        page.wait_for_timeout(200)
    except:
        pass

    # 2) "2명" 선택
    for role, pat in [
        ("option", re.compile(rf"^{PEOPLE}\s*명$")),
        ("button", re.compile(rf"^{PEOPLE}\s*명$")),
        ("option", re.compile(rf"{PEOPLE}\s*명")),
        ("button", re.compile(rf"{PEOPLE}\s*명")),
    ]:
        try:
            page.get_by_role(role, name=pat).first.click(timeout=2500)
            return
        except:
            continue

def _date_strings():
    dt = datetime.fromisoformat(TARGET_DATE)
    m = dt.month
    d = dt.day
    # UI에 흔히 보이는 형태들
    return [
        f"{m}월 {d}일",
        f"{m}월{d}일",
        f"{m}/{d}",
        f"{m}.{d}",
        f"{m:02d}/{d:02d}",
        f"{m:02d}.{d:02d}",
        str(d),  # 최소 fallback
    ]

def assert_date_applied(page):
    body = ""
    try:
        body = page.inner_text("body", timeout=2500)
    except:
        return
    hits = 0
    for s in _date_strings()[:6]:
        if s and s in body:
            hits += 1
    if hits == 0:
        raise RuntimeError("DATE_NOT_APPLIED")

def try_set_date(page):
    dt = datetime.fromisoformat(TARGET_DATE)
    month = dt.month
    day = dt.day

    # (A) input[type=date] 있으면 최우선
    try:
        loc = page.locator('input[type="date"]')
        if loc.count() > 0:
            loc.first.fill(TARGET_DATE, timeout=3000)
            page.wait_for_timeout(300)
            assert_date_applied(page)
            return
    except:
        pass

    # (B) 날짜 버튼 열기
    opened = False
    for pat in [
        re.compile(r"\d+\s*월\s*\d+\s*일"),
        re.compile(r"날짜|일자|Date", re.IGNORECASE),
    ]:
        try:
            page.get_by_role("button", name=pat).first.click(timeout=3000)
            opened = True
            page.wait_for_timeout(250)
            break
        except:
            continue

    # (C) 달력에서 월 이동 시도(가능하면)
    # "다음/이전" 버튼이 있을 때만, 최대 12번
    if opened:
        for _ in range(12):
            try:
                # 현재 달 표시가 화면에 있으면 멈춤
                if page.get_by_text(re.compile(rf"\b{month}\s*월\b")).count() > 0:
                    break
            except:
                pass
            moved = False
            for pat in [
                re.compile(r"다음|Next", re.IGNORECASE),
                re.compile(r"›|»|→"),
            ]:
                try:
                    page.get_by_role("button", name=pat).first.click(timeout=1500)
                    page.wait_for_timeout(200)
                    moved = True
                    break
                except:
                    continue
            if not moved:
                break

    # (D) 날짜 클릭 (gridcell/button 둘 다 시도)
    clicked = False
    for role in ["gridcell", "button"]:
        try:
            page.get_by_role(role, name=re.compile(rf"^{day}$")).first.click(timeout=2500)
            clicked = True
            page.wait_for_timeout(250)
            break
        except:
            continue

    if not clicked:
        raise RuntimeError("DATE_CLICK_FAILED")

    # (E) 적용 검증
    assert_date_applied(page)

def run_monitor_once():
    label = f"{TARGET_DATE} / {PEOPLE}명 / {MIN_TIME_24}~"
    seen = load_seen()

    with sync_playwright() as p:
        browser = None
        context = None
        page = None

        def restart_browser():
            nonlocal browser, context, page
            try:
                if context:
                    context.close()
            except:
                pass
            try:
                if browser:
                    browser.close()
            except:
                pass

            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-features=site-per-process",
                ],
            )
            context = browser.new_context(locale="ko-KR")
            page = context.new_page()

        restart_browser()

        tg_send(f"✅ 감시 시작\n조건: {label}")

        last_heartbeat = 0
        loop_count = 0

        while True:
            loop_count += 1
            now = time.time()

            # 1시간마다 생존 알림
            if now - last_heartbeat >= HEARTBEAT_EVERY:
                tg_send(f"💓 감시 중(정상)\n조건: {label}")
                last_heartbeat = now

            # 주기적 강제 재시작(메모리 누적/크래시 방지)
            if loop_count % HARD_RESTART_EVERY_LOOPS == 0:
                restart_browser()

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
                # 날짜 적용 실패/페이지 크래시 포함 → 브라우저 재시작
                tg_send(f"⚠️ 에러: {type(e).__name__}: {e}\n(브라우저 재시작)")
                restart_browser()

            time.sleep(POLL_SECONDS)

def main_watchdog():
    if not TG_TOKEN or not TG_CHAT_ID:
        raise SystemExit("TG_TOKEN / TG_CHAT_ID 환경변수부터 설정해야 함")

    while True:
        try:
            run_monitor_once()
        except Exception as e:
            tb = traceback.format_exc(limit=2)
            tg_send(f"🚨 감시 중단됨(프로그램 크래시)\n{type(e).__name__}: {e}\n재시작함\n{tb}")
            time.sleep(RESTART_COOLDOWN)

if __name__ == "__main__":
    main_watchdog()
