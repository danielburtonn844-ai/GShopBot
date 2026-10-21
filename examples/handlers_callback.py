"""回调分发入口（v2.6.4）：所有按钮点击的唯一入口。

原本是 1919 行、152 个 `if data.startswith(...)` 的巨型函数，
现按回调命名空间拆到 4 个 dispatcher 模块：

| 模块 | 命名空间 |
|---|---|
| cb_user.py | `m:` `d:` `buy:` `cbuy:` `od:` `urge:` `redeliver:` `rsub:` `rc:` `rcpay:` `rcchk:` |
| cb_supply.py | `sup:` `a:sup*`（供需大厅/发布向导/开关/审核） |
| cb_admin.py | `padd_*` `a:*`（上架向导/商品/订单/用户/设置） |
| cb_grab.py | `gb:` `a:g*`（抢单/接单员/提现/申诉） |

分发采用「顺序短路」：第一个返回 True 的 dispatcher 即为命中，后续不再执行。
顺序固定为 user → supply → admin → grab，因为 `a:sup*` 必须早于 `a:*` 匹配，
否则会被管理端通配吃掉。

新增按钮时：先判断属于哪个命名空间，只在对应模块里加一个 `if`，其余模块不受影响。
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from .cb_admin import _dispatch_admin
from .cb_grab import _dispatch_grab
from .cb_supply import _dispatch_supply
from .cb_user import _dispatch_user
from .runtime import db, service
from .ui import clear_state

logger = logging.getLogger(__name__)

# 处于输入状态时，这些前缀属于「流程内按钮」，必须保留 state 与 payload
_STATE_KEEP_PREFIXES = ("padd_kind:", "padd_mode:", "a:bcsend", "sup:", "a:stkdone:")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """按钮回调总入口：建档 → 清状态 → 按命名空间分发 → 未命中兜底回主菜单。"""
    query = update.callback_query
    await query.answer()
    tg_user = update.effective_user
    uid = tg_user.id
    data = query.data or ""

    service.get_or_create_user(
        uid,
        username=tg_user.username or "",
        first_name=tg_user.first_name or "",
        last_name=tg_user.last_name or "",
    )

    # 输入状态清理：点任何按钮都视为放弃当前输入，防止用户点「取消」后
    # 随手发的消息仍被当成提交内容处理。流程内按钮除外。
    if context.user_data.get("state") and not data.startswith(_STATE_KEEP_PREFIXES):
        clear_state(context)

    # 管理端权限闸门（fail-closed）：拆分成多 dispatcher 后，若权限校验散落在
    # 各模块里，新增一个 dispatcher 就可能漏检——cb_supply 的 `a:sup*` 分支
    # 曾因此在入口顺序调整后绕过校验。集中在此，任何 `a:` 前缀一律先过闸门。
    if data.startswith("a:") and not db.is_admin(uid):
        from .ui import kb
        from .ui import send_or_edit as _send

        await _send(query, "❌ 无权限", kb([[("🏠 主菜单", "m:root")]]))
        return

    for dispatch in (_dispatch_user, _dispatch_supply, _dispatch_admin, _dispatch_grab):
        if await dispatch(query, context, uid, data, tg_user):
            return

    # 未知回调兜底：回主菜单，不静默失败
    logger.warning("未处理的回调: %s", data)
    from .pages_user import page_root
    from .ui import send_or_edit

    text, keyboard = page_root(uid)
    await send_or_edit(query, text, keyboard)
