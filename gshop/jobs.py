"""后台任务（v2.5.0）：充值轮询、链上监听、经营日报、自动备份。

四个常驻协程由 post_init 统一拉起：
- auto_check_recharges：每 60 秒轮询支付平台待支付充值单，到账自动加分并通知
- auto_check_chain_recharges：每 45 秒轮询冷钱包链上入账（v2.5.0），匹配单号/尾数自动入账
- daily_report：每天 08:05（本地时区）给全体管理员推送昨日/累计经营日报
- auto_backup：每天 04:30（本地时区）备份数据库到 backups/，保留最近 7 份

所有任务循环都带异常兜底：单次失败只记日志，绝不让协程退出。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from .models import LOCAL_TZ, now_iso
from .runtime import BASE_DIR, VERSION, db, grab, service

logger = logging.getLogger(__name__)

# 备份目录与保留份数
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
BACKUP_KEEP = 7


# ==================================================================
# 充值轮询
# ==================================================================

async def auto_check_recharges() -> None:
    """每 60 秒轮询一次待支付充值单，到账后自动加积分并通知。"""
    while True:
        await asyncio.sleep(60)
        try:
            paid_list = await service.check_all_pending_recharges()
            for r in paid_list:
                await service.notify_user_recharge_success(r)
                await service.notify_admins_recharge_paid(r)
        except Exception as exc:
            logger.warning("自动轮询充值失败：%s", exc)


async def auto_check_chain_recharges() -> None:
    """每 45 秒轮询一次冷钱包链上入账（v2.5.0），匹配到待支付单自动加积分并通知。

    只在对应渠道开启且配置了收款地址时才会真正请求链上 API（见 check_chain_recharges），
    未开启时每轮空转一次内存判断，开销可忽略。
    """
    while True:
        await asyncio.sleep(45)
        try:
            paid_list = await service.check_chain_recharges()
            for r in paid_list:
                await service.notify_user_recharge_success(r)
                await service.notify_admins_recharge_paid(r)
        except Exception as exc:
            logger.warning("链上充值轮询失败：%s", exc)


# ==================================================================
# 抢单后台三件套（v2.6.0）：超时释放重派 / 超时前预警 / 佣金到期解冻
# ==================================================================

async def auto_grab_housekeeping() -> None:
    """每 60 秒扫一轮抢单模块的时效事务。

    三个子任务各自独立兜底，互不影响；抢单模块未开启（grab.enabled=False）时
    只做内存判断，开销可忽略。
    """
    while True:
        await asyncio.sleep(60)
        try:
            await grab.check_warns()
        except Exception as exc:
            logger.warning("抢单超时预警扫描失败：%s", exc)
        try:
            await grab.check_timeouts()
        except Exception as exc:
            logger.warning("抢单超时释放扫描失败：%s", exc)
        try:
            await grab.release_due_commissions()
        except Exception as exc:
            logger.warning("佣金到期解冻扫描失败：%s", exc)


# ==================================================================
# 经营日报
# ==================================================================

def _seconds_until(hour: int, minute: int) -> float:
    """距本地时区下一个 hour:minute 的秒数（今天已过点则算明天）。"""
    now = datetime.now(LOCAL_TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def build_daily_report() -> str:
    """组装经营日报文本（今日 + 累计 + 库存池 + 供需）。"""
    from .runtime import supply  # 延迟导入，保持 jobs 可独立单测

    s = service.get_finance_stats()
    try:
        sup = supply.stats()
    except Exception:
        sup = {}
    lines = [
        f"📊 <b>经营日报</b>（{datetime.now(LOCAL_TZ).strftime('%Y-%m-%d')}）",
        "",
        f"📦 今日订单：<code>{s['today_orders']}</code> 笔（待处理 {s['pending_orders']}）",
        f"💰 今日充值：<code>{s['today_recharge']:.2f}</code> USDT",
        f"👥 今日新增用户：<code>{s['today_users']}</code> 人",
        "",
        f"🗄 累计：用户 {s['total_users']} · 订单 {s['total_orders']} · 充值 {s['total_recharge']:.2f} USDT",
        f"🛍 在售商品：{s['total_products']} 个（商品 {s['product_count']} / 报单 {s['report_count']}）",
        f"📦 库存池：现货 {s.get('available', 0)} 件 · 已售 {s.get('sold', 0)} 件",
    ]
    if sup:
        lines.append(
            f"📢 供需：今日 +{sup.get('today_cnt', 0)} 条 · 累计 {sup.get('total_cnt', 0)} 条"
            f" · 累计收入 {sup.get('total_income', 0)} 积分"
        )
    lines += ["", f"🤖 版本 v{VERSION} · 运行正常"]
    return "\n".join(lines)


def _daily_task_hour() -> int:
    """日报/备份触发时刻（设置页可改，0~23 点，东八区）。"""
    try:
        hour = int(db.get_settings().get("daily_task_hour", 8))
        return max(0, min(23, hour))
    except Exception:
        return 8


async def daily_report() -> None:
    """每天设定时刻给全体管理员推送经营日报（可在设置页关闭）。"""
    while True:
        await asyncio.sleep(_seconds_until(_daily_task_hour(), 5))
        try:
            if not db.get_settings().get("daily_report_enabled", True):
                continue
            bot = service._bot
            if not bot:
                continue
            text = build_daily_report()
            for admin in db.list_admins():
                try:
                    await bot.send_message(chat_id=admin.user_id, text=text, parse_mode="HTML")
                    await asyncio.sleep(0.05)
                except Exception as exc:
                    logger.warning("日报发送失败 admin=%s: %s", admin.user_id, exc)
        except Exception as exc:
            logger.warning("经营日报任务失败：%s", exc)


# ==================================================================
# 自动备份
# ==================================================================

def do_backup() -> str:
    """立即备份数据库，返回备份文件路径（供定时任务与「a:backup」按钮共用）。

    v2.7.1 改了三处（审计 N-P2-9）：

    1) 用 SQLite 官方 backup API 代替 `shutil.copy2`。`copy2` 在 WAL 模式下
       即使先 checkpoint 也存在竞争窗口：复制到一半如果刚好有写入提交，
       拿到的库可能是「主库旧页 + WAL 新页」的混合体，打开就报
       `database disk image is malformed`。backup API 走页级一致快照，
       且允许源库继续读写，不需要停服。
    2) 文件名带毫秒。原来只到秒，同一秒内连点两次备份会互相覆盖，
       第二次写的可能是半成品。
    3) 备份完做一次 `PRAGMA integrity_check`，不通过就删掉这个文件并抛错——
       比留着一个坏备份等真出事时才发现要好。
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now(LOCAL_TZ).strftime("%Y%m%d_%H%M%S_%f")[:-3]
    dest = os.path.join(BACKUP_DIR, f"shop_{stamp}.db")
    db_path = str(db.path) if hasattr(db, "path") else os.path.join(BASE_DIR, "data", "shop.db")

    src = sqlite3.connect(db_path)
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()

    # 校验备份可用性：integrity_check 返回单行 'ok' 才算合格
    try:
        chk = sqlite3.connect(dest)
        try:
            result = chk.execute("PRAGMA integrity_check").fetchone()
        finally:
            chk.close()
    except sqlite3.Error as exc:
        _remove_quietly(dest)
        raise RuntimeError(f"备份文件无法打开：{exc}") from exc
    if not result or result[0] != "ok":
        _remove_quietly(dest)
        raise RuntimeError(f"备份文件校验失败：{result[0] if result else 'no result'}")

    # 只保留最近 BACKUP_KEEP 份
    try:
        files = sorted(
            (f for f in os.listdir(BACKUP_DIR) if f.startswith("shop_") and f.endswith(".db")),
            reverse=True,
        )
        for old in files[BACKUP_KEEP:]:
            _remove_quietly(os.path.join(BACKUP_DIR, old))
    except Exception as exc:
        logger.warning("清理旧备份失败：%s", exc)
    return dest


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


async def auto_backup() -> None:
    """每天 04:30 自动备份数据库（可在系统设置里关闭）。

    原实现没有读 `daily_backup_enabled`，管理员在面板上关掉自动备份后
    它照样每天跑——设置项形同虚设。
    """
    while True:
        await asyncio.sleep(_seconds_until(4, 30))
        try:
            if not db.get_settings().get("daily_backup_enabled", True):
                continue
            dest = do_backup()
            logger.info("自动备份完成：%s", dest)
        except Exception as exc:
            logger.warning("自动备份失败：%s", exc)

async def auto_heartbeat(health_file: str) -> None:
    """P2-12: refresh the health file every 30s for the Docker HEALTHCHECK.

    A live event loop is the signal: if polling or the process wedges,
    the file stops being rewritten and the container gets restarted.
    """
    from .singleton import write_health

    while True:
        try:
            write_health(health_file, now_iso())
        except Exception as exc:
            logger.warning("心跳写入失败：%s", exc)
        await asyncio.sleep(30)
