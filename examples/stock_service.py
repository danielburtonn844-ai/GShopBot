"""库存池业务层（v2.4.0）：补货入库、原子售卖发货、失败回滚、到货提醒。

设计要点：
- 一件货 = 一条文字 / 一张图片 / 一个文件 / 一组图册（同一条多图消息算一件）
- 售卖的原子性由 models.take_stock_item 在事务内保证，本层负责「售出后的送达」
  与「送达失败的补偿回滚」（退积分 + 货回池 + 订单标记退款 + 通知双方）
- 补货即时镜像商品 stock 字段，售罄自动复活为在售，并触发到货提醒
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import InputMediaPhoto

from .models import STOCK_TYPES, Database, Order, Product, StockItem, now_iso

logger = logging.getLogger(__name__)

# 补货一件货的内部结构（parse_* 系列产出，add_item 消费）
# {"content_type": "text", "content_text": "..."}
# {"content_type": "photo", "file_ids": ["..."], "caption": "..."}
# {"content_type": "document", "file_ids": ["..."], "caption": "..."}
# {"content_type": "album", "file_ids": ["...", "..."], "caption": "..."}


class StockService:
    def __init__(self, db: Database, shop: Any = None) -> None:
        self.db = db
        self.shop = shop          # ShopService，用于退款/通知（售出回滚时）
        self._bot = None

    def set_bot(self, bot: Any) -> None:
        self._bot = bot

    # ==================================================================
    # 补货：消息 → 一件货
    # ==================================================================

    @staticmethod
    def parse_text(text: str) -> dict | None:
        text = (text or "").strip()
        if not text:
            return None
        return {"content_type": "text", "content_text": text, "file_ids": [], "caption": ""}

    @staticmethod
    def parse_photo(file_id: str, caption: str = "") -> dict | None:
        if not file_id:
            return None
        return {"content_type": "photo", "content_text": "", "file_ids": [file_id], "caption": caption or ""}

    @staticmethod
    def parse_document(file_id: str, caption: str = "") -> dict | None:
        if not file_id:
            return None
        return {"content_type": "document", "content_text": "", "file_ids": [file_id], "caption": caption or ""}

    @staticmethod
    def parse_album(file_ids: list[str], caption: str = "") -> dict | None:
        """同一条多图消息（media_group）聚合成一件。"""
        ids = [f for f in file_ids if f]
        if len(ids) < 2:
            return StockService.parse_photo(ids[0], caption) if ids else None
        return {"content_type": "album", "content_text": "", "file_ids": ids, "caption": caption or ""}

    def parse_message(self, message) -> dict | None:
        """把一条普通（非图册）消息解析成一件货。图册走 parse_album 由调用方聚合。"""
        if getattr(message, "document", None):
            cap = (message.caption or "").strip()
            return self.parse_document(message.document.file_id, cap)
        if getattr(message, "photo", None):
            cap = (message.caption or "").strip()
            return self.parse_photo(message.photo[-1].file_id, cap)
        return self.parse_text(getattr(message, "text", "") or "")

    def add_item(self, product_id: str, item: dict, admin_id: int = 0) -> StockItem | None:
        """入库一件货：写库存池 + 镜像商品现货数 + 售罄自动复活。返回入库后的对象。"""
        product = self.db.get_product(product_id)
        if not product or getattr(product, "delivery_mode", "fixed") != "pool":
            return None
        item_id = self.db.add_stock_item(
            product_id,
            content_type=item["content_type"],
            content_text=item.get("content_text", ""),
            file_ids=item.get("file_ids") or [],
            caption=item.get("caption", ""),
        )
        left = self.db.count_stock_items(product_id, "available")
        product.stock = left
        if product.status == "sold_out" and left > 0:
            product.status = "on"  # 补货后自动恢复在售
        self.db.save_product(product)
        return self.db.get_stock_item(item_id)

    def flush_album_buffer(self, payload: dict, product_id: str, admin_id: int = 0) -> int:
        """把补货会话里缓冲的图册提交入库（防抖任务与「完成补货」共用）。返回入库件数（0/1）。"""
        buf = payload.get("album")
        payload["album"] = None
        if not buf or not buf.get("file_ids"):
            return 0
        item = self.parse_album(list(buf["file_ids"]), buf.get("caption", ""))
        if not item:
            return 0
        if self.add_item(product_id, item, admin_id):
            payload["added"] = int(payload.get("added", 0) or 0) + 1
            return 1
        return 0

    def remove_item(self, item_id: int, admin_id: int = 0) -> tuple[bool, str]:
        """删除一件未售出的货（已售出的保留作出货凭证，不可删）。"""
        item = self.db.get_stock_item(item_id)
        if not item:
            return False, "货物不存在"
        if item.status != "available":
            return False, "该件已售出，作为出货凭证保留，不能删除"
        pid = item.product_id
        self.db.delete_stock_item(item_id)
        self._mirror_stock(pid)
        return True, pid

    def clear_sold(self, product_id: str) -> int:
        """清空已售记录（只清记录，不影响在售件）。"""
        n = self.db.clear_sold_stock_items(product_id)
        self._mirror_stock(product_id)
        return n

    def _mirror_stock(self, product_id: str) -> None:
        """把 product.stock 镜像成池内现货数，并按需切换售罄/在售。"""
        p = self.db.get_product(product_id)
        if not p or getattr(p, "delivery_mode", "fixed") != "pool":
            return
        left = self.db.count_stock_items(product_id, "available")
        p.stock = left
        if left == 0 and p.status == "on":
            p.status = "sold_out"
        elif left > 0 and p.status == "sold_out":
            p.status = "on"
        self.db.save_product(p)

    # ==================================================================
    # 售卖：发货 + 失败回滚
    # ==================================================================

    async def deliver_item(self, user_id: int, item: StockItem) -> bool:
        """把一件货送达买家私信。返回是否成功（失败由调用方决定回滚）。"""
        if not self._bot:
            return False
        header = f"📦 您的专属内容（编号 #{item.id}）"
        try:
            if item.content_type == "text":
                # 文字件内容已写进订单，这里再单独发一条便于复制
                await self._bot.send_message(
                    chat_id=user_id,
                    text=f"{header}\n\n{item.content_text}",
                )
            elif item.content_type == "photo":
                await self._bot.send_photo(
                    chat_id=user_id,
                    photo=item.file_ids[0],
                    caption=f"{header}" + (f"\n{item.caption}" if item.caption else ""),
                )
            elif item.content_type == "document":
                await self._bot.send_document(
                    chat_id=user_id,
                    document=item.file_ids[0],
                    caption=f"{header}" + (f"\n{item.caption}" if item.caption else ""),
                )
            elif item.content_type == "album":
                media = []
                for i, fid in enumerate(item.file_ids):
                    cap = ""
                    if i == 0:
                        cap = header + (f"\n{item.caption}" if item.caption else "")
                    media.append(InputMediaPhoto(media=fid, caption=cap))
                await self._bot.send_media_group(chat_id=user_id, media=media)
            else:
                return False
            return True
        except Exception as exc:
            logger.warning("库存池发货失败 item=%s user=%s: %s", item.id, user_id, exc)
            return False

    async def rollback_sale(self, order: Order, reason: str = "发货失败") -> None:
        """售出后送达失败的补偿：退积分 + 货回池 + 订单置退款 + 通知买家与管理员。"""
        logger.error("库存池售出回滚：订单 %s，原因 %s", order.id, reason)
        refunded = self.shop.refund_order(order.id, 0) if self.shop else None
        text = (
            f"⚠️ <b>订单 {order.id} 发货失败</b>\n\n"
            f"商品：{order.product_name}\n"
            f"已自动全额退款 <code>{order.points}</code> 积分，请重新下单或联系管理员"
        )
        if self._bot:
            try:
                await self._bot.send_message(chat_id=order.user_id, text=text, parse_mode="HTML")
            except Exception as exc:
                logger.warning("回滚通知买家失败：%s", exc)
            for admin in self.db.list_admins():
                try:
                    await self._bot.send_message(
                        chat_id=admin.user_id,
                        text=(
                            f"🚨 <b>库存池发货失败已自动回滚</b>\n\n"
                            f"订单：<code>{order.id}</code>\n"
                            f"买家：<code>{order.user_id}</code>\n"
                            f"商品：{order.product_name}\n"
                            f"原因：{reason}\n"
                            f"处理：{'已退款、货已回池' if refunded else '退款失败，请人工核查'}"
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

    # ==================================================================
    # 到货提醒
    # ==================================================================

    async def notify_restock(self, product_id: str) -> int:
        """补货后通知所有订阅者（取空订阅，提醒一次即退订）。返回通知人数。"""
        if not self._bot:
            return 0
        subs = self.db.pop_restock_subscribers(product_id)
        if not subs:
            return 0
        p = self.db.get_product(product_id)
        name = p.name if p else product_id
        from .ui import kb  # 延迟导入，避免与页面模块循环依赖
        sent = 0
        for uid in subs:
            try:
                await self._bot.send_message(
                    chat_id=uid,
                    text=(
                        f"🔔 <b>到货提醒</b>\n\n"
                        f"您订阅的「{name}」已补货上架，先到先得！"
                    ),
                    parse_mode="HTML",
                    reply_markup=kb([[("🛒 立即查看", f"d:{product_id}")]]),
                )
                sent += 1
                await asyncio.sleep(0.05)
            except Exception as exc:
                logger.warning("到货提醒发送失败 %s: %s", uid, exc)
        return sent
