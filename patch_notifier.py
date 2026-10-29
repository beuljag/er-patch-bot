"""이터널 리턴 패치노트 → 디스코드 자동 알림.

새 패치노트가 올라오면 디스코드 웹훅으로 임베드 메시지를 보낸다.
소스:
  - official : 공식 홈페이지 패치노트 게시판 (한국어)
  - steam    : Steam 뉴스 API (앱 1049590) 중 제목에 'patch'/'패치'가 들어간 공지

사용법:
  python patch_notifier.py            # 계속 실행하며 주기적으로 확인
  python patch_notifier.py --once     # 한 번만 확인하고 종료 (작업 스케줄러/크론용)
  python patch_notifier.py --test     # 디스코드 전송 없이 현재 감지되는 글 목록만 출력
  python patch_notifier.py --send-latest  # 가장 최근 패치노트 1개를 지금 바로 전송 (웹훅 테스트)
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import patch_summary

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"

OFFICIAL_LIST_URL = "https://playeternalreturn.com/posts/news?categoryPath=patchnote&hl=ko-KR"
OFFICIAL_POST_URL = "https://playeternalreturn.com/posts/news/{id}?hl=ko-KR"
STEAM_APP_ID = 1049590
STEAM_NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
TIMEOUT = 20

DEFAULT_CONFIG = {
    "webhook_url": "",
    "sources": ["official"],
    "check_interval_minutes": 10,
    "mention": "",  # 예: "@everyone" 또는 "<@&역할ID>"
}


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_config():
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    # 환경변수가 있으면 우선 (서버/GitHub Actions 배포용)
    if os.environ.get("DISCORD_WEBHOOK_URL"):
        config["webhook_url"] = os.environ["DISCORD_WEBHOOK_URL"]
    if os.environ.get("PATCH_SOURCES"):  # 예: "steam" 또는 "official,steam"
        config["sources"] = [s.strip() for s in os.environ["PATCH_SOURCES"].split(",") if s.strip()]
    if os.environ.get("DISCORD_MENTION"):
        config["mention"] = os.environ["DISCORD_MENTION"]
    return config


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 소스: 공식 홈페이지

def _meta(html, prop):
    m = re.search(
        rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]+content=["\']([^"\']*)["\']', html
    ) or re.search(
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{prop}["\']', html
    )
    return m.group(1).strip() if m else None


def fetch_official():
    """패치노트 게시판에서 글 ID를 모아 최신순으로 반환."""
    html = requests.get(OFFICIAL_LIST_URL, headers=HEADERS, timeout=TIMEOUT).text
    ids = sorted({int(x) for x in re.findall(r"/posts/news/(\d+)", html)}, reverse=True)
    posts = []
    for post_id in ids[:5]:
        url = OFFICIAL_POST_URL.format(id=post_id)
        title, image, page = None, None, None
        try:
            page = requests.get(url, headers=HEADERS, timeout=TIMEOUT).text
            title = _meta(page, "og:title")
            image = _meta(page, "og:image")
        except requests.RequestException:
            pass
        posts.append({
            "id": f"official:{post_id}",
            "title": title or f"패치노트 #{post_id}",
            "url": url.replace("?hl=ko-KR", ""),
            "image": image,
            "date": None,
            "source": "공식 홈페이지",
            "html": page,
        })
    return posts


# ---------------------------------------------------------------- 소스: Steam

def fetch_steam():
    params = {
        "appid": STEAM_APP_ID,
        "count": 20,
        "maxlength": 300,
        "feeds": "steam_community_announcements",
        "format": "json",
    }
    data = requests.get(STEAM_NEWS_URL, params=params, headers=HEADERS, timeout=TIMEOUT).json()
    posts = []
    for item in data.get("appnews", {}).get("newsitems", []):
        title = item.get("title", "")
        if not re.search(r"patch|패치", title, re.IGNORECASE):
            continue
        img = re.search(r"\{STEAM_CLAN_IMAGE\}(/[^\s\[\]\"]+\.(?:png|jpg|gif))", item.get("contents", ""))
        posts.append({
            "id": f"steam:{item['gid']}",
            "title": title,
            "url": f"https://store.steampowered.com/news/app/{STEAM_APP_ID}/view/{item['gid']}",
            "image": f"https://clan.fastly.steamstatic.com/images{img.group(1)}" if img else None,
            "date": item.get("date"),
            "source": "Steam",
        })
    return posts


SOURCES = {"official": fetch_official, "steam": fetch_steam}


# ---------------------------------------------------------------- 디스코드 전송

def _post_webhook(webhook_url, payload):
    for _ in range(3):
        r = requests.post(webhook_url, json=payload, timeout=TIMEOUT)
        if r.status_code == 429:  # 레이트 리밋
            time.sleep(float(r.json().get("retry_after", 2)))
            continue
        r.raise_for_status()
        return
    raise RuntimeError("디스코드 전송 실패 (레이트 리밋)")


def summary_messages(post):
    """공식 패치노트면 마크다운 요약 메시지 목록, 아니면 None."""
    if not post.get("html"):
        return None
    try:
        lines = patch_summary.summarize(post["html"])
    except Exception as e:
        log(f"요약 실패, 기본 알림으로 전송: {e}")
        return None
    if not lines or len(lines) <= 1:
        return None
    return patch_summary.to_messages(lines, f"[공식 패치노트 원문](<{post['url']}>)")


def send_to_discord(webhook_url, post, mention=""):
    messages = summary_messages(post)
    if messages:
        for i, text in enumerate(messages):
            payload = {"username": "이터널 리턴 패치 알림", "content": text,
                       "allowed_mentions": {"parse": []}}
            if i == 0 and mention:
                payload["content"] = f"{mention}\n{text}"
                payload["allowed_mentions"] = {"parse": ["everyone", "roles", "users"]}
            _post_webhook(webhook_url, payload)
            time.sleep(1)
        return

    embed = {
        "title": post["title"][:256],
        "url": post["url"],
        "color": 0x6C3BD1,
        "author": {"name": "이터널 리턴 패치노트"},
        "footer": {"text": f"출처: {post['source']}"},
    }
    if post.get("image"):
        embed["image"] = {"url": post["image"]}
    if post.get("date"):
        embed["timestamp"] = datetime.fromtimestamp(post["date"], timezone.utc).isoformat()

    payload = {"username": "이터널 리턴 패치 알림", "embeds": [embed]}
    if mention:
        payload["content"] = mention
        payload["allowed_mentions"] = {"parse": ["everyone", "roles", "users"]}
    _post_webhook(webhook_url, payload)


# ---------------------------------------------------------------- 메인 로직

def collect(config):
    results = {}
    for name in config["sources"]:
        try:
            results[name] = SOURCES[name]()
        except Exception as e:  # 한 소스가 죽어도 다른 소스는 계속
            log(f"[{name}] 가져오기 실패: {e}")
            results[name] = None
    return results


def check_once(config, state):
    for name, posts in collect(config).items():
        if posts is None:
            continue
        seen = set(state.get(name, []))
        first_run = name not in state
        new_posts = [p for p in posts if p["id"] not in seen]

        if first_run:
            # 처음 실행 시 기존 글로 도배하지 않도록 '읽음' 처리만
            log(f"[{name}] 첫 실행: 기존 글 {len(posts)}개를 기준점으로 저장")
        else:
            for post in reversed(new_posts):  # 오래된 것부터 전송
                send_to_discord(config["webhook_url"], post, config.get("mention", ""))
                log(f"[{name}] 전송: {post['title']}")

        state[name] = sorted(seen | {p["id"] for p in posts})
        save_state(state)


def main():
    parser = argparse.ArgumentParser(description="이터널 리턴 패치노트 디스코드 알림")
    parser.add_argument("--once", action="store_true", help="한 번만 확인하고 종료")
    parser.add_argument("--test", action="store_true", help="전송 없이 감지된 글만 출력")
    parser.add_argument("--send-latest", action="store_true", help="최신 패치노트 1개 즉시 전송")
    args = parser.parse_args()

    config = load_config()

    if args.test:
        for name, posts in collect(config).items():
            print(f"\n=== {name} ===")
            if posts is None:
                print("  (실패)")
            elif not posts:
                print("  (패치노트를 찾지 못함)")
            for p in posts or []:
                print(f"  {p['title']}\n    {p['url']}")
            if posts and summary_messages(posts[0]):
                print("\n--- 최신 글 요약 미리보기 ---")
                print("\n\n".join(summary_messages(posts[0])))
        return

    if not config["webhook_url"]:
        sys.exit("config.json 에 webhook_url 을 넣어주세요. (README.md 참고)")

    if args.send_latest:
        for posts in collect(config).values():
            if posts:
                send_to_discord(config["webhook_url"], posts[0], config.get("mention", ""))
                log(f"전송: {posts[0]['title']}")
                return
        sys.exit("가져온 패치노트가 없습니다.")

    state = load_state()
    if args.once:
        check_once(config, state)
        return

    interval = max(1, int(config["check_interval_minutes"])) * 60
    log(f"감시 시작 (소스: {', '.join(config['sources'])}, {interval // 60}분 간격)")
    while True:
        try:
            check_once(config, state)
        except Exception as e:
            log(f"오류: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
