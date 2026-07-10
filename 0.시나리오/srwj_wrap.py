# -*- coding: utf-8 -*-
"""
srwj_wrap.py — 대사 줄바꿈 / 대사창 길이 맞춤

규칙 (새 정의)
--------------
* 모든 글자는 1로 카운트한다. (전각/반각 구분 없음 — 단, 원문자 ①②… 는 5자)
* 한 줄 최대 글자 수 = 14 (DISPLAY_WIDTH).
* 첫 줄: 화자명(최대 5) + 꺽쇠 「」(2) = 7 을 예약 → 순수 대사 최대 7자.
  - 띄어쓰기 없는 한 단어가 7자를 넘으면, 단어를 잘라서라도 첫 줄은 7자에서 끊고
    나머지는 다음 줄로 넘긴다. (greedy_wrap 의 글자단위 분리로 처리)
* 독백(화자 없음)도 ( ) 괄호로 시작·끝나므로 첫 줄 공식을 동일하게 적용한다(예약 7).
* 마지막 줄은 끝에 닫는 괄호(」 또는 ))가 붙으므로 1자를 덜 채운다(최대 13자).
* 줄 수는 일본어 원본 턴의 줄 수에 맞추는 것을 기본으로 하되, 한국어가 그 줄 수에
  안 들어가면 자동으로 줄을 늘리고 경고한다. (기존 규칙 유지)
"""

from srwj_codec import normalize_text, text_width, char_width, DISPLAY_WIDTH

# 첫 줄에서 화자 라벨이 차지하는 폭 = 화자명(최대 5자) + 꺽쇠 「」(2자) = 7.
#  → 첫 줄 순수 대사 예산 = DISPLAY_WIDTH(14) - 7 = 7자.
#  화자명이 최대 5자라는 전제 하에, 화자명 길이와 무관하게 항상 7을 예약한다
#  (독백도 동일 적용).
MAX_SPEAKER_RESERVE = 7
# 닫는 괄호(」 또는 ))가 마지막 줄 끝에 1자 붙으므로 마지막 줄은 1자 덜 채운다.
CLOSE_BRACKET_RESERVE = 1
# 단어 사이 구분자: 전각 공백(normalize_text 가 일반 공백을 전각 공백으로 바꿈).
#  새 규칙상 모든 글자는 1로 카운트하므로 공백도 1.
SEP = '\u3000'
SEP_W = char_width(SEP)        # = 1


def _hard_break(word: str, budget: int):
    """한 단어가 budget 보다 길면 폭 단위로 잘라 여러 조각으로."""
    pieces, cur, cw = [], '', 0
    for ch in word:
        w = char_width(ch)
        if cur and cw + w > budget:
            pieces.append(cur)
            cur, cw = '', 0
        cur += ch
        cw += w
    if cur:
        pieces.append(cur)
    return pieces


def greedy_wrap(text: str, budgets, default_budget: int):
    """text 를 줄별 폭 예산에 맞춰 줄바꿈.

    * 단어(전각 공백 기준)를 우선 단위로 채우되,
    * 한 단어가 그 줄 예산보다 길면 '글자 단위'로 쪼개 여러 줄에 채운다.
    * 각 줄은 자신의 인덱스에 해당하는 예산(budgets[i] 또는 default_budget)을
      절대 넘지 않는다. (띄어쓰기 없는 긴 대사도 줄마다 정확히 맞춤)

    Args:
        text          : 정규화된 텍스트(공백은 전각 '　')
        budgets       : 앞쪽 줄들의 폭 예산 리스트
        default_budget: budgets 를 다 쓴 뒤 적용할 폭 예산

    Returns: 줄 문자열 리스트
    """
    def bud(i):
        return budgets[i] if i < len(budgets) else default_budget

    words = [w for w in text.split(SEP) if w != '']
    lines, cur, cw, li = [], '', 0, 0

    def flush():
        nonlocal cur, cw, li
        lines.append(cur)
        li += 1
        cur, cw = '', 0

    for word in words:
        ww = text_width(word)
        sep = SEP_W if cur else 0

        # 1) 현재 줄에 (공백+단어)가 그대로 들어가면 추가
        if cur and cw + sep + ww <= bud(li):
            cur += SEP + word
            cw += sep + ww
            continue

        # 2) 줄을 바꾸면 단어가 빈 줄에 들어가는 경우 → 줄바꿈 후 추가
        next_li = li + (1 if cur else 0)
        if ww <= bud(next_li):
            if cur:
                flush()
            cur, cw = word, ww
            continue

        # 3) 단어가 한 줄보다도 길다 → 글자 단위로 쪼개며 줄을 채움
        if cur:
            flush()
        for ch in word:
            w = char_width(ch)
            if cur and cw + w > bud(li):
                flush()
            cur += ch
            cw += w

    if cur:
        lines.append(cur)
    return lines if lines else ['']


def _wrap_lines(joined: str, first_budget: int, last_budget: int,
                target_lines: int):
    """첫 줄·마지막 줄 예산을 반영해 줄바꿈 (마지막 줄 위치를 수렴 계산).

    마지막 줄은 닫는 「」 를 위해 last_budget 으로 좁혀야 하는데, 그러면
    줄 수가 바뀔 수 있으므로 줄 수가 안정될 때까지 몇 번 반복한다.
    """
    n = max(1, target_lines)
    lines = None
    for _ in range(6):
        if n <= 1:
            budgets = [min(first_budget, last_budget)]
        else:
            budgets = [first_budget] + [DISPLAY_WIDTH] * (n - 2) + [last_budget]
        lines = greedy_wrap(joined, budgets, DISPLAY_WIDTH)
        if len(lines) == n:
            break
        n = len(lines)
    # 안전망: 마지막 줄이 여전히 last_budget 초과면 글자단위로 한 번 더 분리
    if lines and text_width(lines[-1]) > last_budget:
        tail = greedy_wrap(lines[-1], [last_budget], last_budget)
        lines = lines[:-1] + tail
    return lines


def speaker_reserve(speaker) -> int:
    """첫 줄에서 화자 라벨(또는 독백 괄호)이 차지하는 폭.

    새 규칙: 화자명은 최대 5자라고 보고, 화자명 길이·종류와 무관하게 항상
    MAX_SPEAKER_RESERVE(=7 = 화자명5 + 꺽쇠2)를 예약한다.
      * 일반 화자        → 7
      * 독백('' )        → 7 (( ) 괄호로 시작·끝나므로 첫 줄 공식 동일 적용)
      * 미상(None/'???') → 7
    → 첫 줄 순수 대사 예산 = DISPLAY_WIDTH(15) - 7 = 8자로 통일된다.
    """
    return MAX_SPEAKER_RESERVE


def fit_turn_lines(kr_text: str, jp_line_count: int,
                   first_line_reserve: int = MAX_SPEAKER_RESERVE,
                   speaker=None):
    """한 턴의 한국어 텍스트를 게임용 줄 목록으로 변환.

    새 규칙으로 줄바꿈한다 (거의 모든 글자 1, 원문자 ①②… 만 5, 한 줄 최대 14자):
      * 첫 줄은 화자명(최대5)+꺽쇠(2)=7 을 예약 → 순수 대사 최대 7자.
        띄어쓰기 없는 단어가 7자를 넘으면 잘라서라도 7자에서 끊고 다음 줄로 넘긴다.
      * 독백(화자 없음)도 ( ) 괄호로 시작·끝나므로 첫 줄 공식 동일(예약 7).
      * 마지막 줄은 끝에 닫는 괄호(」/))가 1자 붙으므로 1자 덜 채운다(최대 13자).

    Args:
        kr_text            : 번역 한국어 (줄바꿈 \\n 포함 가능)
        jp_line_count      : 일본어 원본 턴의 줄 수 (목표)
        first_line_reserve : (호환용) speaker 처리로 사실상 항상 7 이 적용된다
        speaker            : 화자 이름(번역). 새 규칙상 길이와 무관하게 예약 7.

    Returns:
        (lines, warnings)
    """
    warnings = []
    norm = normalize_text(kr_text)

    # 첫 줄 예약폭: 새 규칙상 화자/독백/미상 모두 7 (= 화자명5 + 꺽쇠2).
    if speaker is not None:
        reserve = speaker_reserve(speaker)
    else:
        reserve = max(first_line_reserve, MAX_SPEAKER_RESERVE)
    first_budget = max(4, DISPLAY_WIDTH - reserve)            # = 14 - 7 = 7
    last_budget  = max(4, DISPLAY_WIDTH - CLOSE_BRACKET_RESERVE)  # = 14 - 1 = 13

    # 번역가의 \n 줄
    raw_lines = [ln for ln in norm.split('\n')]
    # 끝쪽 빈 줄 제거
    while raw_lines and raw_lines[-1].strip('\u3000') == '':
        raw_lines.pop()
    if not raw_lines:
        raw_lines = ['']

    def line_budget(idx, total):
        """idx 번째 줄(전체 total 줄)의 폭 예산."""
        b = DISPLAY_WIDTH
        if idx == 0:
            b = min(b, first_budget)
        if idx == total - 1:
            b = min(b, last_budget)
        return b

    def line_ok(idx, s, total):
        return text_width(s.strip('\u3000')) <= line_budget(idx, total)

    # (A) 번역가 줄 수가 목표와 같고 폭(첫·끝 reserve 포함)도 OK → 그대로 사용
    if len(raw_lines) == jp_line_count and \
            all(line_ok(i, s, len(raw_lines)) for i, s in enumerate(raw_lines)):
        return [s.strip('\u3000') for s in raw_lines], warnings

    # (B) 재배치: 전체 텍스트를 목표 줄 수에 맞춰 그리디 줄바꿈
    #     (첫 줄=화자폭, 마지막 줄=닫는 「」 폭 반영, 글자 단위까지 정확히 맞춤)
    joined = '\u3000'.join(s.strip('\u3000') for s in raw_lines
                           if s.strip('\u3000') != '')
    lines = _wrap_lines(joined, first_budget, last_budget, jp_line_count)

    if len(lines) > jp_line_count:
        warnings.append(
            f'줄 수 초과: 목표 {jp_line_count}줄 → 실제 {len(lines)}줄 '
            f'(한국어가 대사창에 다 안 들어감)')
    # KR이 JP보다 짧게 감겨도 빈 줄로 채우지 않는다.
    #  (채우면 게임이 마지막=빈 줄 끝에 닫는 괄호 」/) 를 그려 "빈 대사에 닫힘
    #   괄호만" 뜨는 현상이 생긴다. 대사창은 KR 길이에 맞게 짧아지는 게 자연스럽고,
    #   닫는 괄호는 실제 마지막 글자 뒤에 붙는다.)

    # 최종 폭 점검 (첫 줄·끝 줄 reserve 반영)
    total = len(lines)
    for i, s in enumerate(lines):
        bud = line_budget(i, total)
        w = text_width(s)
        if w > bud:
            warnings.append(f'{i+1}번째 줄 폭 초과: {w} > {bud}  "{s}"')

    return lines, warnings
