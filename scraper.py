import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# 환경변수에서 텔레그램 정보 로드
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HISTORY_FILE = "sent_history.json"

# 필터링할 핵심 키워드 (제목에 하나라도 포함되면 추출)
TARGET_KEYWORDS = ["설문", "만족도", "이벤트", "의견수렴", "퀴즈"]
REWARD_KEYWORDS = ["커피", "아메리카노", "스타벅스", "기프티콘", "상품권", "쿠폰", "추첨", "경품"]

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_history(history):
    # 최근 500개 기록만 유지하여 파일 크기 관리
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-500:], f, ensure_ascii=False, indent=2)

def is_target_event(title):
    has_target = any(k in title for k in TARGET_KEYWORDS)
    has_reward = any(k in title for k in REWARD_KEYWORDS)
    # 설문/이벤트 성격이면서 경품 키워드가 있거나, 공공기관 설문인 경우
    return has_target and has_reward

def fetch_epeople_surveys():
    """국민생각함(설문/이벤트) 수집"""
    events = []
    url = "https://www.epeople.go.kr/nep/thnk/thnkList.npaid"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        # 게시판 목록 파싱 (사이트 구조 변경 시 선택자 수정 필요)
        items = soup.select(".board_list tbody tr")
        for item in items:
            title_el = item.select_one("td.title a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            link = f"https://www.epeople.go.kr{href}" if href.startswith("/") else href
            
            if "설문" in title or is_target_event(title):
                events.append({
                    "id": f"epeople_{title[:20]}",
                    "source": "국민생각함",
                    "title": title,
                    "link": link
                })
    except Exception as e:
        print(f"국민생각함 수집 오류: {e}")
    return events

def fetch_community_events():
    """뽐뿌 이벤트/쿠폰 게시판 등 추가 타겟 수집 예시"""
    events = []
    url = "https://www.ppomppu.co.kr/zboard/zboard.php?id=event"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")
        rows = soup.select("tr.baseList")
        for row in rows:
            title_el = row.select_one("a.baseList-title")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            link = f"https://www.ppomppu.co.kr/zboard/{href}"
            
            if is_target_event(title):
                events.append({
                    "id": f"ppomppu_{href.split('no=')[-1]}",
                    "source": "이벤트게시판",
                    "title": title,
                    "link": link
                })
    except Exception as e:
        print(f"커뮤니티 수집 오류: {e}")
    return events

def send_telegram_message(new_events):
    if not BOT_TOKEN or not CHAT_ID:
        print("텔레그램 환경변수가 설정되지 않았습니다.")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    if not new_events:
        # 새로운 이벤트가 없을 때는 메시지를 안 보내거나 간단히 알림 (불필요한 알림 방지를 위해 생략 권장)
        print("오늘 새로 수집된 타겟 이벤트가 없습니다.")
        return

    lines = [f"☕ <b>[{today_str}] 오늘의 설문·커피 이벤트 ({len(new_events)}건)</b>\n"]
    for idx, ev in enumerate(new_events, 1):
        lines.append(f"{idx}. <b>[{ev['source']}]</b> {ev['title']}\n👉 <a href='{ev['link']}'>바로 응모하기</a>\n")

    message = "\n".join(lines)
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    res = requests.post(api_url, json=payload)
    print("텔레그램 전송 결과:", res.status_code)

def main():
    history = load_history()
    all_events = fetch_epeople_surveys() + fetch_community_events()
    
    new_events = []
    for ev in all_events:
        if ev["id"] not in history:
            new_events.append(ev)
            history.append(ev["id"])

    send_telegram_message(new_events)
    save_history(history)

if __name__ == "__main__":
    main()
