"""P2-1 回归：版本号单一源一致性。

v2.7.1 之前版本号散在四处且已经互相不一致（pyproject=2.6.1 /
runtime=2.6.7 / main.py 注释=2.6.0 / README=2.6.7）。发版时改漏一处，
线上报障就会先被版本号带偏。

现在的契约：仓库根 VERSION 文件是唯一来源，其它地方要么读它，
要么被本测试强制对齐。任何一处写硬编码版本号，这里就红。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OK = 0
BAD = 0

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def check(name: str, cond: bool, detail: str = "") -> None:
    global OK, BAD
    if cond:
        OK += 1
        print(f"  \u2705 {name}")
    else:
        BAD += 1
        print(f"  \u274c {name} {detail}")


def main() -> int:
    raw = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    print(f"VERSION 文件内容: {raw}")

    check("VERSION 文件是 x.y.z 形式", bool(SEMVER.match(raw)), raw)

    sys.path.insert(0, str(ROOT))
    from gshop import version as vmod

    check("gshop.version.VERSION 与文件一致", vmod.VERSION == raw, vmod.VERSION)
    check("gshop.version.__version__ 同步", vmod.__version__ == raw, vmod.__version__)
    check(
        "gshop.version.__all__ 恰好导出两个名字",
        sorted(vmod.__all__) == ["VERSION", "__version__"],
        str(vmod.__all__),
    )

    import gshop

    check("gshop.VERSION 与文件一致", gshop.VERSION == raw, gshop.VERSION)

    from gshop import runtime

    check("gshop.runtime.VERSION 与文件一致", runtime.VERSION == raw, runtime.VERSION)
    rt_src = (ROOT / "gshop" / "runtime.py").read_text(encoding="utf-8")
    check(
        "runtime.py 不再硬编码 VERSION 字面量",
        "\nVERSION = " not in rt_src,
        "runtime.py 里还有 VERSION = ...",
    )
    check("runtime.py 从 .version 导入 VERSION", "from .version import VERSION" in rt_src)

    py_full = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    py = py_full.split("[tool.", 1)[0]  # 只看 [project] 段，dynamic 里本来就有 version
    project_block = py.split("[project]", 1)[1].split("\n[", 1)[0]
    check(
        "pyproject.toml 的 [project] 段不再写静态 version",
        not re.search(r"(?m)^version\s*=", project_block),
        "[project] 里还有 version = ...",
    )
    check("pyproject.toml 声明 dynamic", 'dynamic = ["version"]' in py)
    # 注意：version 的真实来源写在 [tool.setuptools.dynamic] 段里，而 py 变量
    # 上面已经 split("[tool.", 1)[0] 把它切掉了（那是为了排除 dynamic 里的
    # "version" 字样、避免误判 [project] 段有静态 version）。这里必须查全文。
    check(
        "pyproject.toml 的 version 来源是 VERSION 文件",
        'version = {file = ["VERSION"]}' in py_full,
    )

    first = (ROOT / "main.py").read_text(encoding="utf-8").splitlines()[0]
    check(
        "main.py 首行注释没有硬编码版本号",
        SEMVER.pattern not in first and not re.search(r"v\d+\.\d+\.\d+", first),
        first,
    )

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    rm = re.search(r"版本 `v(\d+\.\d+\.\d+)`", readme)
    check(
        "README 版本号与 VERSION 一致",
        bool(rm) and rm.group(1) == raw,
        rm.group(1) if rm else "README 没找到 版本 vX.Y.Z",
    )

    chg = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    cm = re.search(r"(?m)^## v(\d+\.\d+\.\d+)", chg)
    check(
        "CHANGELOG 首条版本与 VERSION 一致",
        bool(cm) and cm.group(1) == raw,
        cm.group(1) if cm else "CHANGELOG 没找到 ## vX.Y.Z",
    )

    offenders = []
    for path in list(ROOT.glob("*.py")) + list((ROOT / "gshop").rglob("*.py")):
        if path.name == "test_version_consistency.py":
            continue
        for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if raw in line and "VERSION" in line.upper():
                offenders.append(f"{path.relative_to(ROOT)}:{ln}")
    check("全仓没有第二处写死的当前版本号", not offenders, ", ".join(offenders))

    print("=" * 50)
    print(f"\u2705 通过 {OK} 项   \u274c 失败 {BAD} 项")
    print("=" * 50)
    return 1 if BAD else 0


if __name__ == "__main__":
    raise SystemExit(main())
