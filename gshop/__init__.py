"""G Shop Bot 业务包（审计 P2-2：从 `examples/` 更名为 `gshop/`）。

`examples/` 这个名字是「示例代码」的约定，把一个完整生产机器人塞进去，
装包时容易被当成示例排除，读代码的人也要先绕一层才意识到它是主程序。
v2.8.0 起正式改名 `gshop/`，全部内部导入同步更新，旧包已删除。

模块地图：
    version          版本号唯一来源（读仓库根 VERSION）
    runtime          运行时单例：db / service / supply / stock / grab
    models           数据层门面（显式 re-export，无 import *）
    db/              数据层实现：base 常量与工具、schema dataclass、12 个 mixin
    ui               键盘构造、HTML 转义、安全发送、输入状态
    richtext         图标与富文本片段
    pages_*          各端页面渲染
    handlers_*       update 入口（command / callback / message）
    cb_*             按钮回调分域处理
    *_service        业务层（shop / grab / supply / stock）
    jobs             后台任务
    tasks            任务持留与优雅退出
    okpay / kkpay    支付网关
    chain_watch      链上监听
"""

from __future__ import annotations

from .version import VERSION, __version__

__all__ = ["VERSION", "__version__"]
