# -*- coding: utf-8 -*-
"""시나리오 대사: JP 줄수 ≫ KR 자동줄바꿈 줄수 → 후행 빈 줄(닫힘 괄호만) 집계.

빌드(build_korean_block)와 동일 경로로 전 번역 턴을 처리해,
fit_turn_lines 결과의 '후행 빈 줄 수'(= JP 줄수를 채우려 pad된 빈 줄)를 센다.
게임은 턴 마지막 줄 끝에 닫는 괄호를 붙이므로, 후행 빈 줄이 있으면 그 괄호가
빈 줄에 홀로 떠서 "빈 대사에 닫힘 괄호만" 으로 보인다.

사용: python audit_trailing_empty.py [jp롬] [xlsx]
"""
import sys, collections
import srwj_decode as D
import srwj_parser as P
from srwj_wrap import fit_turn_lines
from patch_all import load_merged_xlsx

JP  = sys.argv[1] if len(sys.argv) > 1 else 'Super Robot Taisen J (Japan).gba'
XLS = sys.argv[2] if len(sys.argv) > 2 else 'srwj_matched_all_0625.xlsx'
RESERVE = 7

rom = D.load_rom(JP)
dic = D.Dictionary(rom)
idx = list(D.load_archive_index(rom))
blocks = D.find_all_dialogue_blocks(rom)
by_archive = {m['archive_idx']: m for m in blocks}

kr_per_block, spk_per_block = load_merged_xlsx(XLS)

pad_hist = collections.Counter()     # 후행 빈 줄 수 → 턴 수
over_hist = collections.Counter()    # (len-jp) 초과 → 턴 수 (KR이 더 긺)
examples = collections.defaultdict(list)
tot_kr = 0

for ai in sorted(kr_per_block):
    if ai not in by_archive:
        continue
    meta = by_archive[ai]
    info = P.parse_dialogue_block(rom, meta['rom_addr'], meta['block_size'], dic)
    flat = [t for dlg in info['dialogues'] for t in dlg['turns']]
    kr_by = kr_per_block[ai]
    spk_by = spk_per_block.get(ai, {})
    for i, turn in enumerate(flat):
        kr = kr_by.get(i)
        if not kr or not str(kr).strip():
            continue
        tot_kr += 1
        jp_lines = len(turn['lines'])
        spk = spk_by.get(i)
        lines, warns = fit_turn_lines(str(kr), jp_lines, RESERVE, speaker=spk)
        # 후행 빈 줄 수
        te = 0
        for s in reversed(lines):
            if s.strip('　') == '':
                te += 1
            else:
                break
        if len(lines) > jp_lines:                 # KR이 더 긺(오버플로)
            over_hist[len(lines) - jp_lines] += 1
        if te >= 1:
            pad_hist[te] += 1
            if len(examples[te]) < 4:
                examples[te].append((ai, i, jp_lines, len(lines)-te,
                                     str(kr)[:38].replace('\n', '\\n'), lines))

print(f"번역 턴 총수: {tot_kr:,}")
print("\n=== 후행 빈 줄(닫힘 괄호만) 분포 — JP 줄수 > KR 줄바꿈 줄수 ===")
cum = 0
for te in sorted(pad_hist, reverse=True):
    cum += pad_hist[te]
for te in sorted(pad_hist):
    print(f"  후행 빈 줄 {te}개: {pad_hist[te]:>5} 턴")
tot_pad = sum(pad_hist.values())
big = sum(v for k, v in pad_hist.items() if k >= 2)
big3 = sum(v for k, v in pad_hist.items() if k >= 3)
print(f"  ─ 빈 줄 ≥1 (괄호 뜸)   : {tot_pad:,} 턴")
print(f"  ─ 빈 줄 ≥2 (차이 큼)   : {big:,} 턴")
print(f"  ─ 빈 줄 ≥3 (차이 매우큼): {big3:,} 턴")

print("\n=== 참고: KR이 JP보다 길어 넘치는 턴(오버플로) ===")
for d in sorted(over_hist):
    print(f"  +{d}줄 초과: {over_hist[d]:>5} 턴")
print(f"  ─ 오버플로 합계: {sum(over_hist.values()):,} 턴")

print("\n=== 예시 (후행 빈 줄 많은 순) ===")
for te in sorted(examples, reverse=True)[:4]:
    for ai, i, jpl, wl, kr, lines in examples[te][:3]:
        shown = [repr(s) for s in lines]
        print(f"  [빈{te}] archive{ai} 턴{i}: JP {jpl}줄 → KR {wl}줄  ko={kr!r}")
        print(f"          결과 줄: {shown}")
