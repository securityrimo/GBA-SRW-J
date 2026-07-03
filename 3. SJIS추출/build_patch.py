# -*- coding: utf-8 -*-
"""슈퍼로봇대전 J 한글 패치 빌드.

translations.json 의 번역(ko)을 한글 폰트 ROM(out.gba)에 적용해 패치 ROM을 만든다.
    · 원문 슬롯 안에 들어가면      → 제자리에 기록 (슬롯 전체 0x00 정리)
    · 원문 슬롯을 넘치면(번역이 김) → 자유공간에 재배치하고 ROM의 포인터를 갱신
    · 문자열 끝 잔여 0x80(게임에서 ■로 깨짐)은 자동 청소

사용법
    1) input/out.gba  : 한글 폰트가 적용된 ROM (패치 대상)
       input/jp.gba   : 일본어 원본 ROM (선택, 참고용)
    2) python build_patch.py
    3) output/슈퍼로봇대전J_한글.gba 생성

번역을 고치려면 translations.json 의 'ko' 만 수정하고 다시 실행하면 된다.
"""
import os, sys, json, struct, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import srwj_codec as codec

HERE       = os.path.dirname(os.path.abspath(__file__))
TRANS_JSON = os.path.join(HERE, 'translations.json')
IN_FONT    = os.path.join(HERE, 'input', 'out.gba')      # 한글 폰트 ROM (패치 대상)
IN_JP      = os.path.join(HERE, 'input', 'jp.gba')       # 일본어 원본 (선택)
OUT_DIR    = os.path.join(HERE, 'output')
OUT_ROM    = os.path.join(OUT_DIR, '슈퍼로봇대전J_한글.gba')
REPORT     = os.path.join(OUT_DIR, 'build_report.json')

GBA          = 0x08000000
FREE_BASE    = 0x1240000             # 자유공간 시작 (0xFF 영역, 폰트/데이터와 무충돌)
SAFE_LO, SAFE_HI = 0x090000, 0x1E0000  # 포인터 갱신 안전구역 (4바이트 정렬만)
# 안전구역 밖에 있는 포인터를 명시적으로 갱신 (갱신 전 옛 포인터값을 재검증함)
# 0x77EE8/0x7ADxx 는 Thumb 코드 리터럴 풀(LDR용 주소 상수) — 각 문자열 참조 1곳뿐
EXTRA_PTRS = {
    0x0008362C: [0x1909C],    # 統夜(토우야) 이름 테이블 포인터
    0x000936FC: [0x77EE8],    # 戦闘を起動 (전투 기동 디버그 메뉴)
    0x000946C4: [0x7AD28],    # 数え切れない昔からの、 (엔딩 내레이션 1)
    0x000946DC: [0x7AD38],    # 数え切れない巡り合わせの果てに・・・ (엔딩 내레이션 2)
    0x00094704: [0x7AD8C],    # 私達はここにいる・・・ (엔딩 내레이션 3)
}


def find_pointers(rom, off):
    """off(=ROM오프셋)를 가리키는 4바이트 LE 포인터 위치들을 찾는다."""
    target = struct.pack('<I', off + GBA)
    locs, start = [], SAFE_LO
    while True:
        i = rom.find(target, start, SAFE_HI)
        if i < 0:
            break
        if i % 4 == 0:
            locs.append(i)
        start = i + 1
    for ex in EXTRA_PTRS.get(off, []):                       # 안전구역 밖 명시 포인터
        if ex % 4 == 0 and bytes(rom[ex:ex+4]) == target and ex not in locs:
            locs.append(ex)
    return locs


def clean_trailing_garbage(rom, slots):
    """각 슬롯에서 '유효 문자열 종료 직전의 단독 0x80'(■로 깨지는 잔여바이트)을 0x00으로."""
    cleaned = 0
    for off, L in slots:
        i, end = off, off + L + 4
        while i < end:
            c = rom[i]
            if c == 0x00:
                break
            if c == 0x80:                  # 문자 시작 위치의 단독 0x80 = 잔여 garbage
                rom[i] = 0x00; cleaned += 1; break
            i += 2 if (0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC) else 1
    return cleaned


def main():
    for path, desc in [(TRANS_JSON, 'translations.json'), (IN_FONT, 'input/out.gba')]:
        if not os.path.exists(path):
            print(f"[오류] {desc} 가 없습니다: {path}")
            if desc == 'input/out.gba':
                print("      한글 폰트 ROM을 input/out.gba 로 넣어주세요.")
            return 1

    data = json.load(open(TRANS_JSON, encoding='utf-8'))['entries']
    rom  = bytearray(open(IN_FONT, 'rb').read())
    os.makedirs(OUT_DIR, exist_ok=True)

    ents = sorted(data, key=lambda x: int(x['off'], 16))

    inplace = overflow = skipped = 0
    overflow_items, skip_items = [], []
    slots = []                                                # (offset, len) — 잔여바이트 청소용

    # 1) 제자리에 들어가는 항목 기록 / 넘치는 항목은 따로 모음
    #    슬롯 가용 공간 = 다음 항목까지의 거리(원문 길이 + 정렬 패딩). 널 종료 1바이트는 남겨야 함.
    for i, e in enumerate(ents):
        off = int(e['off'], 16); L = e['len']; ko = e['ko']
        nxt = int(ents[i+1]['off'], 16) if i + 1 < len(ents) else off + L + 4
        # 실제 가용 공간 = 원문(L) 뒤에 이어지는 0x00 패딩까지.
        #   그 뒤의 0이 아닌 바이트는 translations.json에 없더라도 별개의 문자열
        #   (예: 「？？？」「ＡＩ」 같은 미번역 기호/이름·스태프닉)이므로 침범 금지.
        #   패딩이 다음 항목까지 이어지면 avail=nxt-off 가 되어 기존과 동일(재배치 최소화 유지).
        p = off + L
        while p < nxt and rom[p] == 0x00:
            p += 1
        avail = p - off                                       # 널 종료 포함해 실제로 쓸 수 있는 바이트
        slots.append((off, L))

        enc, bad = codec.encode(ko)
        if bad:
            skipped += 1
            skip_items.append({'off': e['off'], 'jp': e['jp'], 'ko': ko, 'bad': bad})
            continue

        if len(enc) < avail:                                  # 제자리 기록 (널 종료 보장, 번역 원형 유지)
            fill_to = min(max(L, len(enc) + 1), avail)
            rom[off:off+fill_to] = enc + b'\x00' * (fill_to - len(enc))
            inplace += 1
        else:                                                 # 슬롯+패딩에도 안 들어가면 재배치
            overflow_items.append({'off': e['off'], 'jp': e['jp'], 'ko': e['ko'],
                                   'enc_len': len(enc), 'avail': avail})

    # 2) 자유공간이 비어있는지 확인
    if any(b != 0xFF for b in rom[FREE_BASE:FREE_BASE+0x40]):
        print(f"[경고] 0x{FREE_BASE:X} 부근이 0xFF가 아닙니다 — FREE_BASE 재확인 필요.")

    # 3) 넘치는 항목을 자유공간에 재배치하고 포인터 갱신
    wp = FREE_BASE
    reloc_report, no_pointer = [], []
    for it in overflow_items:
        off = int(it['off'], 16)
        enc, _ = codec.encode(it['ko'])
        locs = find_pointers(rom, off)
        if not locs:
            no_pointer.append(it); continue
        new_off = wp
        rom[new_off:new_off+len(enc)] = enc
        rom[new_off+len(enc)] = 0x00
        wp = new_off + len(enc) + 1
        if wp % 2:
            wp += 1
        new_ptr = struct.pack('<I', new_off + GBA)
        for loc in locs:
            rom[loc:loc+4] = new_ptr
        overflow += 1
        reloc_report.append({**it, 'new_off': hex(new_off), 'ptrs': len(locs)})

    # 4) 잔여 0x80 청소
    garbage = clean_trailing_garbage(rom, slots)

    # 5) 저장 + 보고
    open(OUT_ROM, 'wb').write(rom)
    json.dump({
        'inplace': inplace, 'relocated': overflow, 'skipped_bad': skipped,
        'garbage_cleaned': garbage,
        'free_base': hex(FREE_BASE), 'free_used': wp - FREE_BASE,
        'relocations': reloc_report,
        'no_pointer': no_pointer,
        'skipped_chars': skip_items,
    }, open(REPORT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print(f"제자리 기록 : {inplace}")
    print(f"재배치      : {overflow}  (자유공간 0x{FREE_BASE:X}~0x{wp:X}, {wp-FREE_BASE}B)")
    print(f"잔여0x80 청소: {garbage}")
    if skipped:
        print(f"[주의] 인코딩 불가 문자로 건너뛴 항목: {skipped}  (build_report.json 참고)")
        for it in skip_items[:5]:
            print(f"    {it['off']} {it['ko']}  ← {it['bad']}")
    if no_pointer:
        print(f"[주의] 포인터를 못 찾아 재배치 못한 항목: {len(no_pointer)}")
        for it in no_pointer[:5]:
            print(f"    {it['off']} {it['jp']} → {it['ko']}")
    print(f"\n저장: {OUT_ROM}")
    print(f"md5 : {hashlib.md5(rom).hexdigest()}   크기: {len(rom):,} bytes")
    return 0


if __name__ == '__main__':
    sys.exit(main())
