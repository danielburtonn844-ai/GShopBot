"""连接、事务、建表与迁移（core 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class CoreMixin:
    """连接、事务、建表与迁移。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        # WAL 模式 + busy_timeout：读写并发更稳，避免偶发 database is locked
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def atomic(self) -> Iterator[sqlite3.Connection]:
        """事务上下文：块内所有写操作要么全部提交，要么全部回滚。

        涉及资金（积分扣减/退回/到账、库存变动、订单写入）的多步操作必须
        放在同一个 atomic 块里，防止中途崩溃造成钱货不一致。
        """
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _maybe_conn(self, conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
        """有事务连接就复用（不提交不关闭），否则自己开一个（自动提交）。"""
        if conn is not None:
            yield conn
        else:
            own = self._get_conn()
            try:
                yield own
                own.commit()
            finally:
                own.close()

    def _init_db(self) -> None:
        conn = self._get_conn()
        try:
            # 检查 admins 表是否有 added_by 字段
            cursor = conn.execute("PRAGMA table_info(admins)")
            columns = [row[1] for row in cursor.fetchall()]
            if "added_by" not in columns:
                # 旧表结构，需要重建
                logger.info("检测到旧数据库结构，重建 admins 表")
                conn.execute("DROP TABLE IF EXISTS admins")
                conn.execute("""
                    CREATE TABLE admins (
                        user_id INTEGER PRIMARY KEY,
                        name TEXT DEFAULT '',
                        permissions TEXT DEFAULT '[]',
                        added_by INTEGER DEFAULT 0,
                        added_at TEXT DEFAULT ''
                    )
                """)
                conn.commit()

            # 检查 channels 表是否存在
            cursor = conn.execute("PRAGMA table_info(channels)")
            if not cursor.fetchall():
                conn.execute("""
                    CREATE TABLE channels (
                        id TEXT PRIMARY KEY,
                        chat_id TEXT DEFAULT '',
                        title TEXT DEFAULT '',
                        username TEXT DEFAULT '',
                        added_by INTEGER DEFAULT 0,
                        added_at TEXT DEFAULT ''
                    )
                """)
                conn.commit()

            # 创建其他表
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT DEFAULT '',
                    first_name TEXT DEFAULT '',
                    last_name TEXT DEFAULT '',
                    points INTEGER DEFAULT 0,
                    total_recharge REAL DEFAULT 0,
                    total_spent INTEGER DEFAULT 0,
                    is_banned INTEGER DEFAULT 0,
                    invite_code TEXT DEFAULT '',
                    invited_by TEXT DEFAULT '',
                    created_at TEXT DEFAULT '',
                    last_active TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS products (
                    id TEXT PRIMARY KEY,
                    name TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    points INTEGER DEFAULT 0,
                    kind TEXT DEFAULT 'product',
                    stock INTEGER DEFAULT -1,
                    limit_per_user INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'on',
                    is_top INTEGER DEFAULT 0,
                    tags TEXT DEFAULT '[]',
                    auto_delivery TEXT DEFAULT '',
                    photo_file_id TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    delivery_mode TEXT DEFAULT 'fixed',
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER DEFAULT 0,
                    username TEXT DEFAULT '',
                    product_id TEXT DEFAULT '',
                    product_name TEXT DEFAULT '',
                    product_kind TEXT DEFAULT 'product',
                    points INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    priority TEXT DEFAULT 'normal',
                    note TEXT DEFAULT '',
                    reply_content TEXT DEFAULT '',
                    reply_admin_id INTEGER DEFAULT 0,
                    reply_at TEXT DEFAULT '',
                    locked_by INTEGER DEFAULT 0,
                    stock_item_id INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT '',
                    completed_at TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS recharges (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER DEFAULT 0,
                    username TEXT DEFAULT '',
                    amount REAL DEFAULT 0,
                    points INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    pay_url TEXT DEFAULT '',
                    okpay_order_id TEXT DEFAULT '',
                    gateway TEXT DEFAULT 'okpay',
                    created_at TEXT DEFAULT '',
                    paid_at TEXT DEFAULT '',
                    chain TEXT DEFAULT '',
                    expected_amount TEXT DEFAULT '',
                    txid TEXT DEFAULT '',
                    memo TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS chain_txids (
                    txid TEXT PRIMARY KEY,
                    recharge_id TEXT DEFAULT '',
                    chain TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS order_notifications (
                    chat_id INTEGER,
                    message_id INTEGER,
                    order_id TEXT,
                    created_at TEXT DEFAULT '',
                    PRIMARY KEY (chat_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS point_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER DEFAULT 0,
                    change INTEGER DEFAULT 0,
                    balance_after INTEGER DEFAULT 0,
                    reason TEXT DEFAULT '',
                    operator_id INTEGER DEFAULT 0,
                    ref TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
                CREATE INDEX IF NOT EXISTS idx_orders_product ON orders(product_id);
                CREATE INDEX IF NOT EXISTS idx_point_history_user ON point_history(user_id);
                CREATE TABLE IF NOT EXISTS supply_posts (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER DEFAULT 0,
                    username TEXT DEFAULT '',
                    display_name TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    content TEXT DEFAULT '',
                    content_html TEXT DEFAULT '',
                    photo_file_id TEXT DEFAULT '',
                    channel TEXT DEFAULT 'bot',
                    cost INTEGER DEFAULT 0,
                    channel_leg INTEGER DEFAULT 0,
                    anonymous INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    bot_ok INTEGER DEFAULT 0,
                    channel_ok INTEGER DEFAULT 0,
                    channel_msg TEXT DEFAULT '',
                    refunded INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_supply_user ON supply_posts(user_id);
                CREATE INDEX IF NOT EXISTS idx_supply_status ON supply_posts(status, created_at);
                -- 库存池（v2.4.0）：一件货只卖一次，售出与订单绑定可追溯
                CREATE TABLE IF NOT EXISTS stock_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id TEXT NOT NULL,
                    content_type TEXT DEFAULT 'text',
                    content_text TEXT DEFAULT '',
                    file_ids TEXT DEFAULT '[]',
                    caption TEXT DEFAULT '',
                    status TEXT DEFAULT 'available',
                    order_id TEXT DEFAULT '',
                    buyer_id INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT '',
                    sold_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_stock_ps ON stock_items(product_id, status, id);
                CREATE INDEX IF NOT EXISTS idx_stock_order ON stock_items(order_id);
                -- 到货提醒订阅（v2.4.0）：售罄商品补货后自动私信订阅者，提醒后即删除
                CREATE TABLE IF NOT EXISTS restock_subs (
                    product_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at TEXT DEFAULT '',
                    PRIMARY KEY (product_id, user_id)
                );
                -- 管理员操作日志（v2.4.0）：多管理员协作可追溯
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER DEFAULT 0,
                    action TEXT DEFAULT '',
                    detail TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(id);
                -- 接单员档案（v2.6.0 抢单）：状态/余额/业绩一表管
                CREATE TABLE IF NOT EXISTS grabbers (
                    user_id INTEGER PRIMARY KEY,
                    seq INTEGER DEFAULT 0,
                    username TEXT DEFAULT '',
                    name TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    balance_available INTEGER DEFAULT 0,
                    balance_frozen INTEGER DEFAULT 0,
                    total_commission INTEGER DEFAULT 0,
                    completed_orders INTEGER DEFAULT 0,
                    timeout_orders INTEGER DEFAULT 0,
                    giveup_orders INTEGER DEFAULT 0,
                    cooldown_until TEXT DEFAULT '',
                    last_grab_at TEXT DEFAULT '',
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                );
                -- 佣金流水（v2.6.0）：冻结/解冻/追回全留痕
                CREATE TABLE IF NOT EXISTS grab_commissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT DEFAULT '',
                    grabber_id INTEGER DEFAULT 0,
                    amount INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'frozen',
                    frozen_until TEXT DEFAULT '',
                    released_at TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_gc_grabber ON grab_commissions(grabber_id, status);
                CREATE INDEX IF NOT EXISTS idx_gc_order ON grab_commissions(order_id);
                -- 提现单（v2.6.0）：申请/审核/打款留痕
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id TEXT PRIMARY KEY,
                    grabber_id INTEGER DEFAULT 0,
                    amount INTEGER DEFAULT 0,
                    address TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    txid TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    processed_by INTEGER DEFAULT 0,
                    processed_at TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_wd_grabber ON withdrawals(grabber_id, status);
                -- 申诉（v2.6.0）：接单员异议与管理员裁定
                CREATE TABLE IF NOT EXISTS grab_appeals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    grabber_id INTEGER DEFAULT 0,
                    order_id TEXT DEFAULT '',
                    content TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    reply TEXT DEFAULT '',
                    handled_by INTEGER DEFAULT 0,
                    handled_at TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_ga_status ON grab_appeals(status, id);
            """)
            conn.commit()

            # 迁移：老库的 recharges 表补 gateway 列（支付渠道，老数据默认 okpay）。
            # CREATE TABLE IF NOT EXISTS 不会给已存在的表加列，必须单独 ALTER。
            cursor = conn.execute("PRAGMA table_info(recharges)")
            rc_columns = [row[1] for row in cursor.fetchall()]
            if rc_columns and "gateway" not in rc_columns:
                logger.info("检测到旧 recharges 表结构，补充 gateway 列")
                conn.execute("ALTER TABLE recharges ADD COLUMN gateway TEXT DEFAULT 'okpay'")
                conn.commit()

            # 迁移：老库的 supply_posts 表补 content_html 列（v2.3.0 富文本，
            # 老数据该列为空串，发送时自动走「转义纯文本」的旧逻辑，完全兼容）。
            cursor = conn.execute("PRAGMA table_info(supply_posts)")
            sp_columns = [row[1] for row in cursor.fetchall()]
            if sp_columns and "content_html" not in sp_columns:
                logger.info("检测到旧 supply_posts 表结构，补充 content_html 列")
                conn.execute("ALTER TABLE supply_posts ADD COLUMN content_html TEXT DEFAULT ''")
                conn.commit()

            # 迁移：老库 supply_posts 表补 channel_leg 列（v2.6.2 退款快照）。
            # 老数据为 0，退款时自动回退到「按当前价格估算」的旧口径，不会因迁移而多退。
            if sp_columns and "channel_leg" not in sp_columns:
                logger.info("检测到旧 supply_posts 表结构，补充 channel_leg 列")
                conn.execute("ALTER TABLE supply_posts ADD COLUMN channel_leg INTEGER DEFAULT 0")
                conn.commit()

            # 迁移：老库 products 表补 category / delivery_mode 列（v2.4.0 商品分类与库存池，
            # 老数据默认 无分类 + fixed 固定发货，行为与旧版完全一致）。
            cursor = conn.execute("PRAGMA table_info(products)")
            pr_columns = [row[1] for row in cursor.fetchall()]
            if pr_columns and "category" not in pr_columns:
                logger.info("检测到旧 products 表结构，补充 category 列")
                conn.execute("ALTER TABLE products ADD COLUMN category TEXT DEFAULT ''")
                conn.commit()
            if pr_columns and "delivery_mode" not in pr_columns:
                logger.info("检测到旧 products 表结构，补充 delivery_mode 列")
                conn.execute("ALTER TABLE products ADD COLUMN delivery_mode TEXT DEFAULT 'fixed'")
                conn.commit()

            # 迁移：老库 orders 表补 stock_item_id 列（v2.4.0 库存池发货绑定，0=非池商品）。
            cursor = conn.execute("PRAGMA table_info(orders)")
            od_columns = [row[1] for row in cursor.fetchall()]
            if od_columns and "stock_item_id" not in od_columns:
                logger.info("检测到旧 orders 表结构，补充 stock_item_id 列")
                conn.execute("ALTER TABLE orders ADD COLUMN stock_item_id INTEGER DEFAULT 0")
                conn.commit()

            # 迁移：老库 orders 表补抢单十一列（v2.6.0，老数据全为空/0=非派单订单，行为不变）。
            cursor = conn.execute("PRAGMA table_info(orders)")
            od_cols = [row[1] for row in cursor.fetchall()]
            if od_cols:
                for col, ddl in (
                    ("grab_status", "ALTER TABLE orders ADD COLUMN grab_status TEXT DEFAULT ''"),
                    ("grabber_id", "ALTER TABLE orders ADD COLUMN grabber_id INTEGER DEFAULT 0"),
                    ("grabber_name", "ALTER TABLE orders ADD COLUMN grabber_name TEXT DEFAULT ''"),
                    ("commission", "ALTER TABLE orders ADD COLUMN commission INTEGER DEFAULT 0"),
                    ("grab_at", "ALTER TABLE orders ADD COLUMN grab_at TEXT DEFAULT ''"),
                    ("deadline", "ALTER TABLE orders ADD COLUMN deadline TEXT DEFAULT ''"),
                    ("dispatch_chat", "ALTER TABLE orders ADD COLUMN dispatch_chat TEXT DEFAULT ''"),
                    ("dispatch_msg", "ALTER TABLE orders ADD COLUMN dispatch_msg INTEGER DEFAULT 0"),
                    ("dispatch_count", "ALTER TABLE orders ADD COLUMN dispatch_count INTEGER DEFAULT 0"),
                    ("last_dispatch_at", "ALTER TABLE orders ADD COLUMN last_dispatch_at TEXT DEFAULT ''"),
                    ("warn_sent", "ALTER TABLE orders ADD COLUMN warn_sent INTEGER DEFAULT 0"),
                ):
                    if col not in od_cols:
                        logger.info("检测到旧 orders 表结构，补充 %s 列", col)
                        conn.execute(ddl)
                        conn.commit()

            # 迁移：老库 products 表补 commission_pct 列（v2.6.0 抢单佣金比例，-1=跟随全局）。
            cursor = conn.execute("PRAGMA table_info(products)")
            pr_cols = [row[1] for row in cursor.fetchall()]
            if pr_cols and "commission_pct" not in pr_cols:
                logger.info("检测到旧 products 表结构，补充 commission_pct 列")
                conn.execute("ALTER TABLE products ADD COLUMN commission_pct INTEGER DEFAULT -1")
                conn.commit()

            # 迁移：老库 grabbers 表补专属入群链接两列（v2.6.1）。
            cursor = conn.execute("PRAGMA table_info(grabbers)")
            gb_cols = [row[1] for row in cursor.fetchall()]
            if gb_cols:
                for col, ddl in (
                    ("invite_link", "ALTER TABLE grabbers ADD COLUMN invite_link TEXT DEFAULT ''"),
                    ("joined_group", "ALTER TABLE grabbers ADD COLUMN joined_group INTEGER DEFAULT 0"),
                ):
                    if col not in gb_cols:
                        logger.info("检测到旧 grabbers 表结构，补充 %s 列", col)
                        conn.execute(ddl)
                        conn.commit()

            # 迁移：老库 withdrawals 表补 amount_usdt 列（v2.6.1，提现按汇率自动折算 USDT）。
            cursor = conn.execute("PRAGMA table_info(withdrawals)")
            wd_cols = [row[1] for row in cursor.fetchall()]
            if wd_cols and "amount_usdt" not in wd_cols:
                logger.info("检测到旧 withdrawals 表结构，补充 amount_usdt 列")
                conn.execute("ALTER TABLE withdrawals ADD COLUMN amount_usdt REAL DEFAULT 0")
                conn.commit()

            # 迁移：老库 recharges 表补冷钱包四列（v2.5.0，老数据全部为空串，行为不变）。
            cursor = conn.execute("PRAGMA table_info(recharges)")
            rc_cols = [row[1] for row in cursor.fetchall()]
            if rc_cols:
                for col, ddl in (
                    ("chain", "ALTER TABLE recharges ADD COLUMN chain TEXT DEFAULT ''"),
                    ("expected_amount", "ALTER TABLE recharges ADD COLUMN expected_amount TEXT DEFAULT ''"),
                    ("txid", "ALTER TABLE recharges ADD COLUMN txid TEXT DEFAULT ''"),
                    ("memo", "ALTER TABLE recharges ADD COLUMN memo TEXT DEFAULT ''"),
                ):
                    if col not in rc_cols:
                        logger.info("检测到旧 recharges 表结构，补充 %s 列", col)
                        conn.execute(ddl)
                        conn.commit()
            # 链上交易防重表（txid 主键，天然幂等）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chain_txids (
                    txid TEXT PRIMARY KEY,
                    recharge_id TEXT DEFAULT '',
                    chain TEXT DEFAULT '',
                    created_at TEXT DEFAULT ''
                )
            """)
            conn.commit()
        finally:
            conn.close()

    # ---------- 用户 ----------
