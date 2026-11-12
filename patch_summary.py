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
# 수치가 낮아질수록 좋은 항목 ('80초마다', '1.45초 당' 처럼 주기도 포함)
LOWER_BETTER = re.compile(
    r"쿨다운|쿨타임|재사용|소모|시전 시간|받는 피해량|받는 피해(?! 감소)|딜레이|초\s?마다|초\s?당"
)
NOT_LOWER_BETTER = re.compile(r"쿨다운 감소|감소량|피해 감소")
# 버프/너프를 붙이지 않는 변경 (상품 가격 등)
NO_TAG = re.compile(r"\bNP\b|할인\)")

# '옷', '팔/장식', '활' 같은 분류 제목이 나올 수 있는 섹션
CATEGORY_SECTIONS = re.compile(r"무기|방어구|아이템")
# 설정값(화살표 없는 수치)에도 버프/너프를 붙이는 섹션
BALANCE_SECTIONS = re.compile(r"실험체|무기|방어구|아이템|전술 스킬|특성")
HEADING = "\x01"  # 소제목 줄 표시 (출력 전에 '### ' 또는 '- ' 로 바뀜)
NEW_OR_REMOVED = re.compile(r"\((?:신규|삭제)[^)]*\)")
# 화살표 없는 설정값이라도 남길 만한 단위
UNIT = re.compile(r"\d\s*(?:%|초|스택|중첩|m\b|레벨|배|칸|개)")
# 아이템 이름 뒤에 바로 붙는 능력치 (예: '드라우프니르 스킬 증폭 75 -> 76')
STAT_START = re.compile(
    r"\s(?=(?:레벨 당 )?(?:스킬 증폭|공격력|방어력|방어 관통|공격 속도|최대 체력|체력|쿨다운 감소|"
    r"치명타|이동 속도|생명력 흡수|주는 회복|기본 공격 증폭)\b)"
)


def _text(el):
    s = el.get_text("") if isinstance(el, Tag) else str(el)
    s = s.replace("\xa0", " ").replace("​", "").replace("→", "->")
    s = s.replace("[img]", "")
    return re.sub(r"\s+", " ", s).strip()


def _own_text(li):
    """li 안에서 하위 ul 을 제외한 자기 텍스트."""
    parts = [_text(c) for c in li.children if not (isinstance(c, Tag) and c.name in ("ul", "ol"))]
    return " ".join(p for p in parts if p)


def _sub_list(li):
    return li.find(["ul", "ol"], recursive=False)


def _is_sentence(text):
    return bool(re.search(r"(니다|다)[.!]?(\s*\([^)]*\))?$", text))


def _keep_leaf(text, ancestors, strict):
    """변경 사항으로 남길 줄인지.

    strict: 실험체/아이템 소속이 아닌 일반 목록. 이때는 수치 변경과 신규/삭제만 남긴다
    ('보관함 -> 기타' 같은 메뉴 경로, 지역별 아이템 개수표 등 제외).
    """
    if "->" in text:
        return not strict or bool(re.search(r"\d[^>]*->.*\d", text))
    if NEW_OR_REMOVED.search(text):
        return True
    if strict or len(text) > 60 or _is_sentence(text) or not re.search(r"\d", text):
        return False
    # 신규 아이템 능력치, '방어력 +2', '받는 피해량 115%', '100스택 획득' 같은 설정값
    return bool(
        UNIT.search(text) or re.search(r"\+\d", text)
        or any(re.search(r"능력치|신규", a) for a in ancestors)
    )


def _changes(li, ancestors=(), strict=False):
    """li 트리에서 변경 사항을 '라벨 변경내용' 형태로 평탄화."""
    label = _own_text(li)
    sub = _sub_list(li)
    if not sub:
        return [label] if label and _keep_leaf(label, ancestors, strict) else []
    out = []
    if "[고유" in label and (NEW_OR_REMOVED.search(label) or any("신규" in a for a in ancestors)):
        return [label]  # 새 고유 효과는 이름만 (설명 문장 생략)
    if NEW_OR_REMOVED.search(label):
        out.append(label)
    prefix = "" if label == "능력치" else f"{label} "
    for child in sub.find_all("li", recursive=False):
        out += [f"{prefix}{c}" for c in _changes(child, (*ancestors, label), strict)]
    return out


def _is_bold_title(p):
    """<p><strong>이름</strong></p> 형태(실험체/아이템 이름)인지."""
    if p.name != "p":
        return False
    text = _text(p)
    strong = p.find("strong")
    return bool(text) and strong is not None and _text(strong) == text and len(text) <= 30


def _starts_bold(p):
    """'<strong>스킨 이름</strong> 스킨이 출시됩니다.' 처럼 굵은 글씨로 시작하는 짧은 안내."""
    strong = p.find("strong")
    text = _text(p)
    if strong is None or not text or len(text) > 80 or text.startswith("["):
        return False
    bold = _text(strong)
    return bool(bold) and text.startswith(bold) and text != bold


def _numbers(s):
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s.replace(",", ""))]


def _numeric_tag(change):
    if "->" not in change or NO_TAG.search(change):
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


def _setting_tag(change):
    """화살표 없는 설정값의 버프/너프 ('받는 피해량 115%', '궁극기 쿨다운 감소량 30%' 등).

    기준은 기본값 100% (패치노트의 '별도 표기가 없다면 100%').
    """
    m = re.search(r"받는 피해(?:량)? (\d+(?:\.\d+)?)%", change)
    if m:
        v = float(m.group(1))
        return "너프" if v > 100 else "버프" if v < 100 else None
    m = re.search(r"(?:입히는|주는) 피해(?:량)? (\d+(?:\.\d+)?)%", change)
    if m:
        v = float(m.group(1))
        return "버프" if v > 100 else "너프" if v < 100 else None
    if re.search(r"쿨다운 감소(?:량)? \d|\+\d|\d+\s*(?:스택|중첩)\s*획득", change):
        return "버프"
    return None


def _tag(tag):
    return f"**({tag})**" if tag else ""


def _entry(name, changes, comment=None, settings=False):
    """'이름 - 변경, 변경**(버프)**'.

    settings: 화살표 없는 설정값도 버프/너프를 판단 (실험체·아이템 섹션의 모드 보정값 등).
    판단이 항목마다 다르면 각 항목 뒤에 따로 붙인다.
    """
    head = f"**{name}** - "
    if settings and comment is None and "신규" not in name and not any("->" in c for c in changes):
        tags = [_setting_tag(c) for c in changes]
        if len(set(tags)) > 1:
            return head + ", ".join(c + _tag(t) for c, t in zip(changes, tags))
        return f"{head}{', '.join(changes)}{_tag(tags[0])}"
    return f"{head}{', '.join(changes)}{_tag(classify(comment, changes))}"


def _split_item(change, settings=False):
    """'드라우프니르 스킬 증폭 75 -> 76' → '드라우프니르 - 스킬 증폭 75 -> 76'."""
    parts = STAT_START.split(change, maxsplit=1)
    if len(parts) == 2 and parts[0]:
        return _entry(parts[0], [parts[1]], settings=settings)
    tag = classify(None, [change])
    return change + (f"**({tag})**" if tag else "")


def _title_info(soup):
    """'13.0 패치노트 Part.1 - 메인 (2026.11.12)' → ('13.0', 'Part.1 - 메인')."""
    title = soup.select_one(".er-article-detail__title")
    text = _text(title) if title else ""
    m = re.search(r"(\d+\.\d+(?:\.\d+)?[a-z]?)\s*(?:패치\s?노트|핫픽스|업데이트)", text)
    version = m.group(1) if m else ""
    part = ""
    if m:
        part = re.sub(r"\(\d{4}\.\d{1,2}\.\d{1,2}\)", "", text[m.end():]).strip(" -")
    return version, part


class _Section:
    """h5 섹션 하나를 읽으며 요약 줄을 만든다."""

    def __init__(self, title):
        self.title = title
        self.lines = []
        self.entity = None  # [이름, 코멘트, 변경목록]
        self.category = None  # 아직 출력하지 않은 분류 제목
        self.in_category = False
        self.has_categories = False
        self.after_heading = False  # 바로 위가 h3 소제목이면 들여쓰기
        self.allow_categories = bool(CATEGORY_SECTIONS.search(title))
        self.tag_settings = bool(BALANCE_SECTIONS.search(title))

    def emit(self, line):
        if self.category:
            self.lines.append(f"### {self.category}")
            self.category = None
        self.lines.append(line)

    def flush(self):
        if self.entity and self.entity[2]:
            name, comment, changes = self.entity
            self.emit(_entry(name, changes, comment, settings=self.tag_settings))
        self.entity = None

    def heading(self, el):
        self.flush()
        self.category, self.in_category = None, False
        text = _text(el)
        if text:
            # 소제목 표시: 아래에 대상 줄이 오면 '### ', 아니면 글머리표 (_append_section 에서 결정)
            indent = "  " if el.name == "h4" and self.after_heading else ""
            self.lines.append(f"{HEADING}{indent}{text}")
            self.after_heading = True

    def paragraph(self, el):
        text = _text(el)
        if not text:
            return
        if _is_bold_title(el):
            self.flush()
            self.after_heading = False
            nxt = el.find_next_sibling(lambda t: isinstance(t, Tag) and _text(t))
            if self.allow_categories and nxt is not None and nxt.name == "ul" and (
                self.has_categories or all(_sub_list(li) for li in nxt.find_all("li", recursive=False))
            ):
                self.category, self.in_category, self.has_categories = text, True, True
            else:
                self.entity, self.in_category = [text, None, []], False
            return
        if self.entity and not self.entity[2] and self.entity[1] is None:
            self.entity[1] = text  # 이름 바로 다음 문단 = 개발자 코멘트
        elif not self.entity and _starts_bold(el):
            self.emit(f"- {text}")

    def bullet_list(self, el):
        for li in el.find_all("li", recursive=False):
            label = _own_text(li)
            if self.entity is not None and not self.in_category:
                self.entity[2] += _changes(li)
            elif _sub_list(li):
                # 전술 스킬/아이템/음식: 최상위 항목이 이름
                if _is_sentence(label) or len(label) > 40:
                    continue  # 이름이 아니라 설명 문장 + 예시 목록
                changes = []
                for child in _sub_list(li).find_all("li", recursive=False):
                    changes += _changes(child, (label,))
                if NEW_OR_REMOVED.search(label) and not changes:
                    self.emit(f"- {label}")
                elif changes:
                    self.emit(_entry(label, changes, settings=self.tag_settings))
            elif self.in_category and _keep_leaf(label, (), strict=False):
                self.emit(_split_item(label, settings=self.tag_settings))
            elif _keep_leaf(label, (), strict=True):
                # 특정 대상에 속하지 않은 변경(랭크 포인트 등)이라 버프/너프는 붙이지 않음
                indent = "  " if self.after_heading else ""
                self.emit(f"{indent}- {label}")

    def finish(self, h5):
        self.flush()
        if not self.lines:
            # 내용을 못 뽑은 섹션은 첫 안내 문장으로 대신
            for el in h5.find_next_siblings():
                if el.name == "h5":
                    break
                for node in ([el] if el.name == "p" else el.find_all("li") if el.name == "ul" else []):
                    text = _own_text(node) if node.name == "li" else _text(node)
                    if _is_sentence(text) and len(text) <= 150:
                        return [f"- {text}"]
        return self.lines


def summarize(html):
    """패치노트 HTML 을 마크다운 줄 목록으로. 구조를 못 찾으면 None."""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one(".er-article-content")
    if content is None or not content.select("h5.er-point-title"):
        return None

    version, part = _title_info(soup)
    title = f"# 이리 {version}버전 업데이트 패치 내역" if version else "# 이리 업데이트 패치 내역"
    out = [f"{title} {part}".strip()]

    section, h5 = None, None
    for el in content.children:
        if isinstance(el, NavigableString) or not isinstance(el, Tag):
            continue
        if el.name == "h5" and "er-point-title" in (el.get("class") or []):
            if section:
                _append_section(out, section, h5)
            section, h5 = _Section(_text(el)), el
        elif section is None:
            # 첫 섹션 전 공지 (점검 시간, 루트 초기화 안내 등)
            if el.name in ("h2", "h3", "h4") and _text(el):
                out.append(f"- {_text(el)}")
        elif el.name in ("h2", "h3", "h4"):
            section.heading(el)
        elif el.name == "p":
            section.paragraph(el)
        elif el.name in ("ul", "ol"):
            section.bullet_list(el)
    if section:
        _append_section(out, section, h5)
    return out


def _append_section(out, section, h5):
    body = _bug_counts(h5) if re.search(r"버그", section.title) else section.finish(h5)
    out.append(f"## {section.title}")
    prev = None
    for i, line in enumerate(body):
        if line.startswith(HEADING):
            # 바로 아래에 대상 줄이 오는 소제목은 굵은 제목으로, 아니면 글머리표로
            text = line[len(HEADING):]
            nxt = next((l for l in body[i + 1:] if l), "")
            line = f"### {text.strip()}" if _is_entry(nxt) or nxt.startswith("### ") else (
                f"  - {text.strip()}" if text.startswith("  ") else f"- {text}"
            )
        # 대상(실험체/아이템)이 바뀔 때마다 빈 줄로 구분
        if _is_entry(line) and prev is not None and _is_entry(prev):
            out.append("")
        out.append(line)
        prev = line


def _is_entry(line):
    """'**이름** - 변경' 형태의 대상 줄인지 (제목/글머리표 제외)."""
    if line.startswith("**"):
        return bool(re.match(r"\*\*[^*]+\*\* - ", line))
    return bool(line) and not line.startswith(("#", "-", " ", HEADING))


def _bug_counts(h5):
    """버그 수정 섹션: h3 소제목별 항목 수."""
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
