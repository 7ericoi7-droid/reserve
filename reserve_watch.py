def try_set_date(page):
    dt = datetime.fromisoformat(TARGET_DATE)
    month = dt.month
    day = dt.day

    # 1) 날짜 칩/버튼(예: "3월 13일")이 있으면 그걸 최우선 클릭
    #    (UI가 달력 형태가 아닐 때 여기서 해결됨)
    patterns = [
        re.compile(rf"{month}\s*월\s*{day}\s*일"),  # "3월 13일"
        re.compile(rf"{month}\s*/\s*{day}"),       # "3/13"
        re.compile(rf"{month}\s*\.\s*{day}"),      # "3.13"
    ]
    for pat in patterns:
        # 버튼/칩 형태
        try:
            page.get_by_role("button", name=pat).first.click(timeout=3000)
            page.wait_for_timeout(300)
            return
        except:
            pass
        # 텍스트 클릭 가능한 경우
        try:
            page.get_by_text(pat).first.click(timeout=3000)
            page.wait_for_timeout(300)
            return
        except:
            pass

    # 2) 그래도 없으면 "날짜" 버튼 눌러서 달력 열기 시도
    opened = False
    for pat in [re.compile(r"\d+\s*월\s*\d+\s*일"), re.compile(r"날짜|일자|Date", re.IGNORECASE)]:
        try:
            page.get_by_role("button", name=pat).first.click(timeout=3000)
            opened = True
            page.wait_for_timeout(300)
            break
        except:
            pass

    # 3) 달력에서 13 클릭 시도
    if opened:
        for role in ["gridcell", "button"]:
            try:
                page.get_by_role(role, name=re.compile(rf"^{day}$")).first.click(timeout=3000)
                page.wait_for_timeout(300)
                return
            except:
                pass

    raise RuntimeError("DATE_CLICK_FAILED")


