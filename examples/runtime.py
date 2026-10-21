"""运行时单例：数据库与业务服务的唯一实例（v2.4.0 模块化拆分）。

所有页面/回调/后台任务模块都从这里取 db/service/supply，避免循环导入：
    runtime 只依赖 models / shop_service / supply_service，不反向依赖任何上层模块。
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

from .grab_service import GrabService
from .models import Database
from .shop_service import ShopService
from .stock_service import StockService
from .supply_service import SupplyService

logger = logging.getLogger(__name__)

load_dotenv()

# 版本号：每次交付必须递增并在 CHANGELOG.md 记录（见交付规范）
VERSION = "2.6.4"

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("请在 .env 中设置 BOT_TOKEN")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")

# 项目根目录（examples/ 的上一级），数据库默认放在根目录 data/ 下
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据库路径：默认 data/shop.db；可用环境变量 SHOP_DB_PATH 覆盖（自检/多开场景用，
# 防止测试脚本误碰正式库——2026-09-07 数据事故教训）。
db = Database(os.environ.get("SHOP_DB_PATH") or os.path.join(BASE_DIR, "data", "shop.db"))
service = ShopService(db, None)
service.add_admin(ADMIN_ID, "主管理员")
supply = SupplyService(db, service)
stock = StockService(db, service)
# 抢单派单（v2.6.0）：匿名派单到接单群，接单员抢单回单赚佣金
grab = GrabService(db, service)
