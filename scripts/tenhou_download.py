"""
天凤 (Tenhou) 凤桌牌谱下载器
================================

数据流:
    list.cgi  →  scc*.html.gz (凤桌牌谱索引, 按小时)
              →  提取 log= id
    0/log/?id →  XML 牌谱 (gzip)

重要政策 (来源: houou-logs README + 天凤作者 tsuno 推文):
    - 天凤禁止再分发牌谱数据 (download 的 XML 不得公开/镜像)
    - 只允许【单线程/单会话】下载, 多线程会被封
    - 建议请求间隔 >= 1 秒

参考实现: https://github.com/Apricot-S/houou-logs (当前唯一维护方案)
         https://github.com/MahjongRepository/phoenix-logs (已归档, 机制相同)

用法:
    python scripts/tenhou_download.py download-index --days 7
    python scripts/tenhou_download.py extract-ids --filter "四鳳南"
    python scripts/tenhou_download.py download-xml --limit 50
    python scripts/tenhou_download.py stats
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ------------------------------------------------------------ #
# 路径与常量
# ------------------------------------------------------------ #
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "tenhou"
SCC_DIR = DATA_DIR / "scc_cache"      # 原始 scc*.html.gz 缓存
XML_DIR = DATA_DIR / "xml"            # 解压后的 XML 牌谱
IDS_FILE = DATA_DIR / "log_ids.json"  # 提取出的 log id 列表
STATE_FILE = DATA_DIR / "download_state.json"  # XML 下载进度

LIST_CGI_URL = "https://tenhou.net/sc/raw/list.cgi"
SCC_DAT_URL = "https://tenhou.net/sc/raw/dat/{name}"
LOG_URL = "https://tenhou.net/0/log/?{log_id}"

# scc 文件条目正则: 时间 | 序号 | 规则串 | <a href="...log=ID">牌譜</a> | 玩家成绩
SCC_LINE_RE = re.compile(
    r"(\d{2}:\d{2})\s*\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*"
    r'<a\s+href="[^"]*log=([^"]+)"',
)
LOG_ID_RE = re.compile(r"^\d{10}gm-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{8}$")
FILE_INDEX_RE = re.compile(r"file\s*:\s*'([^']+)'\s*,\s*size\s*:\s*(\d+)")

# 规则代码位掩码 (来自 houou-logs log_id.py)
TYPE_HANCHAN = 0x008      # 半庄
TYPE_3_PLAYERS = 0x010    # 三麻

# 下载限速 (遵守天风单线程政策)
REQUEST_INTERVAL_SEC = 1.0
HTTP_TIMEOUT = 30


# ------------------------------------------------------------ #
# HTTP 工具
# ------------------------------------------------------------ #
def _http_get(url: str, *, binary: bool = False, timeout: int = HTTP_TIMEOUT):
    """带 UA 的简单 GET。返回 text 或 bytes。"""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "mjagent-tenhou-downloader/1.0 (research)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return data if binary else data.decode("utf-8", errors="replace")


def _ensure_dirs():
    for d in (DATA_DIR, SCC_DIR, XML_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------ #
# Step 1: 下载 scc 索引
# ------------------------------------------------------------ #
def list_scc_files() -> list[str]:
    """从 list.cgi 拉取所有 scc 文件名 (凤桌牌谱索引)。"""
    text = _http_get(LIST_CGI_URL)
    files = [name for name, _size in FILE_INDEX_RE.findall(text)
             if name.startswith("scc")]
    return files


def download_index(days: int, *, force: bool = False) -> int:
    """
    下载最近 N 天的 scc 凤桌索引文件。
    scc 文件按 [日期][小时] 命名: sccYYYYMMDDHH.html.gz
    """
    _ensure_dirs()
    all_files = list_scc_files()
    if not all_files:
        print("[ERROR] list.cgi 未返回任何 scc 文件", file=sys.stderr)
        return 0

    # 解析日期, 过滤最近 N 天
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    target_files = []
    for name in all_files:
        # sccYYYYMMDDHH.html.gz -> 取 YYYYMMDDHH
        m = re.search(r"scc(\d{10})\.html\.gz", name)
        if not m:
            continue
        try:
            ftime = datetime.strptime(m.group(1), "%Y%m%d%H").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if ftime >= cutoff:
            target_files.append((name, ftime))

    target_files.sort(key=lambda x: x[1])
    print(f"[index] 最近 {days} 天共有 {len(target_files)} 个 scc 文件待下载")

    downloaded = 0
    skipped = 0
    for name, ftime in target_files:
        out = SCC_DIR / name
        if out.exists() and not force:
            skipped += 1
            continue
        url = SCC_DAT_URL.format(name=name)
        try:
            data = _http_get(url, binary=True)
            # 校验是 gzip
            if not data[:2] == b"\x1f\x8b":
                print(f"  [warn] {name} 不是 gzip, 跳过 (可能 404)")
                continue
            out.write_bytes(data)
            downloaded += 1
            if downloaded % 20 == 0:
                print(f"  已下载 {downloaded}/{len(target_files)}...")
            # list.cgi 是公开索引, 间隔可短; 仍保留小延迟
            time.sleep(0.2)
        except Exception as e:
            print(f"  [error] {name}: {e}", file=sys.stderr)

    print(f"[index] 完成: 新下载 {downloaded}, 已存在跳过 {skipped}")
    return downloaded


# ------------------------------------------------------------ #
# Step 2: 提取 log id
# ------------------------------------------------------------ #
def _parse_log_type(hex_code: str) -> tuple[int, bool]:
    """解析规则代码 -> (玩家数, 是否东场)。"""
    t = int(hex_code, 16)
    is_3p = (t & TYPE_3_PLAYERS) == TYPE_3_PLAYERS
    is_hanchan = (t & TYPE_HANCHAN) == TYPE_HANCHAN
    return (3 if is_3p else 4, not is_hanchan)


def extract_ids(filter_str: str = "四鳳南") -> int:
    """
    扫描所有已下载的 scc 文件, 提取符合规则的 log id。
    filter_str: 规则串子串过滤, 默认 "四鳳南" (四人凤桌南喰赤半庄)。
    """
    _ensure_dirs()
    entries = []  # [{log_id, time, rule, file}]
    seen = set()

    scc_files = sorted(SCC_DIR.glob("scc*.html.gz"))
    if not scc_files:
        print("[ERROR] scc_cache 为空, 请先运行 download-index", file=sys.stderr)
        return 1

    for fp in scc_files:
        try:
            with gzip.open(fp, mode="rt", encoding="utf-8") as gz:
                text = gz.read()
        except Exception as e:
            print(f"  [warn] 解压 {fp.name} 失败: {e}", file=sys.stderr)
            continue

        for time_str, rule, log_id in SCC_LINE_RE.findall(text):
            if filter_str and filter_str not in rule:
                continue
            if not LOG_ID_RE.fullmatch(log_id):
                continue
            if log_id in seen:
                continue
            seen.add(log_id)
            try:
                num_players, is_tonpu = _parse_log_type(log_id[13:17])
            except (ValueError, IndexError):
                continue
            entries.append({
                "log_id": log_id,
                "time": time_str,
                "rule": rule.strip(),
                "num_players": num_players,
                "is_tonpu": is_tonpu,
                "source_file": fp.name,
            })

    # 按时间排序
    entries.sort(key=lambda e: e["log_id"])  # log_id 以 YYYYMMDDHH 开头, 天然时间序

    IDS_FILE.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[ids] 提取 {len(entries)} 个 log id (过滤='{filter_str}')")
    print(f"[ids] 保存到 {IDS_FILE.relative_to(ROOT)}")

    # 简单统计
    if entries:
        times = [e["time"] for e in entries]
        files = set(e["source_file"] for e in entries)
        print(f"[ids] 覆盖 {len(files)} 个 scc 文件, 时间范围 "
              f"{entries[0]['log_id'][:8]} ~ {entries[-1]['log_id'][:8]}")
    return len(entries)


# ------------------------------------------------------------ #
# Step 3: 下载 XML 牌谱
# ------------------------------------------------------------ #
def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"downloaded": [], "failed": [], "last_idx": 0}


def _save_state(state: dict):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def download_xml(limit: int | None, *, sleep: float = REQUEST_INTERVAL_SEC) -> int:
    """
    根据 log_ids.json 下载 XML 牌谱。
    单线程 + 限速, 遵守天风政策。
    """
    _ensure_dirs()
    if not IDS_FILE.exists():
        print("[ERROR] log_ids.json 不存在, 请先运行 extract-ids", file=sys.stderr)
        return 1

    entries = json.loads(IDS_FILE.read_text(encoding="utf-8"))
    state = _load_state()
    done = set(state["downloaded"])
    failed = set(state["failed"])

    pending = [e for e in entries if e["log_id"] not in done]
    if limit:
        pending = pending[:limit]

    print(f"[xml] 待下载 {len(pending)} 个牌谱 (已完成 {len(done)}, "
          f"失败 {len(failed)})")
    if not pending:
        return 0

    success = 0
    for i, entry in enumerate(pending, 1):
        log_id = entry["log_id"]
        out = XML_DIR / f"{log_id}.xml"
        if out.exists():
            done.add(log_id)
            success += 1
            continue

        url = LOG_URL.format(log_id=log_id)
        try:
            data = _http_get(url, binary=True)
            # 天风 XML 接口返回 gzip
            if data[:2] == b"\x1f\x8b":
                try:
                    data = gzip.decompress(data)
                except Exception:
                    pass  # 某些情况可能已解压
            # 基本校验: 应以 <?xml 或 <mujunou 开头
            if not (data[:5] == b"<?xml" or data[:1] == b"<"):
                raise RuntimeError(f"返回非 XML: {data[:60]!r}")
            out.write_bytes(data)
            done.add(log_id)
            success += 1
            if i % 10 == 0:
                print(f"  进度 {i}/{len(pending)} (成功 {success})")
                _save_state({**state, "downloaded": list(done),
                            "failed": list(failed)})
        except urllib.error.HTTPError as e:
            failed.add(log_id)
            print(f"  [fail] {log_id}: HTTP {e.code}", file=sys.stderr)
        except Exception as e:
            failed.add(log_id)
            print(f"  [fail] {log_id}: {e}", file=sys.stderr)

        # 限速 (遵守天风单线程政策)
        time.sleep(sleep)

    _save_state({**state, "downloaded": list(done), "failed": list(failed)})
    print(f"[xml] 完成: 本次成功 {success}/{len(pending)}, "
          f"累计成功 {len(done)}, 失败 {len(failed)}")
    return success


# ------------------------------------------------------------ #
# Step 4: 统计
# ------------------------------------------------------------ #
def stats():
    _ensure_dirs()
    scc_count = len(list(SCC_DIR.glob("scc*.html.gz")))
    xml_count = len(list(XML_DIR.glob("*.xml")))
    ids_count = 0
    if IDS_FILE.exists():
        ids_count = len(json.loads(IDS_FILE.read_text(encoding="utf-8")))
    state = _load_state()

    print("=== 天凤牌谱数据统计 ===")
    print(f"  scc 索引文件 : {scc_count} 个  ({SCC_DIR.relative_to(ROOT)})")
    print(f"  log id 总数  : {ids_count}    ({IDS_FILE.relative_to(ROOT)})")
    print(f"  XML 牌谱     : {xml_count} 个 ({XML_DIR.relative_to(ROOT)})")
    print(f"  已记录下载   : {len(state.get('downloaded', []))}")
    print(f"  已记录失败   : {len(state.get('failed', []))}")
    if xml_count:
        total_kb = sum(f.stat().st_size for f in XML_DIR.glob("*.xml")) / 1024
        print(f"  XML 总大小   : {total_kb:.1f} KB (avg {total_kb/xml_count:.1f} KB/局)")


# ------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------ #
def main():
    parser = argparse.ArgumentParser(
        description="天凤凤桌牌谱下载器 (单线程, 遵守天风使用政策)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("download-index", help="下载最近 N 天的 scc 凤桌索引")
    p1.add_argument("--days", type=int, default=7)
    p1.add_argument("--force", action="store_true", help="强制重新下载已存在文件")

    p2 = sub.add_parser("extract-ids", help="从 scc 提取 log id")
    p2.add_argument("--filter", default="四鳳南",
                    help="规则串子串过滤 (默认 '四鳳南')")

    p3 = sub.add_parser("download-xml", help="下载 XML 牌谱")
    p3.add_argument("--limit", type=int, default=None, help="最多下载 N 个")
    p3.add_argument("--sleep", type=float, default=REQUEST_INTERVAL_SEC,
                    help=f"请求间隔秒数 (默认 {REQUEST_INTERVAL_SEC}, 遵守天风政策)")

    sub.add_parser("stats", help="显示数据统计")

    args = parser.parse_args()

    if args.cmd == "download-index":
        download_index(args.days, force=args.force)
    elif args.cmd == "extract-ids":
        extract_ids(args.filter)
    elif args.cmd == "download-xml":
        download_xml(args.limit, sleep=args.sleep)
    elif args.cmd == "stats":
        stats()


if __name__ == "__main__":
    main()
