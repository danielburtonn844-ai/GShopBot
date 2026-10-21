"""设置项（setting 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SettingMixin:
    """设置项。"""

    def get_setting(self, key: str, default: Any = None) -> Any:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row:
                return json.loads(row[0])
            return default
        finally:
            conn.close()

    def set_setting(self, key: str, value: Any) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()

    def get_settings(self) -> dict[str, Any]:
        return {
            "welcome_text": self.get_setting("welcome_text", "👋 欢迎光临"),
            # 欢迎语富文本版（text_html 生成，含加粗/链接/高级会员表情）；
            # 空串表示老数据，展示时自动回退到转义 welcome_text
            "welcome_text_html": self.get_setting("welcome_text_html", ""),
            "welcome_image": self.get_setting("welcome_image", ""),
            "points_rate": self.get_setting("points_rate", 10),
            "min_recharge_usdt": self.get_setting("min_recharge_usdt", 1),
            # ---------- 支付设置（v2.5.0）：渠道开关 / 冷钱包地址与Key / 单笔上限 ----------
            "max_recharge_usdt": self.get_setting("max_recharge_usdt", 1000),
            "pay_okpay_enabled": self.get_setting("pay_okpay_enabled", True),
            "pay_kkpay_enabled": self.get_setting("pay_kkpay_enabled", True),
            "pay_tron_enabled": self.get_setting("pay_tron_enabled", False),
            "pay_ton_enabled": self.get_setting("pay_ton_enabled", False),
            "tron_wallet_address": self.get_setting("tron_wallet_address", ""),
            "ton_wallet_address": self.get_setting("ton_wallet_address", ""),
            "trongrid_api_key": self.get_setting("trongrid_api_key", ""),
            "toncenter_api_key": self.get_setting("toncenter_api_key", ""),
            "maintenance_mode": self.get_setting("maintenance_mode", False),
            "order_expire_minutes": self.get_setting("order_expire_minutes", 30),
            "force_channels": self.get_setting("force_channels", False),
            # ---------- 付费供需 ----------
            "supply_price_bot": self.get_setting("supply_price_bot", 10),
            "supply_price_channel": self.get_setting("supply_price_channel", 10),
            "supply_discount": self.get_setting("supply_discount", 10),
            "supply_daily_limit": self.get_setting("supply_daily_limit", 5),
            "supply_max_len": self.get_setting("supply_max_len", 600),
            "supply_channel_id": self.get_setting("supply_channel_id", ""),
            "supply_categories": self.get_setting("supply_categories", []),
            # ---------- 供需开关（v2.3.0，管理端「开关面板」逐项切换） ----------
            "supply_paused": self.get_setting("supply_paused", False),        # 停止发布（大厅仍可逛）
            "supply_review": self.get_setting("supply_review", False),        # 人工审核：先扣费待审，通过后发布
            "supply_hall_open": self.get_setting("supply_hall_open", True),   # 大厅是否开放浏览
            "supply_anonymous": self.get_setting("supply_anonymous", True),   # 是否允许匿名发布
            # ---------- 商品分类（v2.4.0） ----------
            "product_categories": self.get_setting("product_categories", []),
            # ---------- 每日定时任务（v2.4.0）：经营日报 + 自动备份 ----------
            "daily_report_enabled": self.get_setting("daily_report_enabled", True),   # 每天定时推送昨日经营日报
            "daily_backup_enabled": self.get_setting("daily_backup_enabled", True),   # 每天定时自动备份数据库发管理员
            "daily_task_hour": self.get_setting("daily_task_hour", 8),                # 触发时刻（东八区，0~23 点）
            # ---------- 抢单派单（v2.6.0）：匿名派单到接单群，接单员抢单回单 ----------
            "grab_enabled": self.get_setting("grab_enabled", False),              # 抢单总开关（开=报单类订单自动派到接单群）
            "grab_chat_id": self.get_setting("grab_chat_id", ""),                 # 接单群 ID（私密群，负号数字串）
            "grab_commission_pct": self.get_setting("grab_commission_pct", 20),   # 全局佣金比例%（按订单积分；商品可单独覆盖）
            "grab_freeze_hours": self.get_setting("grab_freeze_hours", 24),       # 佣金冻结小时数（0=完单立即到账）
            "grab_timeout_hours": self.get_setting("grab_timeout_hours", 24),     # 最晚回单时限（小时，从抢中时刻起算）
            "grab_warn_minutes": self.get_setting("grab_warn_minutes", 30),       # 超时前多少分钟提醒接单员
            "grab_cooldown_seconds": self.get_setting("grab_cooldown_seconds", 5),  # 抢单冷却（秒，防连点）
            "grab_penalty_minutes": self.get_setting("grab_penalty_minutes", 60), # 超时/放弃惩罚冷静期（分钟，期间禁止抢单）
            "grab_max_dispatch": self.get_setting("grab_max_dispatch", 3),        # 单笔最大重派次数（超时释放自动重派）
            "grab_withdraw_min": self.get_setting("grab_withdraw_min", 100),      # 最低提现积分
            "grab_points_per_usdt": self.get_setting("grab_points_per_usdt", 100),  # 积分兑U汇率（多少积分=1 USDT，提现自动换算）
            "grab_auto_release": self.get_setting("grab_auto_release", True),     # 完单后佣金到点自动解冻
            "grab_notify_admin": self.get_setting("grab_notify_admin", True),     # 抢中/回单/超时是否通知管理员
        }

    def update_settings(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            self.set_setting(key, value)

    # ---------- 订单通知映射（管理员引用回复回单） ----------
