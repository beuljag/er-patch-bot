"""공식 패치노트 HTML → 디스코드용 마크다운 요약.

출력 예:
    # 이리 12.7버전 업데이트 패치 내역
    ## 실험체
    나타폰 - 스냅샷(Q) 피해량 60(+스킬 증폭의 80%) -> 60(+스킬 증폭의 85%)**(버프)**
"""

import re

from bs4 import BeautifulSoup, NavigableString, Tag

DISCORD_LIMIT = 1900  # 메시지당 2000자 제한, 여유분 확보

# 개발자 코멘트에서 버프/너프 판단
BUFF_WORDS = re.compile(r"상향|강화|보완|높이|높여|높입|늘려|늘립|늘어|보강|끌어올|힘을 더")
NERF_WORDS = re.compile(r"하향|낮춰|낮추|견제(?!력)|덜어|단축|줄여|줄입|줄이|약화|리스크")
# 수치가 낮아질수록 좋은 항목
LOWER_BETTER = re.compile(r"쿨다운|쿨타임|재사용|소모|시전 시간|받는 피해량|받는 피해(?! 감소)|딜레이")
NOT_LOWER_BETTER = re.compile(r"쿨다운 감소|감소량|피해 감소")


def _text(el):
    s = el.get_text("") if isinstance(el, Tag) else str(el)
    s = s.replace("\xa0", " ").replace("→", "->")
    return re.sub(r"\s+", " ", s).strip()


def _own_text(li):
    """li 안에서 하위 ul 을 제외한 자기 텍스트."""
    parts = [_text(c) for c in li.children if not (isinstance(c, Tag) and c.name in ("ul", "ol"))]
    return " ".join(p for p in parts if p)


def _sub_list(li):
    return li.find(["ul", "ol"], recursive=False)


def _is_change(text):
    """'12 -> 10' 처럼 화살표 양쪽에 숫자가 있는 수치 변경인지 ('보관함 -> 기타' 제외)."""
    return bool(re.search(r"\d[^>]*->.*\d", text))


def _changes(li, prefix=""):
    """li 트리에서 수치 변경 사항을 '라벨 변경내용' 형태로 평탄화."""
    label = _own_text(li)
    sub = _sub_list(li)
    if not sub:
        return [f"{prefix}{label}".strip()] if _is_change(label) else []
    out = []
    for child in sub.find_all("li", recursive=False):
        out += _changes(child, f"{prefix}{label} ")
    return out


def _is_bold_title(p):
    """<p><strong>이름</strong></p> 형태(실험체/아이템 이름)인지."""
    if p.name != "p":
        return False
    text = _text(p)
    strong = p.find("strong")
    return bool(text) and strong is not None and _text(strong) == text and len(text) <= 30


def _numbers(s):
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]


def _numeric_tag(change):
    if "->" not in change:
        return 0
    before, after = change.split("->", 1)
    nb, na = _numbers(before), _numbers(after)
    if not nb or not na:
        return 0
    # 앞쪽 라벨에 섞인 숫자(예: '40mm', 'Q1')를 피하려고 마지막 덩어리끼리 비교
    nb = nb[-len(na):] if len(nb) > len(na) else nb
    diff = sum(na) - sum(nb)
    if diff == 0:
        return 0
    lower_better = LOWER_BETTER.search(before) and not NOT_LOWER_BETTER.search(before)
    return -1 if (diff > 0) == bool(lower_better) else 1


def _comment_tag(text):
    nerf = bool(NERF_WORDS.search(text))
    text = re.sub(r"리스크[^.,]*?(높|더)", "", text)  # '리스크를 높입니다' 는 너프
    buff = bool(BUFF_WORDS.search(text))
    if buff and nerf:
        return "조정"
    return "버프" if buff else "너프" if nerf else None


def classify(comment, changes):
    if comment:
        # 코멘트는 보통 '현황 설명. 이에 ~를 상향/하향합니다.' 구조라 마지막 문장을 우선
        sentences = [s for s in re.split(r"(?<=[.!])\s+", comment) if s.strip()]
        tag = _comment_tag(sentences[-1]) if sentences else None
        tag = tag or _comment_tag(comment)
        if tag:
            return tag
    votes = {_numeric_tag(c) for c in changes} - {0}
    if votes == {1}:
        return "버프"
    if votes == {-1}:
        return "너프"
    if votes:
        return "조정"
    return None


class _Entity:
    def __init__(self, name):
        self.name, self.comment, self.changes = name, None, []

    def line(self):
        tag = classify(self.comment, self.changes)
        body = ", ".join(self.changes)
        return f"{self.name} - {body}" + (f"**({tag})**" if tag else "")


def summarize(html, version=None):
    """패치노트 HTML 을 마크다운 줄 목록으로. 구조를 못 찾으면 None."""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one(".er-article-content")
    if content is None or not content.select("h5.er-point-title"):
        return None

    if version is None:
        title = soup.select_one(".er-article-detail__title")
        m = re.search(r"(\d+\.\d+(?:\.\d+)?[a-z]?)\s*(?:패치|핫픽스|업데이트)", _text(title) if title else "")
        version = m.group(1) if m else ""

    sections = []  # [(제목, [줄])]
    lines = None
    entity = None
    category_mode = False  # 지금 굵은 제목이 '옷' 같은 분류 제목인지
    has_categories = False  # 이 섹션에 분류 제목이 나온 적 있는지

    def flush():
        nonlocal entity
        if entity and entity.changes:
            lines.append(entity.line())
        entity = None

    for el in content.children:
        if isinstance(el, NavigableString) or not isinstance(el, Tag):
            continue
        if el.name == "h5" and "er-point-title" in (el.get("class") or []):
            if lines is not None:
                flush()
            lines = []
            sections.append((_text(el), lines))
            category_mode = has_categories = False
            continue
        if lines is None:
            continue  # 첫 섹션 이전 안내문

        if el.name in ("h2", "h3", "h4"):
            flush()
            category_mode = False
            if _text(el):
                lines.append(f"- {_text(el)}")
            continue

        if el.name == "p":
            text = _text(el)
            if not text:
                continue
            if _is_bold_title(el):
                flush()
                nxt = el.find_next_sibling(lambda t: isinstance(t, Tag) and _text(t))
                # '옷', '팔/장식' 처럼 바로 하위 항목 목록이 오는 분류 제목
                if nxt is not None and nxt.name == "ul" and (has_categories or all(
                    _sub_list(li) for li in nxt.find_all("li", recursive=False)
                )):
                    lines.append(f"**{text}**")
                    category_mode = has_categories = True
                else:
                    entity = _Entity(text)
                    category_mode = False
            elif entity and not entity.changes and entity.comment is None:
                entity.comment = text
            elif not entity and el.find("strong") and len(text) <= 80:
                # 스킨 출시 등 일반 안내: 굵은 글씨로 시작하는 짧은 문장
                lines.append(f"- {text}")
            continue

        if el.name in ("ul", "ol"):
            for li in el.find_all("li", recursive=False):
                label = _own_text(li)
                changes = _changes(li)
                if entity is not None and not category_mode:
                    entity.changes += changes
                elif changes and _sub_list(li):
                    # 전술 스킬/아이템: 최상위 항목이 이름
                    e = _Entity(label)
                    e.changes = [c[len(label):].strip() for c in changes]
                    lines.append(e.line())
                elif changes and category_mode:
                    tag = classify(None, changes)
                    lines.append(changes[0] + (f"**({tag})**" if tag else ""))
                elif changes:
                    lines.append(f"  - {changes[0]}")
            continue

    if lines is not None:
        flush()

    # 버그 수정처럼 항목이 많은 섹션은 소제목+건수로 축약
    out = [f"# 이리 {version}버전 업데이트 패치 내역" if version else "# 이리 업데이트 패치 내역"]
    for title, body in sections:
        if re.search(r"버그", title):
            body = _bug_counts(content, title) or body
        if not body:
            continue
        out.append(f"## {title}")
        prev = None
        for line in body:
            # 대상(실험체/아이템)이 바뀔 때마다 빈 줄로 구분
            if _is_entry(line) or line.startswith("**"):
                if prev is not None and _is_entry(prev):
                    out.append("")
            out.append(line)
            prev = line
    return out


def _is_entry(line):
    """'이름 - 변경' 형태의 대상 줄인지 (제목/글머리표/분류 제목 제외)."""
    return bool(line) and not line.startswith(("#", "-", " ", "**"))


def _bug_counts(content, section_title):
    """balance 가 아닌 섹션에서 h3 소제목별 항목 수."""
    h5 = next((h for h in content.select("h5.er-point-title") if _text(h) == section_title), None)
    if h5 is None:
        return []
    out, current, count = [], None, 0
    for el in h5.find_next_siblings():
        if el.name == "h5":
            break
        if el.name in ("h3", "h4"):
            if current:
                out.append(f"- {current} ({count}건)")
            current, count = _text(el), 0
        elif el.name in ("ul", "ol"):
            count += len(el.find_all("li", recursive=False))
    if current:
        out.append(f"- {current} ({count}건)")
    return out


def to_messages(lines, footer=""):
    """2000자 제한에 맞춰 줄 단위로 여러 메시지로 분할."""
    messages, buf = [], ""
    for line in lines + ([footer] if footer else []):
        line = line[:DISCORD_LIMIT]
        if len(buf) + len(line) + 1 > DISCORD_LIMIT:
            messages.append(buf.rstrip())
            buf = ""
        if not line and not buf:
            continue  # 메시지 첫머리의 빈 줄은 생략
        buf += line + "\n"
    if buf.strip():
        messages.append(buf.rstrip())
    return messages
