#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取「全收藏品 ID」表（6 类）并合并成 mai_edit 能直接吃的 sheet 格式。

为什么需要：`data/collections_2025-02-28.json` 是 2025-02-28 从一份 xlsx 转出来的，
之后没再更新；新版本（A011 之后）的姓名框 / 称号 / 头像 / 旅行伙伴 / 搭档 / 背景
全都不在里面。游戏的静态表（Item / NamePlate / Trophy / Icon / Map / Character / Partner）
只在客户端里，服务端没有任何「列出全部藏品」的接口 ——
`GetUserItemApi` 只返回**该账号已拥有**的条目，所以必须从客户端资源或社区资源仓库拿全量。

上游：https://github.com/Flora-Software/maimai-resourse（国服 MaiMaiDX JSON 资源，含中文名）

用法：
    ./.venv/bin/python tools/fetch_collections.py                  # 合并 → data/collections_full.json
    ./.venv/bin/python tools/fetch_collections.py --no-merge       # 只用上游，不并旧表
    ./.venv/bin/python tools/fetch_collections.py --base A.json --out B.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

RAW = "https://raw.githubusercontent.com/Flora-Software/maimai-resourse/main/{f}.json"

# 上游文件 → (itemKind, 中文类型名, 上游里的 list 键)
SOURCES = {
    "plate":   (1,  "姓名框",   "plate"),
    "title":   (2,  "称号",     "titles"),
    "icon":    (3,  "头像",     "icon"),
    "chara":   (9,  "旅行伙伴", "chara"),
    "partner": (10, "搭档",     "partner"),
    "frame":   (11, "背景",     "frame"),
}

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASE = os.path.join(HERE, "data", "collections_2025-02-28.json")
DEFAULT_OUT = os.path.join(HERE, "data", "collections_full.json")
KEEP_KINDS = ("音乐", "区域", "签到章", "友人对战")


def fetch(fname: str, timeout: float = 30.0) -> dict:
    url = RAW.format(f=fname)
    req = urllib.request.Request(url, headers={"User-Agent": "mai-collections-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as rsp:
        return json.loads(rsp.read().decode("utf-8"))


def parse_upstream(fname: str, payload: dict) -> dict[int, str]:
    """上游 JSON → {itemId: name}（id 是零填充字符串，转 int；占位项保留，调用方自己过滤）"""
    list_key = SOURCES[fname][2]
    rows = payload.get(list_key)
    if not isinstance(rows, list):
        # 兼容上游改名：退化成「取第一个 list 值」
        rows = next((v for v in payload.values() if isinstance(v, list)), [])
    out: dict[int, str] = {}
    for x in rows:
        try:
            out[int(x["id"])] = str(x.get("name") or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
    return out


def sheet_key(kind: int) -> str:
    return f"{SOURCES_BY_KIND[kind][1]} ({kind})"


SOURCES_BY_KIND = {v[0]: (k, v[1], v[2]) for k, v in SOURCES.items()}


def load_base(path: str | None) -> dict:
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def merge(base: dict, upstream: dict[int, dict[int, str]]) -> tuple[dict, list[str]]:
    """旧表 + 上游，按 kind 取并集；同名 ID 用上游名字覆盖。返回 (新表, 报告行)"""
    out = dict(base)
    report: list[str] = []
    for kind, rows in sorted(upstream.items()):
        key = sheet_key(kind)
        old = {int(i): str(n) for i, n in (base.get(key) or [])}
        new_ids = sorted(set(rows) - set(old))
        merged = dict(old)
        merged.update(rows)
        out[key] = [[i, merged[i]] for i in sorted(merged)]
        report.append(
            f"kind={kind:<2} {SOURCES_BY_KIND[kind][1]:6} 上游 {len(rows):5} 件 / 旧表 {len(old):5} 件"
            f" → 合并 {len(merged):5} 件（新增 {len(new_ids):4}，旧表独有 {len(set(old) - set(rows)):3}）"
        )
    for key in KEEP_KINDS:            # 曲库/区域/签到章/友人对战 原样保留
        for k in base:
            if k.startswith(key) and k not in out:
                out[k] = base[k]
    return out, report


def main() -> int:
    ap = argparse.ArgumentParser(description="拉取 6 类全收藏品 ID 并合并成 sheet 格式")
    ap.add_argument("--base", default=DEFAULT_BASE, help="旧表（默认 data/collections_2025-02-28.json）")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出路径（默认 data/collections_full.json）")
    ap.add_argument("--no-merge", action="store_true", help="只用上游，不并旧表")
    ap.add_argument("--timeout", type=float, default=30.0)
    a = ap.parse_args()

    up: dict[int, dict[int, str]] = {}
    for fname, (kind, cn, _) in SOURCES.items():
        try:
            up[kind] = parse_upstream(fname, fetch(fname, a.timeout))
        except Exception as exc:                                    # noqa: BLE001
            print(f"❌ 拉取 {fname}.json 失败：{exc}", file=sys.stderr)
            return 1
        print(f"   上游 {fname}.json → kind={kind} {cn}：{len(up[kind])} 条")

    base = {} if a.no_merge else load_base(a.base)
    table, report = merge(base, up)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False)
    print("\n【合并结果】")
    for line in report:
        print("   " + line)
    total = sum(len(v) for k, v in table.items() if any(k.startswith(cn) for _, cn, _ in SOURCES.values()))
    print(f"\n已写入 {a.out}（6 类合计 {total} 条，全部键：{list(table)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
