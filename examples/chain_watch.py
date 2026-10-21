"""链上监听（v2.5.0）：TRON(TRC20-USDT) / TON(USDT-Jetton) 只读监听客户端 + 收款地址校验。

设计原则：
- 只读：仅用公开只读 API 拉取入账记录，绝不接触私钥/助记词；
- 零新增依赖：地址校验（Base58Check / CRC16）纯标准库实现，HTTP 复用 httpx；
- 识别策略：
    TRON 的 TRC20 转账没有备注 → 给每个待支付订单分配唯一金额尾数（如 10.437），
      按「收款地址 + 金额精确相等 + 时间窗」匹配；
    TON 的 Jetton 转账可带 comment → 按 memo == 充值单号精确匹配（金额需 >= 应付）；
- 幂等：业务层入账前先写 chain_txids（txid 主键），重复轮询/重启/重放都不会重复入账。

公开 API：
- TronGrid   GET https://api.trongrid.io/v1/accounts/{address}/transactions/trc20
             （请求头 TRON-PRO-API-KEY 可选，无 Key 有较低频限）
- TonCenter v3 GET https://toncenter.com/api/v3/jetton/transfers
             （请求头 X-API-Key 可选，无 Key 有较低频限）
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx

logger = logging.getLogger(__name__)

# ---------- 主网常量 ----------
USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"   # USDT-TRC20 合约（同时是最常见的 T 地址，可用来测校验）
USDT_TON_MASTER = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"  # TON 主网 USDT Jetton Master
TOKEN_DECIMALS = 6  # USDT 在两条链上都是 6 位小数

TRONGRID_BASE = "https://api.trongrid.io"
TONCENTER_BASE = "https://toncenter.com"


# ============================================================
# 地址校验（纯标准库，无第三方依赖）
# ============================================================

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(s: str) -> bytes:
    num = 0
    for ch in s:
        num = num * 58 + _B58.index(ch)
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


def is_valid_tron(addr: str) -> bool:
    """TRON 地址（Base58Check）：T 开头 34 位，版本字节 0x41，双 SHA256 前 4 字节校验。"""
    if not addr or len(addr) != 34 or not addr.startswith("T"):
        return False
    try:
        raw = _b58decode(addr)
    except (ValueError, IndexError):
        return False
    if len(raw) != 25 or raw[0] != 0x41:
        return False
    return hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4] == raw[21:]


def _crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def is_valid_ton(addr: str) -> bool:
    """TON friendly 地址：EQ/UQ 开头 48 字符 base64url，末 2 字节为 CRC16-XMODEM。"""
    if not addr or len(addr) != 48:
        return False
    if not (addr.startswith("EQ") or addr.startswith("UQ")):
        return False
    try:
        raw = base64.urlsafe_b64decode(addr)
    except Exception:
        return False
    if len(raw) != 36 or raw[0] not in (0x11, 0x51):
        return False
    return _crc16(raw[:34]) == int.from_bytes(raw[34:], "big")


def validate_chain_address(chain: str, addr: str) -> bool:
    """按链校验收款地址；未知链一律拒绝。"""
    if chain == "trc20":
        return is_valid_tron(addr)
    if chain == "ton":
        return is_valid_ton(addr)
    return False

def ton_normalize_address(addr: str) -> str:
    """把 TON 地址统一成 raw 格式 `workchain:HEX64`，用于跨格式比较。

    TonCenter v3 的 jetton_transfers 里 `jetton_master` / `source` / `destination`
    返回的都是 raw 格式（如 `0:B113A994...`），而项目常量用的是 friendly 格式
    （如 `EQCxE6mU...`）。直接字符串比较永远不等，会把所有转账过滤掉。
    friendly（36 字节 base64url，含 tag + workchain + hash + CRC16）与 raw 两种
    输入都接受；无法识别时原样小写返回，交由调用方判定不相等。
    """
    s = (addr or "").strip()
    if not s:
        return ""
    if ":" in s:
        wc, _, h = s.partition(":")
        try:
            return f"{int(wc)}:{h.strip().upper()}"
        except (TypeError, ValueError):
            return s.lower()
    pad = "=" * (-len(s) % 4)
    try:
        raw = base64.urlsafe_b64decode(s + pad)
    except Exception:
        return s.lower()
    if len(raw) != 36:
        return s.lower()
    wc = raw[1]
    if wc >= 128:  # 单字节有符号 workchain
        wc -= 256
    return f"{wc}:{raw[2:34].hex().upper()}"


# ============================================================
# 金额工具
# ============================================================

def dec_to_str(d: Decimal) -> str:
    """Decimal → 规范化字符串：定点表示、去尾零、不带科学计数法（如 Decimal('10.437000') → '10.437'）。"""
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def parse_chain_amount(raw_value: str, decimals: int = TOKEN_DECIMALS) -> Decimal:
    """链上最小单位整数串 → 人类可读 Decimal。非法输入返回 0。"""
    try:
        return Decimal(str(raw_value)) / (Decimal(10) ** decimals)
    except (InvalidOperation, ValueError):
        return Decimal(0)


# ============================================================
# 统一的链上入账结构
# ============================================================

@dataclass
class ChainTx:
    """一笔入账到收款地址的 USDT 转账（两条链归一化后的结构）。"""
    txid: str          # 链上交易哈希（幂等键）
    amount: Decimal    # USDT 金额（人类可读）
    sender: str        # 付款地址
    timestamp: int     # 秒级时间戳
    memo: str = ""     # TON 转账备注；TRON 恒为 ""

    @property
    def amount_str(self) -> str:
        return dec_to_str(self.amount)


# ============================================================
# TronGrid（TRC20-USDT）
# ============================================================

async def fetch_tron_usdt_incoming(
    address: str,
    api_key: str = "",
    min_timestamp_ms: int = 0,
    limit: int = 50,
    timeout: float = 15.0,
    max_pages: int = 5,
) -> list[ChainTx]:
    """拉取入账到 address 的 USDT-TRC20 转账（按时间升序返回）。

    仅取 only_to=true + 官方 USDT 合约，过滤掉其它 TRC20 代币与转出记录。
    网络/接口异常返回已拉到的部分（幂等表兜住重复），绝不抛炸主循环。

    分页：TronGrid 单页上限 200。历史记录超过一页时若不翻页，按 asc 排序会
    永远只读到最早的一页，之后的新转账永远进不来。这里按 meta.fingerprint
    循环翻页（最多 max_pages 页），配合调用方传入的 min_timestamp_ms 时间游标，
    保证新单不漏、旧单不重复扫。
    """
    page_size = max(1, min(limit, 200))
    headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}
    out: list[ChainTx] = []
    seen: set[str] = set()
    fingerprint = ""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for _page in range(max(1, max_pages)):
                params: dict[str, str] = {
                    "only_to": "true",
                    "contract_address": USDT_TRC20_CONTRACT,
                    "limit": str(page_size),
                    "order_by": "block_timestamp,asc",
                }
                if min_timestamp_ms > 0:
                    params["min_timestamp"] = str(min_timestamp_ms)
                if fingerprint:
                    params["fingerprint"] = fingerprint
                resp = await client.get(
                    f"{TRONGRID_BASE}/v1/accounts/{address}/transactions/trc20",
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                payload = resp.json()

                rows = payload.get("data") or []
                for item in rows:
                    try:
                        token = item.get("token_info") or {}
                        # 双保险：即便传了 contract_address，也再核一次，防止假 USDT 代币钓鱼
                        if token.get("address") != USDT_TRC20_CONTRACT:
                            continue
                        txid = str(item.get("transaction_id") or "")
                        if not txid or txid in seen:
                            continue
                        amount = parse_chain_amount(
                            str(item.get("value") or "0"), int(token.get("decimals", TOKEN_DECIMALS))
                        )
                        if amount <= 0:
                            continue
                        seen.add(txid)
                        out.append(ChainTx(
                            txid=txid,
                            amount=amount,
                            sender=str(item.get("from") or ""),
                            timestamp=int(item.get("block_timestamp") or 0) // 1000,
                            memo="",
                        ))
                    except Exception as exc:  # 单条脏数据不影响整批
                        logger.warning("TronGrid 单条解析失败：%s", exc)
                        continue

                meta = payload.get("meta") or {}
                fingerprint = str(meta.get("fingerprint") or "")
                # 没有下一页指纹，或本页未满（已是最后一页）→ 结束翻页
                if not fingerprint or len(rows) < page_size:
                    break
    except Exception as exc:
        logger.warning("TronGrid 拉取失败：%s", exc)
        return out
    return out


# ============================================================
# TonCenter v3（TON USDT Jetton）
# ============================================================

async def fetch_ton_usdt_incoming(
    address: str,
    api_key: str = "",
    limit: int = 50,
    timeout: float = 15.0,
    min_timestamp: int = 0,
) -> list[ChainTx]:
    """拉取入账到 address 的 USDT Jetton 转账（按时间倒序拉取后翻转为升序返回）。

    仅取官方 USDT Jetton Master，comment 作为 memo 返回（业务层用它匹配充值单号）。
    min_timestamp 为秒级起点（TonCenter 的 start_utime），用于增量扫描。
    网络/接口异常返回空列表（轮询任务下轮再试），绝不抛炸主循环。

    与 TonCenter v3 实际协议的三个对齐点（早期实现全部不符，导致 TON 充值从不入账）：
    1. 收币方参数是 `owner_address`（配合 direction=in），`destination_wallet` 不被识别，
       会被静默忽略并返回任意地址的转账；
    2. 响应里的 `jetton_master` 是 raw 格式（`0:B113A9...`），必须规范化后再与
       friendly 常量比较，否则所有记录都被过滤；
    3. 转账备注在 `decoded_forward_payload.comment`，不在顶层 `comment`。
    另外 `transaction_aborted=true` 表示交易失败，不得入账。
    """
    target_master = ton_normalize_address(USDT_TON_MASTER)
    target_owner = ton_normalize_address(address)
    params: dict[str, str] = {
        "owner_address": address,
        "jetton_master": USDT_TON_MASTER,
        "direction": "in",
        "limit": str(max(1, min(limit, 100))),
        "sort": "desc",
    }
    if min_timestamp > 0:
        params["start_utime"] = str(min_timestamp)
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{TONCENTER_BASE}/api/v3/jetton/transfers", params=params, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        logger.warning("TonCenter 拉取失败：%s", exc)
        return []

    out: list[ChainTx] = []
    for item in payload.get("jetton_transfers") or []:
        try:
            if ton_normalize_address(item.get("jetton_master") or "") != target_master:
                continue  # 非官方 USDT，防假币
            if item.get("transaction_aborted"):
                continue  # 交易失败，链上没有实际转账
            dest = ton_normalize_address(item.get("destination") or "")
            if dest and target_owner and dest != target_owner:
                continue  # 双保险：收款方必须是我们配置的地址
            txid = str(item.get("transaction_hash") or "")
            if not txid:
                continue
            amount = parse_chain_amount(str(item.get("amount") or "0"))
            if amount <= 0:
                continue
            fwd = item.get("decoded_forward_payload") or {}
            comment = str(fwd.get("comment") or "").strip()
            if not comment:  # 兼容旧版本/未解码时的顶层字段
                comment = str(item.get("comment") or "").strip()
            out.append(ChainTx(
                txid=txid,
                amount=amount,
                sender=str(item.get("source") or ""),
                timestamp=int(item.get("transaction_now") or 0),
                memo=comment,
            ))
        except Exception as exc:
            logger.warning("TonCenter 单条解析失败：%s", exc)
            continue
    out.reverse()  # desc 拉取 → 升序返回，先到的先入账
    return out
