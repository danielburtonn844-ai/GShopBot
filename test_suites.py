"""pytest 统一入口（审计 P2-16：测试框架迁到 pytest）。

背景：仓库里的 test_*.py / verify_issues.py 都是「自执行断言脚本」——
顶层直接 print 结果 + sys.exit(0/1)，不是 pytest 风格的测试模块。
直接用 pytest 去收集它们，会在 import 阶段撞上顶层 sys.exit()，触发
INTERNALERROR（SystemExit 被当成收集期异常）。

所以这里用 parametrize 把它们当子进程逐个跑：退出码非 0 即判失败，
并把最后 20 行输出贴进断言消息。pyproject.toml 的
`[tool.pytest.ini_options] python_files` 只留本文件，pytest 就不会再去
import 那些脚本。

单独跑某一套：
    pytest test_suites.py -k test_cov_deep3
全部跑：
    pytest test_suites.py
"""

import os
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.abspath(__file__))

# 顺序即执行顺序：从最快的单元断言到最慢的深度覆盖套件。
SUITES = [
    "test_bot_full.py",
    "test_extra.py",
    "test_p2_fixes.py",
    "test_v281_fixes.py",
    "test_p2_9_money.py",
    "test_version_consistency.py",
    "test_coverage_boost.py",
    "verify_issues.py",
    "test_cov_deep.py",
    "test_cov_deep2.py",
    "test_cov_deep3.py",
]


@pytest.mark.parametrize("script", SUITES)
def test_suite(script):
    path = os.path.join(BASE, script)
    if not os.path.exists(path):
        pytest.skip(f"{script} 不存在")
    proc = subprocess.run(
        [sys.executable, path],
        cwd=BASE,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    tail = "\n".join((proc.stdout or "").splitlines()[-20:])
    err = "\n".join((proc.stderr or "").splitlines()[-10:])
    assert proc.returncode == 0, f"{script} 退出码 {proc.returncode}\n--- stdout 尾部 ---\n{tail}\n--- stderr 尾部 ---\n{err}"
