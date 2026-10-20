"""OkPay 支付接口封装（严格对齐官方开发文档）。

协议要点：
- 接口地址：https://api.okaypay.me/shop/payLink（创建支付链接）
             https://api.okaypay.me/shop/checkDeposit（查询是否到账）
- 请求方式：POST + application/x-www-form-urlencoded（表单，不是 JSON）
- 每个请求都要带 4 个验证参数：id（商户号）、timestamp、nonce、sign
- 签名算法：剔除 sign → 递归拍平嵌套参数（dict 用点号连接）
  → 剔除 None/空字符串，布尔转 true/false → 按 key 升序拼 k=v&k=v
  → HMAC-SHA256(商户token) → hex 大写
- 成功判定：响应 JSON 的 code == 200，业务数据在 data 里
- 到账判定：checkDeposit 返回 data.status == 1 即已支付
- 金额必须原样字符串提交，不要重新格式化（官方文档明确要求）
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.okaypay.me/shop/"
EP_CREATE = "payLink"
EP_CHECK = "checkDeposit"
SUCCESS_CODE = 200
PAID_STATUS = 1


def flatten_params(data: dict, prefix: str = "") -> dict[str, str]:
    """递归拍平嵌套 dict（key 用点号连接），剔除 None/空串，布尔转 true/false。"""
    out: dict[str, str] = {}
    for k, v in data.items():
        key = str(k) if prefix == "" else f"{prefix}.{k}"
        if isinstance(v, dict):
            out.update(flatten_params(v, key))
            continue
        if isinstance(v, bool):
            out[key] = "true" if v else "false"
            continue
        if v is None or v == "":
            continue
        out[key] = str(v)
    return out


def build_base_string(params: dict) -> str:
    """按 key 升序拼 k=v&k=v（sign 已在外层剔除）。"""
    flat = flatten_params(params)
    return "&".join(f"{k}={flat[k]}" for k in sorted(flat.keys()))


def sign_params(params: dict, token: str) -> str:
    """剔除 sign 后计算 HMAC-SHA256，输出大写 hex。"""
    data = {k: v for k, v in params.items() if k != "sign"}
    base = build_base_string(data)
    return hmac.new(token.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest().upper()


class OkPay:
    """OkPay 商户接口客户端。一个实例内部复用同一个 HTTP 连接池，避免反复建连。"""

    def __init__(self, app_id: str, token: str, timeout: float = 15.0) -> None:
        self.app_id = str(app_id)
        self.token = token
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _signed_params(self, params: dict) -> dict:
        p = dict(params)
        p["id"] = self.app_id
        p["timestamp"] = int(time.time())
        p["nonce"] = secrets.token_hex(8)
        p["sign"] = sign_params(p, self.token)
        return p

    async def _post(self, endpoint: str, params: dict) -> dict[str, Any]:
        """发签名表单请求，返回业务 data；非 200 code 抛异常。"""
        client = await self._get_client()
        resp = await client.post(
            BASE_URL + endpoint,
            data=self._signed_params(params),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != SUCCESS_CODE:
            raise RuntimeError(
                f"OkPay 返回错误：code={result.get('code')} msg={result.get('msg') or result.get('error') or result}"
            )
        return result.get("data", {}) or {}

    async def create_payment(
        self,
        unique_id: str,
        amount: str,
        name: str = "积分充值",
        coin: str = "USDT",
        return_url: str = "",
    ) -> dict[str, Any]:
        """创建支付链接。amount 必须原样字符串提交，不要重新格式化。

        成功返回 {"order_id": ..., "pay_url": ..., "status": 0, ...}。
        """
        return await self._post(EP_CREATE, {
            "unique_id": unique_id,
            "name": name,
            "amount": amount,
            "coin": coin,
            "return_url": return_url,
        })

    async def check_payment(self, unique_id: str) -> dict[str, Any]:
        """查询某笔充值是否到账，返回 {"status": 0|1, "amount": ..., "order_id": ...}。"""
        return await self._post(EP_CHECK, {"unique_id": unique_id})

    @staticmethod
    def is_paid(data: dict[str, Any]) -> bool:
        """官方到账判定：data.status == 1。"""
        try:
            return int(data.get("status", 0)) == PAID_STATUS
        except (TypeError, ValueError):
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
