import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

# 한국 표준시(KST) 설정
KST = timezone(timedelta(hours=9))

# 환경변수 로드
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET")

HISTORY_FILE = "sent_history.json"
EVENTS_DATA_FILE = "events_data.json"
DASHBOARD_URL = "https://autoeventcollector.zinzerman01.workers.dev"

# 연도 및 키워드 필터 설정
CURRENT_YEAR = datetime.now(KST).year
PAST_YEARS = [str(CURRENT_YEAR - 1), str(CURRENT_YEAR - 2)]

SURVEY_KEYWORDS = ["설문", "만족도", "의견수렴", "투표", "퀴즈", "이벤트"]
REWARD_KEYWORDS = ["커피", "아메리카노", "스타벅스", "기프티콘", "모바일상품권", "상품권", "쿠폰", "추첨", "경품", "음료"]
PUBLIC_KEYWORDS = ["구청", "시청", "군청", "도청", "공사", "공단", "진흥원", "연구원", "교육청", "보건소", "센터", "재단", "협회", "정부", "지자체", "공공"]
EXCLUDE_KEYWORDS = [
    "마감", "종료", "당첨자 발표", "당첨자발표", "후기", "리뷰",
    "기자", "보도자료", "뉴스", "신문", "일보", "방송", "언론"
] + PAST_YEARS

def clean_html(text):
    return re.sub(r"<[^>]+>", "", text).replace("&quot;", '"').replace("&amp;", "&").strip()

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-1000:], f, ensure_ascii=False, indent=2)

def update_events_json(new_events):
    """웹 대시보드용 events_data.json 업데이트"""
    existing = []
    if os.path.exists(EVENTS_DATA_FILE):
        with open(EVENTS_DATA_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)

    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    for ev in new_events:
        ev["date"] = today_str

    # 신규 이벤트를 최상단에 추가하고 최근 300개 유지
    merged = new_events + existing
    with open(EVENTS_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(merged[:300], f, ensure_ascii=False, indent=2)

def is_valid_event(text, require_public=False):
    if any(ex in text for ex in EXCLUDE_KEYWORDS):
        return False
    has_survey = any(k in text for k in SURVEY_KEYWORDS)
    has_reward = any(k in text for k in REWARD_KEYWORDS)
    if require_public:
        has_public = any(k in text for k in PUBLIC_KEYWORDS)
        return has_survey and has_reward and has_public
    return has_survey and has_reward

# 1. 네이버 검색 API (블로그 최신순 + 최근 7일 이내 글만 필터링)
def fetch_naver_api():
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        print("네이버 API 키가 설정되지 않았습니다.")
        return []

    events = []
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    queries = [
        "공공기관 설문조사 커피",
        "만족도 조사 아메리카노 기프티콘",
        "지자체 설문 이벤트 스타벅스",
        "공단 설문조사 추첨 쿠폰"
    ]
    url = "https://openapi.naver.com/v1/search/blog.json"
    today = datetime.now(KST).date()

    for q in queries:
        params = {"query": q, "display": 20, "sort": "date"}
        try:
            res = requests.get(url, headers=headers, params=params, timeout=10)
            if res.status_code != 200:
                continue
            for item in res.json().get("items", []):
                # 작성일(YYYYMMDD) 체크: 최근 7일 이내 글만 통과
                postdate_str = item.get("postdate", "")
                if len(postdate_str) == 8:
                    post_date = datetime.strptime(postdate_str, "%Y%m%d").date()
                    if (today - post_date).days > 7:
                        continue

                title = clean_html(item.get("title", ""))
                desc = clean_html(item.get("description", ""))
                link = item.get("link", "")
                combined = f"{title} {desc}"

                if is_valid_event(combined, require_public=True):
                    events.append({
                        "id": f"naver_{link}",
                        "source": "네이버블로그",
                        "title": title,
                        "link": link
                    })
        except Exception as e:
            print(f"네이버 API({q}) 오류: {e}")
    return events

# 2. 국민생각함 & 소통24 (Playwright 동적 렌더링 수집)
def fetch_public_portals():
    events = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

        # ① 국민생각함 (진행중인 설문)
        try:
            page.goto("https://www.epeople.go.kr/nep/thnk/thnkList.npaid", timeout=20000)
            page.wait_for_timeout(2000)
            links = page.locator("a").all()
            for el in links:
                title = (el.inner_text() or "").strip()
                href = el.get_attribute("href") or ""
                if len(title) >= 8 and ("설문" in title or "만족도" in title or "이벤트" in title):
                    if any(ex in title for ex in EXCLUDE_KEYWORDS):
                        continue
                    full_link = f"https://www.epeople.go.kr{href}" if href.startswith("/") else "https://www.epeople.go.kr/nep/thnk/thnkList.npaid"
                    events.append({
                        "id": f"epeople_{title[:25]}",
                        "source": "국민생각함",
                        "title": title,
                        "link": full_link
                    })
        except Exception as e:
            print(f"국민생각함 수집 오류: {e}")

        # ② 소통24 (정책참여/설문)
        try:
            page.goto("https://sotong.go.kr/front/propse/propseList.do", timeout=20000)
            page.wait_for_timeout(2500)
            links = page.locator("a").all()
            for el in links:
                title = (el.inner_text() or "").strip()
                href = el.get_attribute("href") or ""
                if len(title) >= 8 and any(k in title for k in SURVEY_KEYWORDS):
                    if any(ex in title for ex in EXCLUDE_KEYWORDS):
                        continue
                    full_link = f"https://sotong.go.kr{href}" if href.startswith("/") else "https://sotong.go.kr/front/propse/propseList.do"
                    events.append({
                        "id": f"sotong_{title[:25]}",
                        "source": "소통24",
                        "title": title,
                        "link": full_link
                    })
        except Exception as e:
            print(f"소통24 수집 오류: {e}")

        browser.close()
    return events

# 3. 커뮤니티 보완 (뽐뿌 이벤트 게시판)
def fetch_ppomppu():
    events = []
    url = "https://www.ppomppu.co.kr/zboard/zboard.php?id=event"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")
        for row in soup.select("tr.baseList"):
            title_el = row.select_one("a.baseList-title")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if is_valid_event(title, require_public=False):
                events.append({
                    "id": f"ppomppu_{href.split('no=')[-1]}",
                    "source": "뽐뿌이벤트",
                    "title": title,
                    "link": f"https://www.ppomppu.co.kr/zboard/{href}"
                })
    except Exception as e:
        print(f"뽐뿌 수집 오류: {e}")
    return events

# 텔레그램 전송 (4,000자 초과 시 자동 분할 발송 + 하단 대시보드 버튼)
def send_telegram_messages(new_events):
    if not BOT_TOKEN or not CHAT_ID:
        print("텔레그램 설정 누락")
        return
    if not new_events:
        print("신규 이벤트 없음")
        return

    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    header = f"☕ <b>[{today_str}] 공공기관·커피 설문 이벤트 ({len(new_events)}건)</b>\n\n"

    chunks = []
    current_msg = header
    for idx, ev in enumerate(new_events, 1):
        entry = f"{idx}. <b>[{ev['source']}]</b> {ev['title']}\n👉 <a href='{ev['link']}'>바로 응모하기</a>\n\n"
        if len(current_msg) + len(entry) > 3800:
            chunks.append(current_msg)
            current_msg = entry
        else:
            current_msg += entry
    chunks.append(current_msg)

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        # 마지막 메시지 하단에 대시보드 바로가기 인라인 버튼 추가
        if i == len(chunks) - 1 and DASHBOARD_URL:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": "📊 대시보드 열기", "url": DASHBOARD_URL}]
                ]
            }

        res = requests.post(api_url, json=payload)
        print("텔레그램 전송 상태:", res.status_code)

def main():
    history = load_history()
    collected = fetch_public_portals() + fetch_naver_api() + fetch_ppomppu()

    # 이번 실행 내 중복 제거 + 과거 발송 이력 중복 제거
    seen_ids = set(history)
    new_events = []
    for ev in collected:
        if ev["id"] not in seen_ids:
            seen_ids.add(ev["id"])
            new_events.append(ev)
            history.append(ev["id"])

    send_telegram_messages(new_events)
    update_events_json(new_events)
    save_history(history)

if __name__ == "__main__":
    main()
