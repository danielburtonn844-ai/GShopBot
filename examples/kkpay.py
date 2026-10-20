"""KKPay 支付接口封装（严格对齐 kkpay 商户 API 对接文档）。

协议要点：
- 接口地址：https://api.kkpaywallet.com/merchant/payLink      （创建支付链接）
             https://api.kkpaywallet.com/merchant/checkDeposit（查询是否到账）
- 请求方式：POST + body 为 base64(JSON 字符串)，Content-Type: text/plain
- 每个请求都要带 4 个验证头：kkpay-id（商户号）、kkpay-timestamp（Unix 秒，
  允许 ±300 秒偏差）、kkpay-nonce（随机串，10 分钟内不可重复）、kkpay-sign（签名）
- 签名算法：待签字符串 = "{timestamp}\n{nonce}\n{method}\n{path}\n{body}"
  （method 大写；path 不含域名/查询串/结尾斜杠；body 为原样 base64 请求体）
  → HMAC-SHA256(key=secret) → base64
- 成功判定：响应 JSON 的 code == 10000，业务数据在 base64 解码后的 data 里，
  且响应头 kkpay-sign == base64(HMAC-SHA256(secret, data原文))（本模块自动验签）
- 到账判定：checkDeposit 返回 statusText == "paid"（本模块已归一化为 data.status == 1，
  与 OkPay 口径一致，is_paid() 直接可用）
- 金额必须原样字符串提交，不要重新格式化；返回中的 amount/fee 按字符串解析，避免浮点误差

无域名说明（重要）：
- payLink 的 callbackUrl 为【可选】参数，不传则平台不发异步回调。
  本模块只做「下单 + 主动查单」，机器人起一个 30~60 秒的轮询任务调 check_payment()
  即可完成到账检测，完全不需要域名/公网回调（与报单机器人 OkPay 方案同一模式）。
- 仅【出款】才强制要求平台登记 https 回调地址；本模块不涉及出款。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.kkpaywallet.com"
EP_CREATE = "/merchant/payLink"
EP_CHECK = "/merchant/checkDeposit"
SUCCESS_CODE = 10000
PAID_STATUS = 1
HEADER_PREFIX = "kkpay"


class KKPay:
    """KKPay 商户接口客户端（异步版）。

    类名/方法名/参数名刻意与报单机器人的 OkPay 保持一致：
      app_id  → 对应 KKPay 的 merchant_id（商户 ID）
      token   → 对应 KKPay 的 secret（商户密钥，仅服务端持有，勿入日志/前端）
    因此 shop_service 里的 _get_okpay() 只需把 OkPay(...) 换成 KKPay(...) 即可，
    其余代码（含 is_paid 判定、order_id/pay_url 字段）零改动。
    一个实例内部复用同一个 HTTP 连接池，避免反复建连。
    """

    def __init__(self, app_id: str, token: str, timeout: float = 15.0) -> None:
        self.app_id = str(app_id)          # merchant_id
        self.token = token                 # secret
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    # ---------- 签名 ----------

    def _hmac_b64(self, message: str) -> str:
        """HMAC-SHA256(key=secret) → base64。请求签名、响应验签、回调验签共用。"""
        return base64.b64encode(
            hmac.new(self.token.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
        ).decode()

    def _signed_headers(self, method: str, path: str, body_b64: str) -> dict[str, str]:
        """构造 4 个验证头。待签字符串五段用换行连接，顺序固定。"""
        ts = str(int(time.time()))
        nonce = secrets.token_hex(16)  # 32 位 hex，10 分钟内不复用
        canonical = f"{ts}\n{nonce}\n{method.upper()}\n{path}\n{body_b64}"
        return {
            f"{HEADER_PREFIX}-id": self.app_id,
            f"{HEADER_PREFIX}-timestamp": ts,
            f"{HEADER_PREFIX}-nonce": nonce,
            f"{HEADER_PREFIX}-sign": self._hmac_b64(canonical),
            "Content-Type": "text/plain",
        }

    # ---------- 请求 ----------

    async def _post(self, path: str, payload: dict) -> dict[str, Any]:
        """发签名请求 → 校验 code → 验响应签名 → 返回 base64 解码后的业务 dict。"""
        body_b64 = base64.b64encode(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ).decode()
        client = await self._get_client()
        resp = await client.post(
            BASE_URL + path,
            content=body_b64,
            headers=self._signed_headers("POST", path, body_b64),
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != SUCCESS_CODE:
            raise RuntimeError(
                f"KKPay 返回错误：code={result.get('code')} msg={result.get('message') or result}"
            )
        data_b64 = result.get("data", "") or ""
        # 官方要求：成功响应带 kkpay-sign 头，签名对象是 data 字段原文（base64 串本身）
        resp_sign = resp.headers.get(f"{HEADER_PREFIX}-sign", "")
        if not hmac.compare_digest(self._hmac_b64(data_b64), resp_sign):
            raise RuntimeError("KKPay 响应验签失败（kkpay-sign 不匹配），数据不可信，已丢弃")
        return json.loads(base64.b64decode(data_b64))

    # ---------- 业务接口（与 OkPay 同签名，方便直接替换） ----------

    async def create_payment(
        self,
        unique_id: str,
        amount: str,
        name: str = "积分充值",
        coin: str = "usdt",
        return_url: str = "",
    ) -> dict[str, Any]:
        """创建支付链接。amount 必须原样字符串提交，不要重新格式化。

        成功返回归一化后的 dict：
          {"order_id": <平台交易号txid>, "txid": <同左>, "pay_url": <Telegram支付链接>,
           "amount": ..., "currency": ..., "fee": ...}
        pay_url 请原样发给用户，勿自行拼接。重复 unique_id 会返回已存在订单（幂等）。
        """
        payload: dict[str, Any] = {
            "amount": amount,
            "coin": coin,
            "userOrder": unique_id,
            "name": name,
        }
        if return_url:
            payload["return_url"] = return_url
        # 注意：不传 callbackUrl —— 无域名模式，到账靠轮询 check_payment() 检测
        data = await self._post(EP_CREATE, payload)
        # 字段归一化：对齐 OkPay 的 create_payment 返回口径（order_id + pay_url）
        data["order_id"] = data.get("txid", "")
        return data

    async def check_payment(self, unique_id: str) -> dict[str, Any]:
        """查询某笔充值是否到账（按商户订单号 userOrder 查）。

        返回归一化后的 dict，关键字段：
          status: 1=已支付, 0=未支付/其它（已与 OkPay 口径对齐，is_paid 直接用）
          statusText / statusTextCN / amount / currency / fee / txid / userOrder / payUser
        """
        data = await self._post(EP_CHECK, {"userOrder": unique_id})
        # 状态归一化：官方 checkDeposit 返回 statusText（pending/paid/cancelled/failed），
        # 映射为 OkPay 的 status（1=已支付），保证上层 is_paid() 零改动
        data["status"] = PAID_STATUS if str(data.get("statusText", "")).lower() == "paid" else 0
        return data

    @staticmethod
    def is_paid(data: dict[str, Any]) -> bool:
        """到账判定：data.status == 1（与 OkPay 完全一致的口径）。"""
        try:
            return int(data.get("status", 0)) == PAID_STATUS
        except (TypeError, ValueError):
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ----------------------------------------------------------------------
# 兼容别名：把代码里的 OkPay 直接换成 KKPay 也能跑（类名替换用）
# ----------------------------------------------------------------------
OkPay = KKPay  # type: ignore[assignment]


# ----------------------------------------------------------------------
# 以下为「未来扩展」预留：回调验签与出款确认应答。
# 无域名模式【不需要】用到它们；只有当以后配了域名 + https 回调地址、
# 或要开通出款功能时才需要。保留在这里是为了让接手的 AI 不必再翻文档。
# ----------------------------------------------------------------------

def verify_callback(secret: str, body_b64: str, sign: str) -> bool:
    """验证 KKPay 发来的回调请求（deposit / withdrawalPendingConfirm / withdraw）。

    body_b64 = 回调的【原始】HTTP body（base64 原文，勿用解析后的 JSON 对象）；
    sign     = 回调请求头 kkpay-sign。返回 True 才可信，然后 base64 解码 body 取业务数据。
    回调请按 txid 幂等去重；处理成功返回纯文本 SUCCESS。
    """
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body_b64.encode("utf-8"), hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, sign)


def sign_confirm_reply(secret: str, txid: str, decision: str) -> str:
    """【出款确认回调 withdrawalPendingConfirm】的应答签名（放款挑战应答）。

    收到该回调后必须：① 响应体返回大写 SUCCESS(放款)/FAIL(拒绝)；
    ② 响应头 kkpay-sign 放本函数返回值。缺一平台一律不放款。
    签名对象固定为 "{txid}:{大写decision}"，分隔符是英文冒号。
    注意：出款确认回调只发往平台登记的 https 回调地址，无域名模式不涉及。
    """
    d = str(decision).strip().upper()
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), f"{txid}:{d}".encode(), hashlib.sha256).digest()
    ).decode()


# ----------------------------------------------------------------------
# 最小用法示例（与报单机器人 OkPay 的用法逐行对应）：
#
#   kkpay = KKPay("<merchant_id>", "<secret>")          # 替换 OkPay(app_id, token)
#
#   # 1) 下单（金额原样字符串，不要重新格式化）
#   data = await kkpay.create_payment("R20260907001", "10")
#   pay_url = data["pay_url"]      # 发给用户的 Telegram 支付链接，原样使用
#   txid    = data["order_id"]     # 平台交易号，建议与 userOrder 一起入库
#
#   # 2) 到账检测（无域名：30~60 秒轮询一次所有 pending 单）
#   info = await kkpay.check_payment("R20260907001")
#   if KKPay.is_paid(info):        # 口径与 OkPay.is_paid() 完全一致
#       ...                        # 入账（记得按 txid/userOrder 幂等防重）
#
#   # 3) 程序退出时
#   await kkpay.close()
# ----------------------------------------------------------------------
