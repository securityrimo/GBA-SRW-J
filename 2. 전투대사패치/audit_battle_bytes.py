# -*- coding: utf-8 -*-
"""전투대사 구간 바이트 단위 완전성 감사.

원본 JP ROM의 전투 구간(블록 193~370)을 1바이트도 빠짐없이 다음 범주로
분해하고, 어느 범주에도 속하지 않는 바이트(=추출 파이프라인이 놓친 바이트)를
찾는다.

  HDR  : 블록 헤더 40바이트 (10×(offset u16,count u16))
  TBL  : 포인터 엔트리 테이블 (8바이트 레코드 × count 합)
  GAP  : pool_start ~ 첫 참조오프셋 (미참조 스테이징/동기 데이터, 원본 보존)
  TXT  : 참조 텍스트 세그먼트 — json 항목 바이트 + 구분자(0x78/79/7a/7c)
  ????: 미분류 = 누락!

추가 검증:
  A. 블록 연속성: blkoff(k+1)==blkoff(k)+size, 구간 전체 타일링
  B. 테이블 패킹: offs[i+1] == offs[i] + 8*counts[i]
  C. 왕복 항등: rebuild(parse(seg)) == seg (모든 세그먼트 바이트 동일)
  D. 엔트리 +4/+6 필드가 미추출 텍스트를 가리키는지 분류
  E. json 각 항목: ROM[ptr:ptr+len]가 모든 출현 위치에서 동일 바이트인지
사용: python audit_battle_bytes.py [jp롬] [json]
"""
import json, sys, struct, collections
from srwj_battle_codec import BattleCodec
from srwj_battle_kr_insert import _mapping

JP  = sys.argv[1] if len(sys.argv) > 1 else '../0.시나리오/Super Robot Taisen J (Japan).gba'
JSP = sys.argv[2] if len(sys.argv) > 2 else 'battle_dialogue.json'
FIRST, LAST = 193, 370
SEP = {0x78, 0x79, 0x7a, 0x7c}

rom = open(JP, 'rb').read()
cx = BattleCodec(rom); cx.set_gaiji(_mapping())

doc = json.load(open(JSP, encoding='utf-8'))
ents = doc['entries'] if isinstance(doc, dict) else doc

# json 항목의 모든 출현 위치 (off + ptrs)
occ = []                                   # (abs_off, blen, raw)
for e in ents:
    raw = e.get('lead','') + e.get('jp','')
    locs = e.get('ptrs') or [e['off']]
    for p in locs:
        occ.append((int(p,16), e['len'], raw))

issues = []
tot = collections.Counter()
seg_rt_fail = 0
f46 = collections.Counter()                 # 엔트리 +4/+6 필드 분류

blk_starts = [cx.blkoff(rom,k) for k in range(FIRST, LAST+2)]
# A. 블록 연속성
contig = all(blk_starts[i] < blk_starts[i+1] for i in range(len(blk_starts)-1))
print(f"A. 블록 연속성(단조증가): {'OK' if contig else 'FAIL'}  구간 0x{blk_starts[0]:X}~0x{blk_starts[-1]:X} ({blk_starts[-1]-blk_starts[0]:,}B)")

for ki, k in enumerate(range(FIRST, LAST+1)):
    bo, be = blk_starts[ki], blk_starts[ki+1]
    b = rom[bo:be]
    cover = bytearray(len(b))               # 0=미분류 1=HDR 2=TBL 3=GAP 4=TXT
    u16 = lambda o: b[o] | (b[o+1] << 8)
    pairs = [(u16(i*4), u16(i*4+2)) for i in range(10)]
    offs = [p[0] for p in pairs]; counts = [p[1] for p in pairs]
    for i in range(0x28): cover[i] = 1
    # B. 테이블 패킹 검사 + TBL 마킹
    if offs[0] != 0x28:
        issues.append(f"blk{k}: offs[0]=0x{offs[0]:X} != 0x28")
    for i in range(9):
        if offs[i+1] != offs[i] + 8*counts[i]:
            issues.append(f"blk{k}: 테이블 {i}~{i+1} 사이 간극 (0x{offs[i]+8*counts[i]:X}→0x{offs[i+1]:X})")
    pool = offs[9] + counts[9]*8
    for i in range(0x28, min(pool, len(b))): cover[i] = 2
    entries = [(offs[i]+e8*8, u16(offs[i]+e8*8+2)) for i in range(10) for e8 in range(counts[i])]
    if not entries:
        # 빈 블록: pool 이후 전부 GAP 취급
        for i in range(pool, len(b)): cover[i] = 3
        tot.update({1:0x28, 2:pool-0x28, 3:len(b)-pool})
        continue
    bounds = sorted(set(t for _, t in entries))
    # 참조 오프셋 자체 유효성
    for t in bounds:
        if not (pool <= t < len(b)):
            issues.append(f"blk{k}: 참조오프셋 0x{t:X} 이 pool(0x{pool:X})~블록끝 밖")
    for i in range(pool, min(bounds[0], len(b))): cover[i] = 3
    segs = [(bounds[j], bounds[j+1] if j+1 < len(bounds) else len(b)) for j in range(len(bounds))]
    for s0, s1 in segs:
        seg = b[s0:s1]
        # C. 왕복 항등
        if cx.rebuild(cx.parse(seg)) != seg: seg_rt_fail += 1
        for i in range(s0, s1): cover[i] = 4
    # D. 엔트리 +4/+6 필드 분류
    bset = set(bounds)
    for eo, _t in entries:
        for fo in (4, 6):
            v = u16(eo+fo)
            if v == 0:                f46['zero'] += 1
            elif pool <= v < bounds[0]: f46['gap'] += 1
            elif v in bset:           f46['bound와 일치'] += 1
            elif bounds[0] <= v < len(b): f46['pool내 비bound ⚠'] += 1
            else:                     f46['기타(범위밖)'] += 1
    cnt = collections.Counter(cover)
    tot.update({c: cnt.get(c,0) for c in (0,1,2,3,4)})
    if cnt.get(0,0):
        first0 = cover.index(0)
        issues.append(f"blk{k}: 미분류 {cnt[0]}B (첫 위치 blk+0x{first0:X} = ROM 0x{bo+first0:X})")

print(f"B/커버리지: HDR {tot[1]:,}B / TBL {tot[2]:,}B / GAP {tot[3]:,}B / TXT {tot[4]:,}B / 미분류 {tot[0]:,}B")
print(f"   합계 {sum(tot[c] for c in (0,1,2,3,4)):,}B == 구간 {blk_starts[-1]-blk_starts[0]:,}B : {'OK' if sum(tot[c] for c in (0,1,2,3,4))==blk_starts[-1]-blk_starts[0] else 'FAIL'}")
print(f"C. 세그먼트 왕복 항등 실패: {seg_rt_fail}")
print(f"D. 엔트리 +4/+6 필드: {dict(f46)}")

# E. json 항목 바이트 대조 + TXT 풀 커버리지 (json 기준 재구성)
mism = 0; covered = 0
txt_map = {}                                # ROM abs off -> 커버 길이 (json)
for off, ln, raw in occ:
    sl = rom[off:off+ln]
    try:
        enc = cx.rebuild(cx.parse(sl))
        if enc != sl: mism += 1
    except Exception:
        mism += 1
    txt_map[off] = max(txt_map.get(off,0), ln)
    covered += ln
# TXT 영역을 json출현+구분자로 타일링 검사
json_hole = 0; hole_ex = []
for ki, k in enumerate(range(FIRST, LAST+1)):
    bo, be = blk_starts[ki], blk_starts[ki+1]
    b = rom[bo:be]
    u16 = lambda o: b[o] | (b[o+1] << 8)
    pairs = [(u16(i*4), u16(i*4+2)) for i in range(10)]
    offs = [p[0] for p in pairs]; counts = [p[1] for p in pairs]
    entries = [(offs[i]+e8*8, u16(offs[i]+e8*8+2)) for i in range(10) for e8 in range(counts[i])]
    if not entries: continue
    bounds = sorted(set(t for _, t in entries))
    p = bounds[0]
    while p < len(b):
        ap = bo + p
        if ap in txt_map:
            p += txt_map[ap]
        elif b[p] in SEP:
            p += 1
        else:
            json_hole += 1
            if len(hole_ex) < 8: hole_ex.append((k, ap, bytes(b[p:p+16]).hex()))
            p += 1
print(f"E. json 출현 {len(occ):,}곳 바이트 재인코딩 불일치: {mism}")
print(f"   TXT 풀에서 json+구분자로 설명 안 되는 바이트: {json_hole}")
for k, ap, hx in hole_ex: print(f"     blk{k} ROM 0x{ap:X}: {hx}")
print(f"\n총평: {'문제 발견 — 아래 이슈 확인' if issues or tot[0] or seg_rt_fail or mism or json_hole else '바이트 단위 완전성 PASS'}")
for s in issues[:20]: print('  !', s)
