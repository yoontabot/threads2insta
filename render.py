"""텍스트형 카드 (교재 지면 스타일, 1080x1350)
- 글 슬라이드: Threads 원문 그대로. 짧으면 크게, 길면 여러 장.
- 사진 슬라이드: 첨부 이미지를 잘리지 않게 액자(만화컷) 안에 넣음.
"""
import base64, html, re
from datetime import datetime

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{width:1080px;height:1350px;overflow:hidden;background:#FBF7EC;
     font-family:"Noto Sans CJK KR","Noto Sans KR",sans-serif;color:#161616}
.page{position:relative;width:1080px;height:1350px;padding:72px 86px 0;display:flex;flex-direction:column}
.top{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:3px solid #111;padding-bottom:16px}
.brand{font-family:"Noto Serif CJK KR",serif;font-weight:900;font-size:34px}
.meta{font-size:24px;color:#666}
.main{flex:1;display:flex;flex-direction:column;justify-content:center;min-height:0}
.lead{font-family:"Noto Serif CJK KR",serif;font-weight:900;line-height:1.42;letter-spacing:-1px;word-break:keep-all;white-space:pre-wrap}
.lead span{background:linear-gradient(transparent 56%,#FFE24A 56% 92%,transparent 92%);padding:0 4px;
           -webkit-box-decoration-break:clone;box-decoration-break:clone}
.body{margin-top:34px;line-height:1.68;word-break:keep-all;white-space:pre-wrap;color:#222}
.cont{font-size:30px;line-height:1.7;word-break:keep-all;white-space:pre-wrap;color:#222}
.frame{position:relative;align-self:center;border:4px solid #111;background:#fff;box-shadow:12px 12px 0 #111;display:flex}
.frame img{display:block;max-width:892px;object-fit:contain}
.label{align-self:flex-start;margin-bottom:28px;font-size:26px;font-weight:700;color:#D7261E;
       border:2.5px solid #D7261E;border-radius:30px;padding:4px 16px;transform:rotate(-2deg)}
.foot{height:100px;border-top:1px solid #111;display:flex;justify-content:space-between;align-items:center;
      font-size:24px;color:#444;margin-top:30px}
.foot b{color:#1D4FD8}
"""


def _page(inner, brand, date, handle, pageno):
    return (f'<html><head><meta charset="utf-8"><style>{CSS}</style></head><body><div class="page">'
            f'<div class="top"><div class="brand">{html.escape(brand)}</div><div class="meta">{date}</div></div>'
            f'<div class="main">{inner}</div>'
            f'<div class="foot"><span>{html.escape(handle)}</span><b>{pageno}</b></div></div></body></html>')


def split_text(text, first=150, rest=380):
    """문단 단위로 슬라이드 분할. 첫 장은 짧게(크게 보이도록)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    pages, cur, limit = [], "", first
    for p in paras:
        while len(p) > limit:
            cut = p.rfind(" ", 0, limit)
            cut = cut if cut > limit * 0.5 else limit
            if cur: pages.append(cur); cur = ""; limit = rest
            pages.append(p[:cut].strip()); p = p[cut:].strip(); limit = rest
        if cur and len(cur) + len(p) > limit:
            pages.append(cur); cur = p; limit = rest
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur: pages.append(cur)
    return pages


def _text_inner(chunk, is_first):
    n = len(chunk)
    if is_first:
        lines = chunk.split("\n", 1)
        lead, body = (lines[0], lines[1] if len(lines) > 1 else "")
        if len(lead) > 70:                      # 첫 줄이 너무 길면 전부 본문 취급
            lead, body = "", chunk
        ls = 120 if len(lead) <= 8 else 80 if len(lead) <= 20 else 66 if len(lead) <= 40 else 56
        bs = 46 if len(body) <= 100 else 41
        out = f'<div class="lead" style="font-size:{ls}px"><span>{html.escape(lead)}</span></div>' if lead else ""
        if body:
            out += f'<div class="body" style="font-size:{bs}px{";margin-top:0" if not lead else ""}">{html.escape(body)}</div>'
        return out
    fs = 44 if n <= 200 else 40 if n <= 300 else 36
    return f'<div class="cont" style="font-size:{fs}px">{html.escape(chunk)}</div>'


def _img_inner(img_bytes, idx, total, note=""):
    b64 = base64.b64encode(img_bytes).decode()
    lab = html.escape(note) if note else (f"첨부 이미지 {idx}/{total}" if total > 1 else "첨부 이미지")
    return (f'<div class="label">{lab}</div>'
            f'<div class="frame"><img src="data:image/jpeg;base64,{b64}" style="max-height:900px"></div>')


def render_post(text, images, out_prefix, when, brand="윤타의 투자노트", handle="@yoonta.official"):
    """text: Threads 원문, images: [bytes], 반환: 생성된 jpg 경로 목록 (최대 10장)"""
    from playwright.sync_api import sync_playwright
    date = when.strftime("%Y.%m.%d")
    t = (text or "").strip()
    note = ""
    if images and len(t) <= 15:               # 아주 짧은 글 + 사진: 글은 사진 위 표식으로
        note, t = t, ""
    chunks = split_text(t) if t else []
    slides = [_text_inner(c, i == 0) for i, c in enumerate(chunks)]
    room = 10 - len(slides)
    if room < len(images):                     # 사진이 많으면 글 장수를 줄이지 않고 사진을 자름
        images = images[:max(room, 1)]
        slides = slides[:10 - len(images)]
    slides += [_img_inner(b, i + 1, len(images), note if i == 0 else "") for i, b in enumerate(images)]
    files = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": 1080, "height": 1350})
        for i, inner in enumerate(slides, 1):
            pno = f"{i} / {len(slides)}" if len(slides) > 1 else ""
            pg.set_content(_page(inner, brand, date, handle, pno))
            pg.wait_for_timeout(250)
            # 사진 액자: 남은 높이에 맞춤
            pg.evaluate("""()=>{const m=document.querySelector('.main'),im=document.querySelector('.frame img');
                if(im){const lab=document.querySelector('.label');
                  im.style.maxHeight=(m.clientHeight-(lab?lab.offsetHeight+28:0)-24)+'px';}}""")
            # 글이 넘치면 글자 크기를 줄임
            pg.evaluate("""()=>{const m=document.querySelector('.main');
                for(let k=0;k<12&&m.scrollHeight>m.clientHeight;k++){
                  m.querySelectorAll('.lead,.body,.cont').forEach(e=>{
                    e.style.fontSize=(parseFloat(getComputedStyle(e).fontSize)*0.93)+'px';});}}""")
            f = f"{out_prefix}_{i}.jpg"
            pg.screenshot(path=f, type="jpeg", quality=92)
            files.append(f)
        br.close()
    return files
