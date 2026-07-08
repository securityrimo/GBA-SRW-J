# -*- coding: utf-8 -*-
"""슈퍼로봇대전 J 전투 대사 전 블록(194~370) 한글 삽입 도구.
- 매핑: KS X 1001 한글 ↔ SJIS L1 한자 (폰트가 한글로 렌더)
- 블록 구조: 10쌍 헤더 + cat0~9 포인터테이블(8B엔트리) + 텍스트풀
- 토큰 단위 번역(선두 ！ 제거해 unique 매칭, 재부착), 포인터 textOffset 재계산
- 변경된 블록만 확장영역에 순차 재배치, 인덱스 갱신
- 무번역 토큰/비대사는 원본 바이트 유지(왕복 항등)
"""
import json, struct, re
from srwj_battle_codec import BattleCodec
import srwj_archive as A
# (kouji_tr 폴백은 build_battle_json.py 단계에서 ko에 반영되므로 여기선 불필요)

FIRST, LAST = 193, 370
# blk193은 합동공격(합체기) 전용 블록이다. 과거엔 '도몬 버그'(화자가 도몬으로
# 고정)가 대사 바이트 길이 변화 때문이라 보고 blk193만 원위치 길이보존(짧으면
# 공백 패딩 / 길면 원문 유지)했으나, 실제 원인은 '한 대사가 4줄 이상'일 때였다.
# 따라서 길이보존은 불필요(오히려 과축약·일본어잔존 유발)하므로 제거하고,
# blk193도 다른 블록처럼 확장영역에 재배치한다. 대신 각 대사를 ≤3줄로 유지한다.
LEN_PRESERVE_BLOCKS = set()   # (도몬 버그는 4줄+ 문제 → 길이보존 폐지, blk193도 재배치)
PAD = 0x20   # 길이보존용 패딩 바이트(반각 공백)
MARK=re.compile(r'\[[0-9a-f]{2}\]')

def _mapping():
    hanguls=[bytes([0xB0+i//94,0xA1+i%94]).decode('euc-kr') for i in range(2350)]
    sj=[]; hi,lo=0x88,0x9f
    while len(sj)<2350:
        try:
            if '\u4e00'<=bytes([hi,lo]).decode('cp932')<='\u9fff': sj.append((hi,lo))
        except: pass
        lo+=1
        if lo==0x7f: lo=0x80
        if lo>0xfc: lo=0x40; hi+=1
    return {hanguls[i]:list(sj[i]) for i in range(2350)}

class BattleKRInserter:
    def __init__(self, rom_path, json_path):
        self.rom=bytearray(open(rom_path,'rb').read())
        self.cx=BattleCodec(self.rom)
        self.cx.set_gaiji(_mapping())
        doc=json.load(open(json_path, encoding='utf-8'))
        ents = doc['entries'] if isinstance(doc, dict) and 'entries' in doc else doc
        # battle_dialogue.json: lead(대사 시작 마커)+jp(원문)+ko(번역) 분리 형식.
        # raw(=lead+jp) 를 키로, (lead, ko) 를 값으로 저장. 패치 때 lead 는 그대로
        # 보존하고 ko 만 정규화해 enc(lead)+enc(ko) 로 합치므로 마커가 누락되지 않는다.
        # ko 가 비었거나(비대사) 일본어 잔존(미완성)이면 제외 → 원본(마커+원문) 유지.
        self.uni={}
        self.enc_fails=[]      # (원문 앞40자, 오류) — 인코딩 실패로 일본어로 남은 토큰
        for x in ents:
            if 'lead' in x:                         # 신형(마커 분리)
                lead=x.get('lead',''); jp=x.get('jp',''); ko=x.get('ko','')
                raw=lead+jp
            else:                                   # 구형 호환(jp=raw 통짜)
                lead=''; raw=x.get('jp',''); ko=x.get('ko', x.get('tr','')) or ''
            if not raw or not ko or not ko.strip(): continue
            if self.hjs(ko): continue
            self.uni[raw]=(lead, ko)

    def normalize(self, s):
        out=[]
        for p in re.split(r'(\[[0-9a-f]{2}\])', s):
            if MARK.fullmatch(p): out.append(p); continue
            p=p.replace('…','・・・').replace('...','・・・')
            p=p.replace('—','―').replace('–','―')   # em/en dash → 전각 가로줄(SJIS 가능)
            # 유사 가운뎃점 → 전각 나카구로(코드표 0x01). U+00B7 등을 방치하면
            # 인코딩 예외로 그 대사가 통째로 일본어로 남는다(blk305 사례).
            for dot in '·•∙ㆍ': p=p.replace(dot,'・')
            q=[]
            for ch in p:
                o=ord(ch)
                if ch=='~': q.append('\u301c')
                elif ch=='-': q.append('\u2212')      # 하이픈마이너스 → 전각 마이너스(SJIS 가능)
                elif 0x21<=o<=0x7d: q.append(chr(o+0xFEE0))
                elif ch==' ': q.append('\u3000')
                else: q.append(ch)
            out.append(''.join(q))
        return ''.join(out)

    @staticmethod
    def hjs(s):  # ・(U+30FB) 제외 일본어 잔존
        return any(('\u3040'<=c<='\u30fa')or('\u30fc'<=c<='\u30ff')or('\u4e00'<=c<='\u9fff') for c in s)

    def tr_token(self, text):
        v=self.uni.get(text)              # text = ROM 원문(raw = lead+jp)
        if v is None: return None         # 매핑 없음 → 원본 바이트 유지
        lead, ko = v
        return lead + self.normalize(ko)  # lead(마커)는 그대로, ko만 정규화

    def rebuild_block(self, b, apply_tr=True, length_preserve=False):
        # length_preserve=True 면 각 토큰을 원본과 같은 바이트 길이로 맞춘다
        # (한글이 짧으면 공백 패딩, 길면 원문 유지). blk193 연출 동기 보존용.
        u16=lambda o: b[o]|(b[o+1]<<8)
        pairs=[(u16(i*4),u16(i*4+2)) for i in range(10)]
        counts=[p[1] for p in pairs]; offs=[p[0] for p in pairs]
        pool_start=offs[9]+counts[9]*8
        entries=[(offs[i]+e*8, u16(offs[i]+e*8+2)) for i in range(10) for e in range(counts[i])]
        if not entries: return bytes(b), 0
        bounds=sorted(set(t for _,t in entries))+[len(b)]
        newpool=bytearray(b[pool_start:bounds[0]]); newmap={}; cur=pool_start+len(newpool); n=0
        for j in range(len(bounds)-1):
            seg=b[bounds[j]:bounds[j+1]]; newmap[bounds[j]]=cur
            out=bytearray()
            for t in self.cx.parse(seg):
                if t[0]=='t' and apply_tr:
                    kr=self.tr_token(t[1])
                    if kr is not None:
                        try:
                            enc=self.cx.enc_text(kr)
                            if length_preserve:
                                orig=self.cx.rebuild([t])
                                if len(enc)<=len(orig):
                                    enc=enc+bytes([PAD])*(len(orig)-len(enc))  # 공백 패딩
                                    out+=enc; n+=1; continue
                                # 번역이 원본보다 길면 길이보존 불가 → 원문 유지
                            else:
                                out+=enc; n+=1; continue
                        except Exception as ex:
                            # 인코딩 실패 = 이 대사가 게임에 일본어로 남는다.
                            # 조용히 넘기지 말고 기록해 빌드 끝에 경고한다.
                            self.enc_fails.append((t[1][:40], str(ex)))
                out+=self.cx.rebuild([t])
            newpool+=out; cur+=len(out)
        nb=bytearray(b[:pool_start])
        for eo,told in entries: struct.pack_into('<H', nb, eo+2, newmap[told])
        nb+=newpool
        return bytes(nb), n

    def find_free(self, need, start=0x1000000, end=0x2000000):
        i=start; run=0; rs=start; step=0x100
        while i<end:
            chunk=self.rom[i:i+step]
            if all(x in (0,0xff) for x in chunk):
                if run==0: rs=i
                run+=len(chunk)
                if run>=need+0x200: return (rs+0xF)&~0xF
            else: run=0
            i+=step
        return None

    def build(self, out_path):
        # 1) 항등 self-test
        for k in (FIRST, 250, 329, LAST):
            b=self.rom[self.cx.blkoff(self.rom,k):self.cx.blkoff(self.rom,k+1)]
            idb,_=self.rebuild_block(b, apply_tr=False)
            assert idb==b, f"블록{k} 항등 실패"
        # 2) 길이보존 블록(blk193 등): 재배치하지 않고 원위치에 동일 길이로 패치
        #    (연출 시퀀스 동기 보존 → 화자/대사 정상). 인덱스는 그대로 둔다.
        len_tok=0; len_over=0
        for k in sorted(LEN_PRESERVE_BLOCKS):
            SA=self.cx.blkoff(self.rom,k); sz=self.cx.blkoff(self.rom,k+1)-SA
            b=bytes(self.rom[SA:SA+sz])
            nb,n=self.rebuild_block(b, apply_tr=True, length_preserve=True)
            assert len(nb)==sz, f"블록{k} 길이보존 실패 ({len(nb)}!={sz})"
            self.rom[SA:SA+sz]=nb; len_tok+=n
        # 3) 나머지 블록: 재구성 후 확장영역 순차 재배치
        changed={}; tot_tok=len_tok
        for k in range(FIRST, LAST+1):
            if k in LEN_PRESERVE_BLOCKS: continue   # 위에서 원위치 처리됨
            b=self.rom[self.cx.blkoff(self.rom,k):self.cx.blkoff(self.rom,k+1)]
            nb,n=self.rebuild_block(b, apply_tr=True)
            if n>0 and nb!=b: changed[k]=nb; tot_tok+=n
        total=sum(len(v) for v in changed.values())
        dest=self.find_free(total)
        assert dest, f"확장영역 빈공간 부족(필요 0x{total:X})"
        cur=dest
        for k in sorted(changed):
            nb=changed[k]
            self.rom[cur:cur+len(nb)]=nb
            A.write_index(self.rom, k, cur - A.IDX_BASE)
            cur=(cur+len(nb)+0xF)&~0xF
        open(out_path,'wb').write(self.rom)
        if self.enc_fails:
            print(f"[경고] 인코딩 실패로 일본어로 남은 토큰 {len(self.enc_fails)}개:")
            for txt,err in self.enc_fails[:10]:
                print(f"    {txt!r} ← {err}")
        return {'changed':len(changed), 'len_preserved':len(LEN_PRESERVE_BLOCKS),
                'len_tokens':len_tok, 'tokens':tot_tok, 'dest':dest,
                'used':cur-dest, 'rom':len(self.rom),
                'enc_fails':len(self.enc_fails)}

if __name__=='__main__':
    import sys
    ins=BattleKRInserter('input.gba','battle_dialogue.json')
    print(f"번역 사전: unique {len(ins.uni)}개 + 마징가 폴백")
    r=ins.build('srwj_battle_kr.gba')
    print(f"변경 블록: {r['changed']}/177, 번역 토큰: {r['tokens']}개")
    print(f"확장영역 0x{r['dest']:X}부터 0x{r['used']:X} 사용, ROM 0x{r['rom']:X}")
    print("저장: srwj_battle_kr.gba")
