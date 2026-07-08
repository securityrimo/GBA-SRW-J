# -*- coding: utf-8 -*-
"""전투대사 일본어 잔존 전수 감사.

원본 JP ROM의 전투 블록(193~370) 전 토큰을 추출한 뒤, 삽입 엔진
(srwj_battle_kr_insert)과 동일한 판정 로직으로 각 토큰의 운명을 분류한다:

  TRANSLATED       : 정상 한글화됨
  SYMBOL_ONLY      : 기호뿐인 비대사(번역 불필요)
  MISSING_FROM_JSON: battle_dialogue.json 에 raw 항목 자체가 없음  ← 추출 누락
  EMPTY_KO         : json 에 있으나 ko 가 빈칸                     ← 미번역
  RESIDUAL_JP_IN_KO: ko 에 카나/한자가 남아 로드시 제외됨          ← 불량 번역
  ENCODE_FAIL      : ko 는 있으나 인코딩 예외로 조용히 원문 유지    ← 인코더 갭

MISSING/EMPTY/RESIDUAL/ENCODE_FAIL = 게임에서 일본어로 보이는 토큰 전부.
사용: python audit_battle_jp.py [jp롬] [json] [리포트out]
"""
import json, sys, re, collections
from srwj_battle_codec import BattleCodec
from srwj_battle_kr_insert import _mapping, BattleKRInserter

JP_ROM = sys.argv[1] if len(sys.argv) > 1 else '../0.시나리오/Super Robot Taisen J (Japan).gba'
JSON_P = sys.argv[2] if len(sys.argv) > 2 else 'battle_dialogue.json'
OUT_P  = sys.argv[3] if len(sys.argv) > 3 else 'audit_battle_report.json'

FIRST, LAST = 193, 370
MARKCODE = re.compile(r'\[[0-9a-f]{2}\]')

def hjs(s):  # 삽입엔진과 동일: ・(U+30FB) 제외 일본어 잔존
    return any(('぀' <= c <= 'ヺ') or ('ー' <= c <= 'ヿ')
               or ('一' <= c <= '鿿') for c in s)

def is_symbol_only(body):
    return not body.strip('！\n　・?？!')

def extract_all(rom, cx):
    """블록 193~370의 모든 text 토큰 (blk, off, raw). 삽입엔진 rebuild_block과
    동일한 경계(포인터 엔트리 bounds) 순회 — 단 카테고리 밖 앞부분(pool_start~
    첫 bound)까지 포함해 전 구간을 커버한다."""
    toks = []
    for k in range(FIRST, LAST + 1):
        bo = cx.blkoff(rom, k); be = cx.blkoff(rom, k + 1)
        b = rom[bo:be]
        u16 = lambda o: b[o] | (b[o+1] << 8)
        pairs = [(u16(i*4), u16(i*4+2)) for i in range(10)]
        offs = [p[0] for p in pairs]; counts = [p[1] for p in pairs]
        pool_start = offs[9] + counts[9]*8
        entries = [(offs[i]+e*8, u16(offs[i]+e*8+2)) for i in range(10) for e in range(counts[i])]
        if not entries:
            continue
        bounds = sorted(set(t for _, t in entries)) + [len(b)]
        # 삽입엔진과 동일: pool_start~첫 bound 는 그대로 보존되는 구간이지만
        # 텍스트가 있는지 검사 대상에는 포함(있다면 그것도 잔존 JP 후보)
        segs = [(pool_start, bounds[0])] + [(bounds[j], bounds[j+1]) for j in range(len(bounds)-1)]
        for s0, s1 in segs:
            pos = 0; seg = b[s0:s1]
            for t in cx.parse(seg):
                blen = len(cx.rebuild([t]))
                if t[0] == 't':
                    toks.append((k, bo+s0+pos, t[1]))
                pos += blen
    return toks

def main():
    rom = open(JP_ROM, 'rb').read()
    cx = BattleCodec(rom); cx.set_gaiji(_mapping())

    doc = json.load(open(JSON_P, encoding='utf-8'))
    ents = doc['entries'] if isinstance(doc, dict) and 'entries' in doc else doc
    jmap = {}   # raw -> entry (빈 ko 포함 전부)
    for x in ents:
        raw = x.get('lead','') + x.get('jp','')
        if raw: jmap.setdefault(raw, x)

    ins = BattleKRInserter.__new__(BattleKRInserter)   # normalize 만 빌림

    toks = extract_all(rom, cx)
    uniq = {}                       # raw -> [(blk,off), ...]
    for k, off, raw in toks:
        uniq.setdefault(raw, []).append((k, off))

    cls = collections.defaultdict(list)
    for raw, locs in uniq.items():
        e = jmap.get(raw)
        lead, body = (raw[:1], raw[1:]) if e is None else (e.get('lead',''), e.get('jp',''))
        if e is None:
            # json 에 없음 — 기호뿐이면 비대사로 분류
            if is_symbol_only(raw):
                cls['SYMBOL_ONLY'].append((raw, locs)); continue
            cls['MISSING_FROM_JSON'].append((raw, locs)); continue
        ko = e.get('ko','')
        if not ko or not ko.strip():
            if is_symbol_only(body):
                cls['SYMBOL_ONLY'].append((raw, locs))
            else:
                cls['EMPTY_KO'].append((raw, locs))
            continue
        if hjs(ko):
            cls['RESIDUAL_JP_IN_KO'].append((raw, locs)); continue
        try:
            cx.enc_text(lead + ins.normalize(ko))
            cls['TRANSLATED'].append((raw, locs))
        except Exception as ex:
            cls['ENCODE_FAIL'].append((raw, locs, str(ex)))

    print(f"고유 토큰 {len(uniq)}, 전체 출현 {len(toks)}")
    for name in ['TRANSLATED','SYMBOL_ONLY','MISSING_FROM_JSON','EMPTY_KO','RESIDUAL_JP_IN_KO','ENCODE_FAIL']:
        print(f"  {name:18}: {len(cls[name])}")
    bad = ['MISSING_FROM_JSON','EMPTY_KO','RESIDUAL_JP_IN_KO','ENCODE_FAIL']
    total_bad_occ = sum(len(x[1]) for name in bad for x in cls[name])
    print(f"  → 게임에 일본어로 남는 고유 토큰: {sum(len(cls[n]) for n in bad)} (출현 {total_bad_occ}회)")

    rep = {}
    for name in bad + ['SYMBOL_ONLY']:
        rep[name] = []
        for item in cls[name]:
            raw, locs = item[0], item[1]
            r = {'raw': raw, 'n': len(locs),
                 'blks': sorted(set(k for k,_ in locs)),
                 'offs': [f"0x{o:08X}" for _,o in locs[:8]]}
            if name == 'ENCODE_FAIL': r['err'] = item[2]
            if raw in jmap: r['ko'] = jmap[raw].get('ko','')
            rep[name].append(r)
    json.dump(rep, open(OUT_P,'w',encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"상세 리포트: {OUT_P}")

if __name__ == '__main__':
    main()
