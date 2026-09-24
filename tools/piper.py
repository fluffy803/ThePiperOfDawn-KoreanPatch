# -*- coding: utf-8 -*-
"""The Piper Of Dawn 한국어 패치 관리 도구.

  python tools/piper.py check  [--game DIR]          게임과 번역 비교 -> work/todo_*.json
  python tools/piper.py merge  FILE [FILE ...]        번역 결과({id: 한국어}) 반영 + 원문해시 기록
  python tools/piper.py build  [--game DIR] [--install] [--allow-stale]
                                                      덮어쓰기용 zip 생성 (dist/)

번역은 게임의 "영어" 컬럼에 들어간다(게임 언어를 English로 설정하면 한국어 표시).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import UnityPy  # noqa: E402

import bundle_crypto as bc  # noqa: E402
import crc_forge  # noqa: E402
import table_crypto as tc  # noqa: E402
import table_proto as tp  # noqa: E402
import table_ser as ts  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TR_DIR = os.path.join(ROOT, "translations")
META_DIR = os.path.join(ROOT, "meta")
WORK_DIR = os.path.join(ROOT, "work")      # git-ignored: 게임 원문이 들어가는 작업물
DIST_DIR = os.path.join(ROOT, "dist")      # git-ignored: 배포 zip

DEFAULT_GAME = r"C:\Program Files (x86)\Steam\steamapps\common\The Piper Of Dawn"
MAIN_REL = os.path.join("ThePiper_Data", "StreamingAssets", "yoo", "Main")
TABLE_BUNDLE = "assets_gameres_table.bundle"
PAD_MARK = b"PIPERPAD"
MAX_STR = 4000  # 게임 문자열 디코드 버퍼 char[4096]

# 행 필드: 1=id, sc=간체, tc=번체, en=영어(<-한국어), jp=일본어
FIELDMAP = {
    "Language":     dict(sc=2, tc=3, en=4, jp=5),
    "LanguageTalk": dict(sc=2, tc=4, en=5, jp=6),
}


# ---------------------------------------------------------------- utils
def jload(p, default=None):
    if not os.path.exists(p):
        return {} if default is None else default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=isinstance(obj, dict))
        f.write("\n")


def src_hash(row, F):
    """번역 기준 원문(중국어+일본어, 둘 다 비면 영어)의 해시."""
    cn, jp = row.get(F["sc"], ""), row.get(F["jp"], "")
    s = cn + "\x1f" + jp if (cn or jp) else "\x1e" + row.get(F["en"], "")
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def has_text(row, F):
    return any(row.get(F[k], "").strip() for k in ("sc", "en", "jp"))


# ---------------------------------------------------------------- game access
class Game:
    def __init__(self, game_dir):
        self.dir = game_dir or DEFAULT_GAME
        self.main = os.path.join(self.dir, MAIN_REL)
        if not os.path.isdir(self.main):
            sys.exit(f"게임 폴더를 찾을 수 없음: {self.main}  (--game 으로 지정)")
        with open(os.path.join(self.main, "PackageManifest_Main.version"), encoding="utf-8") as f:
            self.version = f.read().strip()
        man = open(os.path.join(self.main, f"PackageManifest_Main_{self.version}.bytes"), "rb").read()
        self.bundle_hash = self._find_hash(man, TABLE_BUNDLE)
        self.bundle_path = os.path.join(self.main, self.bundle_hash + ".bundle")

    @staticmethod
    def _find_hash(man, name):
        pend = None
        for m in re.finditer(rb"[A-Za-z0-9_\-./]+\.bundle|[0-9a-f]{32}", man):
            t = m.group().decode()
            if t.endswith(".bundle"):
                pend = t
            elif pend:
                if pend.endswith(name) and pend.split("/")[-1] == name:
                    return t
                pend = None
        sys.exit(f"매니페스트에서 {name} 을 찾지 못함")

    def pristine_bundle(self):
        """원본 번들 바이트. YooAsset 파일명=원본 MD5 이므로 그걸로 원본 여부 판별."""
        backup = os.path.join(WORK_DIR, "backup", self.bundle_hash + ".bundle")
        data = open(self.bundle_path, "rb").read()
        if hashlib.md5(data).hexdigest() == self.bundle_hash:
            if not os.path.exists(backup):
                os.makedirs(os.path.dirname(backup), exist_ok=True)
                shutil.copyfile(self.bundle_path, backup)
            return data
        if os.path.exists(backup):
            return open(backup, "rb").read()
        sys.exit("설치된 테이블 번들이 원본이 아니고 백업도 없음 -> Steam '게임 파일 무결성 검사' 후 다시 실행")

    def load(self):
        """(env, {name: (TextAsset obj, stored bytes, keys, rows)}, 원본 본문 CRC)"""
        env = UnityPy.load(bc.crypt(self.pristine_bundle(), TABLE_BUNDLE))
        (_, sf), = env.file.files.items()
        crc = zlib.crc32(sf.reader.bytes) & 0xFFFFFFFF
        tables = {}
        for obj in env.objects:
            if obj.type.name != "TextAsset":
                continue
            d = obj.read()
            if d.m_Name not in FIELDMAP:
                continue
            raw = d.m_Script
            stored = bytes(raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8", "surrogateescape"))
            _, keys, rows = tp.parse_table(tc.decrypt(stored), lenient=False)
            tables[d.m_Name] = (d, stored, keys, rows)
        return env, tables, crc


# ---------------------------------------------------------------- UI prefab (하드코딩 텍스트)
# 일부 UI(낚시 보물상자 팝업 등)는 프리팹의 UGUI Text.m_Text 에 중국어가 박혀 있어 언어 설정과 무관.
# 번역: translations/ui_prefab.ko.json = {"<번들명>": {"<path_id>[|<필드경로>]": "한국어"}}
# 프리팹 번들엔 CRC 위조용 빈 공간이 없으므로 매니페스트의 ContentCRC 를 새 값으로 바꾸고
# .hash(= 매니페스트 MD5)를 다시 쓴다. 따라서 이 패치가 들어간 zip 은 게임 버전이 정확히 맞아야 한다.
UI_FILE = "ui_prefab"


def _field_get(tree, path):
    cur = tree
    for part in path.split("."):
        m = re.match(r"(\w+)(?:\[(\d+)\])?$", part)
        cur = cur[m.group(1)]
        if m.group(2) is not None:
            cur = cur[int(m.group(2))]
    return cur


def _field_set(tree, path, value):
    parts = path.split(".")
    cur = tree
    for part in parts[:-1]:
        m = re.match(r"(\w+)(?:\[(\d+)\])?$", part)
        cur = cur[m.group(1)]
        if m.group(2) is not None:
            cur = cur[int(m.group(2))]
    m = re.match(r"(\w+)(?:\[(\d+)\])?$", parts[-1])
    if m.group(2) is None:
        cur[m.group(1)] = value
    else:
        cur[m.group(1)][int(m.group(2))] = value


def _split_key(key):
    pid, _, field = key.partition("|")
    return int(pid), (field or "m_Text")


def content_crc(env):
    """Unity ContentCRC = 번들 내 모든 파일(CAB, .resS …)의 압축해제 바이트를 순서대로 이은 CRC32."""
    c = 0
    for fo in env.file.files.values():
        raw = fo.reader.bytes if hasattr(fo, "reader") else fo.bytes
        c = zlib.crc32(raw, c)
    return c & 0xFFFFFFFF


def _manifest_paths(g):
    return (os.path.join(g.main, f"PackageManifest_Main_{g.version}.bytes"),
            os.path.join(g.main, f"PackageManifest_Main_{g.version}.hash"))


def _crc_offset(man, name, bundle_hash):
    """매니페스트에서 <번들명> 바로 뒤 uint32 LE = ContentCRC (그 뒤 len16 + 파일해시)."""
    nb = name.encode()
    i = man.find(nb)
    while i >= 0:
        j = i + len(nb)
        if man[j + 4:j + 6] == b"\x20\x00" and man[j + 6:j + 38] == bundle_hash.encode():
            return j
        i = man.find(nb, i + 1)
    sys.exit(f"매니페스트에서 {name} 의 CRC 위치를 찾지 못함")


def pristine_manifest(g):
    """원본 매니페스트 bytes. 처음 보는 버전이면 백업하되, 테이블 번들 항목 CRC가 원본과 맞는지로 원본 여부 확인."""
    bpath, _ = _manifest_paths(g)
    backup = os.path.join(WORK_DIR, "backup", os.path.basename(bpath))
    if os.path.exists(backup):
        return open(backup, "rb").read()
    man = open(bpath, "rb").read()
    env = UnityPy.load(bc.crypt(g.pristine_bundle(), TABLE_BUNDLE))
    off = _crc_offset(man, TABLE_BUNDLE, g.bundle_hash)
    if int.from_bytes(man[off:off + 4], "little") != content_crc(env):
        sys.exit("설치된 매니페스트가 원본이 아님(백업 없음) -> Steam 무결성 검사 후 다시 실행")
    os.makedirs(os.path.dirname(backup), exist_ok=True)
    with open(backup, "wb") as f:
        f.write(man)
    return man


def pristine_named_bundle(g, name, bundle_hash):
    path = os.path.join(g.main, bundle_hash + ".bundle")
    backup = os.path.join(WORK_DIR, "backup", bundle_hash + ".bundle")
    data = open(path, "rb").read()
    if hashlib.md5(data).hexdigest() == bundle_hash:
        if not os.path.exists(backup):
            os.makedirs(os.path.dirname(backup), exist_ok=True)
            shutil.copyfile(path, backup)
        return data
    if os.path.exists(backup):
        return open(backup, "rb").read()
    sys.exit(f"{name} 번들이 원본이 아니고 백업도 없음 -> Steam 무결성 검사 후 다시 실행")


def ui_src_hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def ui_bundles(g):
    """{번들명: (hash, env)} for bundles referenced by ui_prefab.ko.json (원본 기준)."""
    tr = jload(os.path.join(TR_DIR, f"{UI_FILE}.ko.json"))
    man = pristine_manifest(g) if tr else b""
    out = {}
    for name in tr:
        h = Game._find_hash(man, name)
        out[name] = (h, UnityPy.load(bc.crypt(pristine_named_bundle(g, name, h), name)))
    return tr, man, out


# ---------------------------------------------------------------- check
def cmd_check(a):
    g = Game(a.game)
    _, tables, _ = g.load()
    print(f"게임 매니페스트 버전 {g.version}, 테이블 번들 {g.bundle_hash}")
    report = {"game_version": g.version, "bundle": g.bundle_hash, "tables": {}}
    for name, (_, _, _, rows) in tables.items():
        F = FIELDMAP[name]
        ko = jload(os.path.join(TR_DIR, f"{name}.ko.json"))
        hs = jload(os.path.join(META_DIR, f"{name}.srchash.json"))
        ids = {str(r[1]) for r in rows}
        todo, n_new, n_changed = [], 0, 0
        for r in rows:
            sid = str(r[1])
            if not has_text(r, F):
                continue
            item = {"id": r[1], "cn": r.get(F["sc"], ""), "en": r.get(F["en"], ""), "jp": r.get(F["jp"], "")}
            if sid not in ko:
                n_new += 1
                todo.append({**item, "status": "new"})
            elif hs.get(sid) != src_hash(r, F):
                n_changed += 1
                todo.append({**item, "status": "changed", "prev_ko": ko[sid]})
        removed = sorted((k for k in ko if k not in ids), key=int)
        jsave(os.path.join(WORK_DIR, f"todo_{name}.json"), todo)
        report["tables"][name] = dict(rows=len(rows), new=n_new, changed=n_changed, removed=len(removed))
        print(f"[{name}] 행 {len(rows)} | 신규 {n_new} | 원문변경 {n_changed} | 게임에서 삭제 {len(removed)}"
              f"  -> work/todo_{name}.json")
    # UI 프리팹: 번역한 위치가 사라졌거나 원문이 바뀐 것
    tr, _, bundles = ui_bundles(g)
    hs = jload(os.path.join(META_DIR, f"{UI_FILE}.srchash.json"))
    todo = []
    for bname, items in tr.items():
        _, env = bundles[bname]
        objs = {o.path_id: o for o in env.objects}
        for key, ko in items.items():
            pid, field = _split_key(key)
            o = objs.get(pid)
            if o is None:
                todo.append({"bundle": bname, "key": key, "status": "missing", "prev_ko": ko}); continue
            cn = _field_get(o.read_typetree(), field)
            if hs.get(bname, {}).get(key) != ui_src_hash(cn):
                todo.append({"bundle": bname, "key": key, "status": "changed", "cn": cn, "prev_ko": ko})
    if tr:
        jsave(os.path.join(WORK_DIR, f"todo_{UI_FILE}.json"), todo)
        n = sum(len(v) for v in tr.values())
        report["tables"][UI_FILE] = dict(entries=n, changed_or_missing=len(todo))
        print(f"[UI 프리팹] 번역 {n}곳 | 원문변경·위치소실 {len(todo)}  -> work/todo_{UI_FILE}.json")
    jsave(os.path.join(WORK_DIR, "check_report.json"), report)


# ---------------------------------------------------------------- merge
def merge_ui(g, path):
    new = jload(path)
    tr = jload(os.path.join(TR_DIR, f"{UI_FILE}.ko.json"))
    hs = jload(os.path.join(META_DIR, f"{UI_FILE}.srchash.json"))
    man = pristine_manifest(g)
    for bname, items in new.items():
        h = Game._find_hash(man, bname)
        env = UnityPy.load(bc.crypt(pristine_named_bundle(g, bname, h), bname))
        objs = {o.path_id: o for o in env.objects}
        n = skipped = 0
        for key, ko in items.items():
            pid, field = _split_key(key)
            o = objs.get(pid)
            if o is None or not isinstance(ko, str) or not ko.strip():
                skipped += 1
                continue
            cn = _field_get(o.read_typetree(), field)
            if cn.count("\n") != ko.count("\n"):
                sys.exit(f"[UI] {bname} {key}: 줄바꿈 개수 불일치")
            tr.setdefault(bname, {})[key] = ko
            hs.setdefault(bname, {})[key] = ui_src_hash(cn)
            n += 1
        print(f"[UI 프리팹] {bname}: {n}곳 반영, {skipped}곳 건너뜀(번들에 없는 위치/빈값)")
    jsave(os.path.join(TR_DIR, f"{UI_FILE}.ko.json"), tr)
    jsave(os.path.join(META_DIR, f"{UI_FILE}.srchash.json"), hs)


def cmd_merge(a):
    g = Game(a.game)
    _, tables, _ = g.load()
    rowmap = {name: {str(r[1]): r for r in t[3]} for name, t in tables.items()}
    for path in a.files:
        base = os.path.basename(path)
        if UI_FILE in base:
            merge_ui(g, path)
            continue
        name = next((n for n in sorted(FIELDMAP, key=len, reverse=True) if n in base), None)
        if not name:
            sys.exit(f"파일명에 테이블명(Language/LanguageTalk)이 없음: {path}")
        F = FIELDMAP[name]
        new = jload(path)
        ko = jload(os.path.join(TR_DIR, f"{name}.ko.json"))
        hs = jload(os.path.join(META_DIR, f"{name}.srchash.json"))
        n = skipped = 0
        for sid, text in new.items():
            sid = str(sid)
            r = rowmap[name].get(sid)
            if r is None or not isinstance(text, str) or not text.strip():
                skipped += 1
                continue
            if len(text) > MAX_STR:
                sys.exit(f"[{name}] id {sid} 문자열이 너무 김({len(text)}자)")
            ko[sid] = text
            hs[sid] = src_hash(r, F)
            n += 1
        jsave(os.path.join(TR_DIR, f"{name}.ko.json"), ko)
        jsave(os.path.join(META_DIR, f"{name}.srchash.json"), hs)
        print(f"[{name}] {base}: {n}행 반영, {skipped}행 건너뜀(게임에 없는 id/빈값)")


# ---------------------------------------------------------------- build
def pad_to_length(keys, rows, target):
    """테이블 난독화(XOR)의 역방향 패스 위치가 전체 길이에 의존하고 일부가 RSA로 고정된 앞 100바이트에
    떨어지므로 평문 길이를 원본과 정확히 같게 맞춰야 한다. 게임 리더가 디코딩 없이 건너뛰는
    최상위 미정의 필드 15에 PIPERPAD + 0 으로 채운다(문자열 필드에 넣으면 char[4096] 버퍼 초과)."""
    pt = ts.serialize_table(keys, rows)
    room = target - len(pt)
    if room < 2 + len(PAD_MARK) + 64:
        sys.exit(f"번역 후 테이블이 원본보다 큼(여유 {room}B) - 패딩 불가")
    tag = ts.enc_varint((15 << 3) | 2)
    n = room - len(tag) - 1
    for _ in range(8):
        pad = tag + ts.enc_varint(n) + PAD_MARK + b"\x00" * (n - len(PAD_MARK))
        if len(pad) == room:
            return pt + pad
        n += room - len(pad)
    sys.exit("패딩 길이 맞추기 실패")


def apply_ko(name, rows, allow_stale):
    F = FIELDMAP[name]
    ko = jload(os.path.join(TR_DIR, f"{name}.ko.json"))
    hs = jload(os.path.join(META_DIR, f"{name}.srchash.json"))
    out, st = [], dict(applied=0, stale_skipped=0, untranslated=0)
    for r in rows:
        r = dict(r)
        sid = str(r[1])
        text = ko.get(sid)
        if text and (allow_stale or hs.get(sid) == src_hash(r, F)):
            r[F["en"]] = text
            for k in ("sc", "tc"):
                if F[k] in r:
                    r[F[k]] = ""
            st["applied"] += 1
        elif text:
            st["stale_skipped"] += 1   # 원문이 바뀐 행: 틀린 번역 대신 영어 원문 유지
        elif has_text(r, F):
            st["untranslated"] += 1
        out.append(r)
    return out, st


def force_crc(env, stored_talk, obj_talk, target):
    """YooAsset이 매니페스트 CRC를 LoadFromStream에 넘기고 Unity가 압축해제 본문 CRC32를 검사한다.
    패딩 안 4바이트를 조정해 원본 CRC와 같게 만든다."""
    (_, sf), = env.file.files.items()
    S = bytearray(sf.save())
    pos = S.find(stored_talk)
    assert pos >= 0 and S.find(stored_talk, pos + 1) < 0
    run = stored_talk.rfind(PAD_MARK + b"\x00" * 32)
    assert run >= 0
    crc_forge.force_crc32(S, pos + run + len(PAD_MARK) + 8, target)
    new_stored = bytes(S[pos:pos + len(stored_talk)])
    obj_talk.m_Script = new_stored.decode("utf-8", "surrogateescape")
    obj_talk.save()
    assert zlib.crc32(sf.save()) & 0xFFFFFFFF == target


def cmd_build(a):
    g = Game(a.game)
    env, tables, crc = g.load()
    stats, talk = {}, None
    for name, (obj, stored, keys, rows) in tables.items():
        new_rows, st = apply_ko(name, rows, a.allow_stale)
        orig_pt = tc.decrypt(stored)
        pt = pad_to_length(keys, new_rows, len(orig_pt))
        assert pt[:100] == orig_pt[:100], "RSA 고정 헤더 영역 변경됨"
        new_stored = tc.encrypt(pt, stored[:128])
        assert tc.decrypt(new_stored) == pt
        obj.m_Script = new_stored.decode("utf-8", "surrogateescape")
        obj.save()
        if name == "LanguageTalk":
            talk = (obj, new_stored)
        stats[name] = st
        print(f"[{name}] 행 {len(rows)} | 적용 {st['applied']} | 원문변경으로 제외 {st['stale_skipped']}"
              f" | 미번역 {st['untranslated']}")
    force_crc(env, talk[1], talk[0], crc)
    out_bundle = bc.crypt(env.file.save(packer="lz4"), TABLE_BUNDLE)

    # 검증: 게임과 같은 경로로 다시 읽기
    chk = UnityPy.load(bc.crypt(out_bundle, TABLE_BUNDLE))
    (_, sf), = chk.file.files.items()
    assert zlib.crc32(sf.reader.bytes) & 0xFFFFFFFF == crc, "CRC 검증 실패"
    for obj in chk.objects:
        if obj.type.name == "TextAsset":
            d = obj.read()
            if d.m_Name in FIELDMAP:
                raw = d.m_Script
                raw = raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8", "surrogateescape")
                tp.parse_table(tc.decrypt(bytes(raw)))

    # 결과물: {게임폴더 기준 상대경로: bytes}
    files = {os.path.join(MAIN_REL, g.bundle_hash + ".bundle"): out_bundle}

    # UI 프리팹 하드코딩 텍스트
    tr, man, bundles = ui_bundles(g)
    if tr:
        hs = jload(os.path.join(META_DIR, f"{UI_FILE}.srchash.json"))
        man = bytearray(man)
        ui_stats = dict(applied=0, stale_skipped=0, missing=0)
        for bname, items in tr.items():
            h, uenv = bundles[bname]
            objs = {o.path_id: o for o in uenv.objects}
            trees = {}
            for key, ko in items.items():
                pid, field = _split_key(key)
                o = objs.get(pid)
                if o is None:
                    ui_stats["missing"] += 1; continue
                tree = trees.get(pid) or o.read_typetree()
                cn = _field_get(tree, field)
                if not a.allow_stale and hs.get(bname, {}).get(key) != ui_src_hash(cn):
                    ui_stats["stale_skipped"] += 1; continue
                _field_set(tree, field, ko)
                trees[pid] = tree
                ui_stats["applied"] += 1
            for pid, tree in trees.items():
                objs[pid].save_typetree(tree)
            plain = uenv.file.save(packer="lz4")
            chk = UnityPy.load(plain)
            new_crc = content_crc(chk)
            cobjs = {o.path_id: o for o in chk.objects}
            for key, ko in items.items():   # 검증: 저장본에 실제로 들어갔는지
                pid, field = _split_key(key)
                if pid in trees:
                    assert _field_get(cobjs[pid].read_typetree(), field) == ko, f"UI 반영 실패 {key}"
            off = _crc_offset(man, bname, h)
            man[off:off + 4] = new_crc.to_bytes(4, "little")
            files[os.path.join(MAIN_REL, h + ".bundle")] = bc.crypt(plain, bname)
        man = bytes(man)
        bpath, hpath = _manifest_paths(g)
        files[os.path.join(MAIN_REL, os.path.basename(bpath))] = man
        files[os.path.join(MAIN_REL, os.path.basename(hpath))] = hashlib.md5(man).hexdigest().encode()
        stats[UI_FILE] = ui_stats
        print(f"[UI 프리팹] 적용 {ui_stats['applied']} | 원문변경으로 제외 {ui_stats['stale_skipped']}"
              f" | 위치소실 {ui_stats['missing']}  (매니페스트 CRC·.hash 갱신)")

    ver = jload(os.path.join(ROOT, "VERSION.json"), {})
    patch_ver = ver.get("patch_version", "0.0.0")
    zip_name = f"ThePiperOfDawn_KoreanPatch_v{patch_ver}.zip"
    os.makedirs(DIST_DIR, exist_ok=True)
    zpath = os.path.join(DIST_DIR, zip_name)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:  # 암호화 데이터라 압축 무의미
        for rel, data in files.items():
            z.writestr(rel.replace(os.sep, "/"), data)
    ver.update({
        "game_manifest_version": g.version,
        "table_bundle": g.bundle_hash,
        "table_content_crc": f"{crc:08x}",
        "built": datetime.date.today().isoformat(),
        "stats": stats,
    })
    jsave(os.path.join(ROOT, "VERSION.json"), ver)
    print(f"배포 zip: {zpath}  ({len(files)}개 파일)")
    if a.install:
        for rel, data in files.items():
            with open(os.path.join(g.dir, rel), "wb") as f:
                f.write(data)
        print(f"게임에 설치함: {len(files)}개 파일  (원본 백업: work/backup/)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", help="게임 설치 폴더 (기본: Steam 기본 경로)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    m = sub.add_parser("merge")
    m.add_argument("files", nargs="+")
    b = sub.add_parser("build")
    b.add_argument("--install", action="store_true", help="빌드 결과를 게임 폴더에 바로 설치")
    b.add_argument("--allow-stale", action="store_true", help="원문이 바뀐 행에도 기존 번역 적용")
    a = ap.parse_args()
    {"check": cmd_check, "merge": cmd_merge, "build": cmd_build}[a.cmd](a)


if __name__ == "__main__":
    main()
