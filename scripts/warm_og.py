#!/usr/bin/env python3
"""发布 Release 后预热 GitHub 的 Open Graph 预览图。

背景
----
Telegram / 微信 / Discord 分享链接时，会读页面里的 `og:image`。
GitHub 的 Release 页面用的是 `opengraph.githubassets.com/<hash>/...` 这张
**懒生成**的卡片图：第一次有人请求时才渲染，通常要几十秒。

如果分享发生在图片生成完成之前，抓取方会拿到 404/超时，然后
**把「这个链接没图」缓存住**——之后再怎么分享都不显示缩略图。
v2.6.6 的 Release 就是这么丢图的（而 v2.6.4 因为分享得晚，图已经生成好了）。

做法
----
发完 Release 立刻调用本脚本：它会先把 og:image 拉一次触发渲染，
再轮询直到返回 200 且体积正常，这样调用方拿到的链接一定带图。

用法
----
    python3 scripts/warm_og.py                       # 用仓库内 pyproject 的默认值
    python3 scripts/warm_og.py --tag v2.6.7
    python3 scripts/warm_og.py --repo owner/name --tag v2.6.7 --timeout 300

退出码 0 表示预览图已就绪，非 0 表示超时（此时先别分享，等一会儿再跑）。
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; GShopBot-release-check/1.0)"
MIN_BYTES = 20_000  # 正常卡片图 100KB 上下，小于 20KB 说明是错误页


def fetch(url: str, timeout: float = 20.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception:
        return 0, b""


def og_image_url(repo: str, tag: str) -> str:
    """从 Release 页面取出 og:image 的绝对地址。"""
    page = f"https://github.com/{repo}/releases/tag/{tag}"
    status, body = fetch(page)
    if status != 200:
        return ""
    html = body.decode("utf-8", "ignore")
    m = re.search(r'property="og:image"\s+content="([^"]+)"', html)
    return m.group(1) if m else ""


def warm(repo: str, tag: str, timeout: float, interval: float) -> bool:
    page = f"https://github.com/{repo}/releases/tag/{tag}"
    print(f"[1/2] 读取 Release 页面：{page}")
    img = ""
    for _ in range(6):
        img = og_image_url(repo, tag)
        if img:
            break
        time.sleep(3)
    if not img:
        print("      ✗ 页面上没有 og:image，GitHub 可能还没索引这个 tag")
        return False
    print(f"      og:image = {img}")

    print(f"[2/2] 预热预览图（最多等 {int(timeout)} 秒）")
    started = time.time()
    attempt = 0
    while time.time() - started < timeout:
        attempt += 1
        status, data = fetch(img)
        size = len(data)
        elapsed = int(time.time() - started)
        if status == 200 and size >= MIN_BYTES:
            print(f"      ✓ 第 {attempt} 次就绪：HTTP {status}，{size} 字节（{elapsed}s）")
            return True
        print(f"      … 第 {attempt} 次：HTTP {status}，{size} 字节，{elapsed}s")
        time.sleep(interval)
    print("      ✗ 超时，预览图还没生成好 —— 先别分享，过几分钟重跑本脚本")
    return False


def main() -> int:
    p = argparse.ArgumentParser(description="预热 GitHub Release 的 OG 预览图")
    p.add_argument("--repo", default="danielburtonn844-ai/GShopBot")
    p.add_argument("--tag", required=True, help="例如 v2.6.7")
    p.add_argument("--timeout", type=float, default=180)
    p.add_argument("--interval", type=float, default=5)
    args = p.parse_args()

    ok = warm(args.repo, args.tag, args.timeout, args.interval)
    print()
    if ok:
        print(f"✅ 可以分享了：https://github.com/{args.repo}/releases/tag/{args.tag}")
    else:
        print("⚠️  预览图未就绪。分享出去会被 Telegram 缓存成「无图」，")
        print("   等几分钟重跑，或者分享后把链接发给 @WebpageBot 强制刷新。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
