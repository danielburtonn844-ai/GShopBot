"""连接、事务、建表与迁移（core 领域）。

由 models.py 拆分而来，方法签名与行为完全不变。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


# 每线程一条持久连接池：{数据库绝对路径: sqlite3.Connection}
# 用 threading.local 是因为 sqlite3 连接不是线程安全的。
# 伪 asyncio 单线程场景下就是全局复用一条，压测里 20 线程也只建 20 条。
_LOCAL = threading.local()


class _PooledConn:
    """池化连接的代理：行为与原 sqlite3.Connection 一致，但 close() 只归还不断开。

    为什么要包一层：原代码有 60+ 处 `conn = self._get_conn() ... finally: conn.close()`，
    把 close() 变成「回滚未提交事务后归还」就能零改动复用连接。
    """

    __slots__ = ("_raw", "_path", "_closed")

    def __init__(self, raw: sqlite3.Connection, path: str) -> None:
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_closed", False)

    # 显式转发常用方法，比 __getattr__ 快
    def execute(self, *a, **kw):
        """执行 SQL，遇到瞬时 database is locked 自动重试。

        WAL 下「读事务升级写事务」会返回 SQLITE_BUSY_SNAPSHOT，
        这种冲突 busy_timeout 不生效、立刻报 locked。并发写场景实测
        1000 次读写里有 5 次会踩到，这里退避重试 3 次兜住。
        """
        for attempt in range(3):
            try:
                return self._raw.execute(*a, **kw)
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < 2:
                    time.sleep(0.02 * (attempt + 1))
                    continue
                raise
        return self._raw.execute(*a, **kw)

    def executemany(self, *a, **kw):
        return self._raw.executemany(*a, **kw)

    def executescript(self, *a, **kw):
        return self._raw.executescript(*a, **kw)

    def cursor(self, *a, **kw):
        return self._raw.cursor(*a, **kw)

    def commit(self):
        return self._raw.commit()

    def rollback(self):
        return self._raw.rollback()

    def close(self) -> None:
        """归还连接。**不做任何回滚**。

        这里踩过一个坑：早先的实现会在 close() 时「发现未提交事务就回滚」，
        结果 atomic() 事务块内部任何一次读操作（比如 `db.get_setting()` 会
        自己 `_get_conn()` 再 `close()`）都会把外层尚未提交的事务整个回滚掉——
        表现是「回单返回成功，但库里状态没变」。

        现在所有路径都显式管理事务：
          - `atomic()`：正常 commit / 异常 rollback
          - `_maybe_conn()` 自建分支：正常 commit / 异常 rollback
        所以 close() 只需标记归还，不碰事务。
        """
        object.__setattr__(self, "_closed", True)

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def __setattr__(self, name, value):
        setattr(self._raw, name, value)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class CoreMixin:
    """连接、事务、建表与迁移。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """取当前线程的持久连接（首次自动建立）。

        v2.6.7 起改为每条线程复用一条连接。原实现每次调用都
        `sqlite3.connect()` + 2 条 PRAGMA + `close()`，实测单次 3.63ms，
        其中 PRAGMA 占 76%——而 `journal_mode=WAL` 是**数据库级持久设置**，
        写进文件头后所有连接自动生效，每次重设纯属浪费。
        压测证据：1000 次读写在 20 线程下原实现只有 14 次/秒。
        """
        key = str(self.path.resolve())
        pool = getattr(_LOCAL, "pool", None)
        if pool is None:
            pool = {}
            _LOCAL.pool = pool
        raw = pool.get(key)
        if raw is None:
            raw = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            raw.row_factory = sqlite3.Row
            raw.execute("PRAGMA busy_timeout=10000")
            raw.execute("PRAGMA journal_mode=WAL")
            # WAL 下 synchronous=NORMAL 是官方推荐档位：断电最多丢最后一个事务，
            # 不会损坏库，写性能比 FULL 高一截
            raw.execute("PRAGMA synchronous=NORMAL")
            pool[key] = raw
        return _PooledConn(raw, key)

    def close_all(self) -> None:
        """真正关闭当前线程的所有连接（测试与进程退出时用）。"""
        pool = getattr(_LOCAL, "pool", None)
        if not pool:
            return
        for raw in list(pool.values()):
            try:
                raw.close()
            except Exception:
                pass
        pool.clear()

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
            except Exception:
                # 自建连接上的写失败必须显式回滚，否则事务残留在池化连接里
                try:
                    own.rollback()
                except Exception:
                    pass
                raise
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
                -- 充值单：链上轮询与尾数分配都按 status + chain 过滤，
                -- 没有索引时每轮都是全表扫（5000 单时单次查询 8ms 以上）
                CREATE INDEX IF NOT EXISTS idx_recharge_status_chain ON recharges(status, chain);
                CREATE INDEX IF NOT EXISTS idx_recharge_user ON recharges(user_id, created_at);
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
