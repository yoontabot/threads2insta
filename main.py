"""Threads 새 글 -> 교재 지면 카드 이미지 -> 인스타그램 피드 자동 게시."""
import html, json, os, re, subprocess, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

ROOT = Path(__file__).parent
STATE = ROOT / "state" / "posted.json"
CARDS = ROOT / "cards"
KST = timezone(timedelta(hours=9))

TH_TOKEN = os.environ["THREADS_TOKEN"]
IG_TOKEN = os.environ["IG_TOKEN"]
IG_USER_ID = os.environ["IG_USER_ID"]
REPO = os.environ.get("GITHUB_REPOSITORY", "")      # owner/repo (공개 저장소)
BRANCH = os.environ.get("BRANCH", "main")
BRAND = os.environ.get("CARD_BRAND", "윤타의 투자노트")
HANDLE = os.environ.get("CARD_HANDLE", "@yoonta")
HASHTAGS = os.environ.get("HASHTAGS", "")
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN") or "3")
CHARS_PER_PAGE = 230
TH = "https://graph.threads.com/v1.0"
IG = "https://graph.facebook.com/v21.0"   # 통합 로그인(EAA 시스템 사용자 토큰, 만료 없음)


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else None


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1))


# ---------- 토큰 갱신 (60일 만료 방지) ----------
def refresh_tokens():
    out = {}
    r = requests.get("https://graph.threads.com/refresh_access_token",
                     params={"grant_type": "th_refresh_token", "access_token": TH_TOKEN}, timeout=30)
    if r.ok: out["THREADS_TOKEN"] = r.json()["access_token"]
    for name, val in out.items():
        if val != os.environ[name] and os.environ.get("GH_TOKEN"):
            subprocess.run(["gh", "secret", "set", name, "--body", val, "-R", REPO], check=False)


# ---------- Threads ----------
def fetch_threads():
    r = requests.get(f"{TH}/me/threads", params={
        "fields": "id,media_type,media_url,text,timestamp,permalink,is_quote_post",
        "limit": 25, "access_token": TH_TOKEN}, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])


def thread_children(tid):
    r = requests.get(f"{TH}/{tid}", params={
        "fields": "children{media_type,media_url}", "access_token": TH_TOKEN}, timeout=30)
    r.raise_for_status()
    return r.json().get("children", {}).get("data", [])


# ---------- 카드 이미지 ----------
def split_pages(text):
    paras, pages, cur = [p.strip() for p in text.split("\n") if p.strip()], [], ""
    for p in paras:
        while len(p) > CHARS_PER_PAGE:                      # 아주 긴 문단은 자름
            cut = p.rfind(" ", 0, CHARS_PER_PAGE) or CHARS_PER_PAGE
            if cur: pages.append(cur); cur = ""
            pages.append(p[:cut]); p = p[cut:].strip()
        if len(cur) + len(p) > CHARS_PER_PAGE and cur:
            pages.append(cur); cur = p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur: pages.append(cur)
    return pages[:10] or [" "]                              # 캐러셀 최대 10장


def render_cards(post):
    from playwright.sync_api import sync_playwright
    text = (post.get("text") or "").strip()
    first, _, rest = text.partition("\n")
    if len(first) > 60:                                      # 첫 문장이 길면 문장 단위로
        m = re.match(r"(.{10,60}?[.!?。])\s*(.*)", text, re.S)
        first, rest = (m.group(1), m.group(2)) if m else (first, rest)
    pages = split_pages(rest) if rest.strip() else [""]
    tpl = (ROOT / "card.html").read_text()
    date = datetime.fromisoformat(post["timestamp"].replace("+0000", "+00:00")).astimezone(KST)
    files = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1080, "height": 1350})
        for i, body in enumerate(pages, 1):
            h = (tpl.replace("{{BRAND}}", html.escape(BRAND))
                    .replace("{{DATE}}", date.strftime("%Y.%m.%d"))
                    .replace("{{LEAD}}", f'<div class="lead"><mark>{html.escape(first)}</mark></div>' if i == 1 else "")
                    .replace("{{BODY}}", html.escape(body))
                    .replace("{{PEN}}", '<div class="pen"></div>' if i == len(pages) else "")
                    .replace("{{HANDLE}}", html.escape(HANDLE))
                    .replace("{{PAGENO}}", f"{i} / {len(pages)}" if len(pages) > 1 else ""))
            pg.set_content(h, wait_until="networkidle")
            f = CARDS / f"{post['id']}_{i}.jpg"
            pg.screenshot(path=str(f), type="jpeg", quality=92)
            files.append(f)
        b.close()
    return files


def publish_files_to_repo(files):
    """이미지를 공개 저장소에 올려 인스타가 가져갈 수 있는 URL을 만든다."""
    subprocess.run(["git", "add", *map(str, files)], check=True)
    subprocess.run(["git", "commit", "-m", "cards"], check=True)
    subprocess.run(["git", "push"], check=True)
    time.sleep(5)
    return [f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/cards/{f.name}" for f in files]


def fit_ratio(urls, pid):
    """인스타 허용 비율(4:5 ~ 1.91:1)을 벗어난 이미지는 종이색 여백을 붙여 맞춘다."""
    from io import BytesIO
    from PIL import Image
    out, files = [], []
    for i, u in enumerate(urls, 1):
        im = Image.open(BytesIO(requests.get(u, timeout=60).content)).convert("RGB")
        w, h = im.size; r = w / h
        if 0.8 <= r <= 1.91:
            out.append(u); continue
        W, H = (w, round(w / 0.8)) if r < 0.8 else (w, round(w / 1.91))
        if r < 0.8 and H < h: W, H = round(h * 0.8), h
        bg = Image.new("RGB", (W, H), (251, 247, 236))
        bg.paste(im, ((W - w) // 2, (H - h) // 2))
        f = CARDS / f"{pid}_fit{i}.jpg"; bg.save(f, quality=92)
        files.append(f); out.append(None)
    if files:
        pub = iter(publish_files_to_repo(files))
        out = [u if u else next(pub) for u in out]
    return out


# ---------- Instagram ----------
def ig_post(path, **data):
    r = requests.post(f"{IG}/{path}", data={**data, "access_token": IG_TOKEN}, timeout=60)
    if not r.ok: raise RuntimeError(f"IG {path}: {r.text}")
    return r.json()["id"]


def wait_ready(cid):
    for _ in range(30):
        r = requests.get(f"{IG}/{cid}", params={"fields": "status_code", "access_token": IG_TOKEN}, timeout=30).json()
        if r.get("status_code") == "FINISHED": return
        if r.get("status_code") == "ERROR": raise RuntimeError(f"container error {cid}")
        time.sleep(4)
    raise RuntimeError(f"container timeout {cid}")


def publish_ig(urls, caption):
    if len(urls) == 1:
        cid = ig_post(f"{IG_USER_ID}/media", image_url=urls[0], caption=caption)
    else:
        kids = [ig_post(f"{IG_USER_ID}/media", image_url=u, is_carousel_item="true") for u in urls]
        for k in kids: wait_ready(k)
        cid = ig_post(f"{IG_USER_ID}/media", media_type="CAROUSEL", children=",".join(kids), caption=caption)
    wait_ready(cid)
    return ig_post(f"{IG_USER_ID}/media_publish", creation_id=cid)


def make_caption(text):
    cap = (text or "").strip()
    if HASHTAGS: cap = f"{cap}\n\n{HASHTAGS}"
    return cap[:2200]


# ---------- 실행 기록 (점검용, 공개 저장소이므로 토큰은 반드시 가림) ----------
RUNLOG = ROOT / "state" / "run_log.json"
RESULT = {"posted": [], "failed": [], "skipped": [], "error": None}


def safe(msg):
    msg = re.sub(r"access_token=[^&\s\"']+", "access_token=***", str(msg))
    msg = re.sub(r"\b(EAA|TH|IG|github_pat_)[A-Za-z0-9_\-]{20,}", "***", msg)
    for v in (TH_TOKEN, IG_TOKEN):
        if v: msg = msg.replace(v, "***")
    return msg[:300]


def write_runlog():
    log = json.loads(RUNLOG.read_text()) if RUNLOG.exists() else []
    log.append({"time": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), **RESULT})
    RUNLOG.write_text(json.dumps(log[-72:], ensure_ascii=False, indent=1))


# ---------- 메인 ----------
def main():
    refresh_tokens()
    posts = [p for p in fetch_threads() if p.get("media_type") != "REPOST_FACADE"]
    state = load_state()
    if os.environ.get("FORCE_LATEST") == "1" and posts:   # 베타테스트: 최신 글 1개 즉시 게시
        latest = max(posts, key=lambda p: p["timestamp"])["id"]
        prev = state or {"posted": [], "skipped": []}
        others = {p["id"] for p in posts} | set(prev["posted"]) | set(prev["skipped"])
        state = {"posted": sorted(others - {latest}), "skipped": []}
        save_state(state)
    if os.environ.get("POST_TODAY") == "1" and state is not None:   # 오늘(KST) 올린 글 전부 다시 대상으로
        today = datetime.now(KST).date()
        def kst_date(p): return datetime.strptime(p["timestamp"], "%Y-%m-%dT%H:%M:%S%z").astimezone(KST).date()
        pub = set(state.get("published", []))
        todays = {p["id"] for p in posts if kst_date(p) == today and p["timestamp"] not in pub}
        state["posted"] = [i for i in state["posted"] if i not in todays]
        state["skipped"] = [i for i in state["skipped"] if i not in todays]
        print(f"오늘 글 {len(todays)}개를 게시 대상으로 설정")
    if state is None:                     # 첫 실행: 기존 글은 건너뛰고 지금부터 올라오는 글만
        save_state({"posted": [p["id"] for p in posts], "skipped": []})
        print(f"초기화 완료: 기존 글 {len(posts)}개는 건너뜀")
        return
    done = set(state["posted"]) | set(state["skipped"])
    new = sorted([p for p in posts if p["id"] not in done], key=lambda p: p["timestamp"])[:MAX_PER_RUN]
    for p in new:
        mt = p.get("media_type")
        try:
            if mt == "TEXT_POST":
                urls = publish_files_to_repo(render_cards(p))
            elif mt == "IMAGE":
                urls = fit_ratio([p["media_url"]], p["id"])
            elif mt == "CAROUSEL_ALBUM":
                urls = [c["media_url"] for c in thread_children(p["id"]) if c.get("media_type") == "IMAGE"][:10]
                urls = fit_ratio(urls, p["id"])
            else:                         # 영상 등은 현재 미지원
                state["skipped"].append(p["id"]); RESULT["skipped"].append(p.get("permalink")); print(f"건너뜀({mt}): {p.get('permalink')}"); continue
            mid = publish_ig(urls, make_caption(p.get("text")))
            state["posted"].append(p["id"])
            state.setdefault("published", []).append(p["timestamp"])
            RESULT["posted"].append(p.get("permalink"))
            print(f"게시 완료: {p.get('permalink')} -> IG {mid}")
        except Exception as e:
            RESULT["failed"].append(f"{p.get('permalink')} :: {safe(e)}")
            print(f"실패: {p.get('permalink')} :: {safe(e)}", file=sys.stderr)
        save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        RESULT["error"] = safe(e)
        raise
    finally:
        write_runlog()
