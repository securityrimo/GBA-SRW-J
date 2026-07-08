# -*- coding: utf-8 -*-
"""SJIS 시스템 텍스트 누락 전수 감사 — 원본 JP ROM 16MB 전 영역 스캔.

원 추출(srwj_sjis_no_dialogue.json)은 0x82228~0x94704 만 스캔했다.
이 도구는 ROM 전체에서 평문 SJIS 일본어 런을 찾아 다음으로 분류한다:

  IN_JSON      : translations.json 이 커버 (off~off+len)
  BATTLE       : 전투 블록 193~370 (2단계 담당, 바이트 감사로 완전성 증명됨)
  SCEN_DLG     : 시나리오 대사 블록 (0단계 담당 — 사전압축)
  DICT         : 사전/코드표/사운드 블록 (0단계가 재구축)
  ARCHIVE_MISC : 아카이브 내 기타 블록의 평문 SJIS  ← 잠재 누락!
  OUTSIDE      : 아카이브 밖(코드/데이터/그래픽)의 평문 SJIS ← 잠재 누락!

후보에는 널종료/포인터참조 신호를 붙여 실문자열 판별을 돕는다.
사용: python audit_sjis_full.py [jp롬] [translations.json] [출력json]
"""
import json, sys, struct, collections, re, bisect

JP  = sys.argv[1] if len(sys.argv) > 1 else '../0.시나리오/Super Robot Taisen J (Japan).gba'
TR  = sys.argv[2] if len(sys.argv) > 2 else 'translations.json'
OUT = sys.argv[3] if len(sys.argv) > 3 else 'audit_sjis_report.json'

IDX_BASE, NUM = 0xE3097C, 373
rom = open(JP, 'rb').read()
N = len(rom)

# ── 블록 지도 ────────────────────────────────────────────────
def blkoff(k): return IDX_BASE + struct.unpack_from('<I', rom, IDX_BASE + k*4)[0]
blocks = [(k, blkoff(k), blkoff(k+1) if k < NUM-1 else 0xFB1C00) for k in range(NUM)]

def is_scen_dialogue(s, e):
    if e - s < 16: return False
    n = struct.unpack_from('<I', rom, s)[0]
    if n % 4 or not (8 <= n <= 0x4000) or s + n > e: return False
    ptrs = struct.unpack_from('<%dI' % (n//4), rom, s)
    return all(ptrs[i] <= ptrs[i+1] for i in range(len(ptrs)-1)) and ptrs[-1] <= e - s

reg_starts, reg_tags = [], []
for k, s, e in blocks:
    if 193 <= k <= 370: tag = 'BATTLE'
    elif k in (371, 372): tag = 'DICT'
    elif s <= 0xF269D8 < e: tag = 'DICT'
    elif is_scen_dialogue(s, e): tag = 'SCEN_DLG'
    else: tag = 'ARCHIVE_MISC'
    reg_starts.append(s); reg_tags.append((s, e, tag, k))
reg_tags.sort(); reg_starts = [t[0] for t in reg_tags]

def classify_off(o):
    if o < IDX_BASE or o >= 0xFB1C00: return 'OUTSIDE', -1
    i = bisect.bisect_right(reg_starts, o) - 1
    if i >= 0:
        s, e, tag, k = reg_tags[i]
        if s <= o < e: return tag, k
    return 'OUTSIDE', -1

# ── translations.json 커버 구간 ──────────────────────────────
tr = json.load(open(TR, encoding='utf-8'))['entries']
iv = sorted((int(e['off'],16), int(e['off'],16)+max(e['len'],1)) for e in tr)
ivs = [a for a,_ in iv]
def in_json(o):
    i = bisect.bisect_right(ivs, o) - 1
    return i >= 0 and iv[i][0] <= o < iv[i][1]

# ── 포인터 타깃 인덱스 (4바이트 정렬 u32 전수) ───────────────
words = struct.unpack('<%dI' % (N//4), rom[:N//4*4])
ptr_targets = {v - 0x08000000 for v in words if 0x08000000 <= v < 0x08000000 + N}

# ── SJIS 런 스캔 (정규식, C 속도) ────────────────────────────
PAIR = re.compile(rb'(?:[\x81-\x9F\xE0-\xEA][\x40-\x7E\x80-\xFC]){2,}')
JPCH = re.compile(r'[ぁ-ゖァ-ヺー一-鿿]')
runs = []
for m in PAIR.finditer(rom):
    st = m.start(); data = m.group()
    # 쌍 단위 디코드, 실패 지점에서 런 분할
    p = 0; cs = st; chars = []
    def flush(endpos):
        if len(chars) >= 2:
            txt = ''.join(chars)
            if len(JPCH.findall(txt)) >= 2:
                runs.append((cs, endpos - cs, txt))
    while p + 1 < len(data) + 1 and p + 2 <= len(data):
        try:
            chars.append(data[p:p+2].decode('cp932')); p += 2
        except Exception:
            flush(st + p); chars = []; p += 1; cs = st + p
    flush(st + p)

# ── 분류 ────────────────────────────────────────────────────
cnt = collections.Counter(); cand = []
for st, ln, txt in runs:
    if in_json(st):
        cnt['IN_JSON'] += 1; continue
    tag, k = classify_off(st)
    cnt[tag] += 1
    if tag in ('ARCHIVE_MISC', 'OUTSIDE'):
        cand.append({'off': f'0x{st:08X}', 'len': ln, 'txt': txt[:70], 'blk': k,
                     'null': st+ln < N and rom[st+ln] == 0x00,
                     'ptr': st in ptr_targets, 'tag': tag})

print(f"전체 SJIS 런(전각JP≥2): {len(runs):,}")
for key in ('IN_JSON','BATTLE','SCEN_DLG','DICT','ARCHIVE_MISC','OUTSIDE'):
    print(f"  {key:13}: {cnt[key]:,}")
strong = [c for c in cand if c['null'] and c['ptr']]
print(f"후보(ARCHIVE_MISC+OUTSIDE) {len(cand):,} 중 강신호(널종료+포인터참조): {len(strong)}")
json.dump({'candidates': cand, 'strong': strong},
          open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f"리포트: {OUT}")
