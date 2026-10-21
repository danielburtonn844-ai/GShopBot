# CHANGELOG — G Shop Bot

## v2.6.6 · 2026-10-21 · 欢迎图常驻 + 五个静默缺陷

### 功能：主界面欢迎图不再消失（参照扎堆号铺的媒体感知更新）

**问题复现**：`/start` 发的是带欢迎图的图文消息 → 点任意按钮进子页 → 图被 `edit_message_text` 抹成纯文字 → 再点「主菜单」返回，走的仍是无图分支，**图彻底回不来**。

**根因**：`send_or_edit(query, text, kb)` 只有「有图 → 发新消息」和「无图 → 改文字」两条路，而 Tele­gram 的 `editMessageText` 不能保留图片，`editMessageCaption` 不会在纯文字消息上创建图片。

**修法**（对齐扎堆号铺的 `send_or_edit` 设计）：把「当前消息承载什么媒体」纳入决策。

| 当前消息 | 目标 | 处理方式 |
|---|---|---|
| 图文 | 同一张图 | `edit_message_caption` 就地改图注，图不丢、不删消息 |
| 图文 | 另一张图 | `edit_message_media` 原地换图 |
| 图文 | 无图 | 删旧消息 → 发纯文字 |
| 文字 | 有图 | 删旧消息 → 发图文（Telegram 不支持文→图就地转换） |
| 文字 | 无图 | `edit_message_text` 就地改 |

配套：
- 新增 `pages_user.home_photo()`，`m:root` / `m:checkjoin` / 未知回调兜底三处回主界面都带它
- 文案超过 1024 字符（caption 上限）时主动放弃带图，退回纯文字，避免编辑失败整页打不开
- HTML 解析失败 / `Message is not modified` 的安全降级全部保留

### 修复

- **[充值] 普通充值入口（OkPay/KKPay）积分精度**
  `points = int(amount * rate)` 走 float。`0.29 USDT @ 100 积分/U` 得 28（应为 29）、`0.57` 得 56、`0.58` 得 57。v2.6.2 只修了冷钱包入口，这个主入口漏了。
  修法：`int(Decimal(str(amount)) * Decimal(str(rate)))`。

- **[充值] 充值确认页与实付不一致**
  `page_recharge_confirm` 同样用 `int(amount * rate)` 预览。页面上写「获得 100 积分」、实际入账 99，是最容易招投诉的一类不一致。
  修法：与 `create_recharge` 用同一套 Decimal 算法。

- **[资金安全] 提现审核「通过」缺少事务与 CAS**
  `approve_withdrawal` 是「读 → 判断 status == pending → 写」，三步不在事务内。两个管理员同时点「通过」和「拒绝」时，拒绝方在事务里退回余额，通过方随后无条件覆盖成 `paid`——用户既拿到打款又拿回积分。
  修法：`atomic()` + 新增 `set_withdrawal_status(expect="pending")` 做 CAS。

- **[订单] 管理员回单未放事务**
  `complete_order` 同样是读-改-写三分离，双击或两个管理员同时点会各写一次，用户收到两条回单通知。
  修法：整体放进 `atomic()`。

- **[后台] 自动备份开关无效**
  `auto_backup()` 从未读取 `daily_backup_enabled`，管理员在面板关掉后照样每天 04:30 备份。
  修法：循环内先判开关。

- **[广播] 用户超过 1 万时静默漏人**
  商品广播与供需广播都用 `list_users(limit=10000)`，第 10001 个之后的用户永远收不到，且没有任何报错。
  修法：新增 `db.iter_users(batch=500)` 分批遍历，内存占用也更低。

- **[补货] 按 username 找人只查前 500 个用户**
  `for u in db.list_users(limit=500)` 线性比对，用户超 500 后新用户永远找不到。
  修法：新增 `get_user_by_username()` 走 SQL 精确查询。

- **[统计] 订单管理页总数显示错误**
  `len(orders)` 是 `LIMIT 200` 截断后的长度，超过 200 单时显示「共 200 条」。
  修法：改用 `count_orders()` 真实计数。

- **[设置] 移除僵尸设置项 `order_expire_minutes`**
  只在 `get_settings()` 里定义了默认值 30，全项目没有任何代码读取，界面上也没有开关。留着会让后人以为存在「订单超时自动取消」功能。

### 性能

- **强制关注检查加 60 秒缓存**
  每点一次主菜单都要对所有强制频道调一次 `get_chat_member`（一次网络往返），配 3 个频道就是 3 次 API 调用。加 `(user_id, 频道ID元组)` → 结果的 TTL 缓存，频道变化时 key 自动失效。

### 测试

- `test_extra.py` 从 71 项扩到 **85 项**，新增 14 项专门验证欢迎图的六种媒体切换路径
- `verify_issues.py` 从 43 项扩到 **47 项**，新增普通充值入口 4 种精度组合
- 覆盖率 76% → **75%**（新增代码拉低，绝对行数从 0 到覆盖）

```
test_bot_full.py        → ✅ 7207 项
verify_issues.py        → ✅ 47 项
test_extra.py           → ✅ 85 项
test_coverage_boost.py  → ✅ 75 项
ruff check              → All checks passed
```

---

## v2.6.5 · 2026-10-21 · 覆盖率补强 + 一个真实缺陷

### 修复

- **[抢单] `_toast()` 参数名不匹配，三处错误提示直接抛 TypeError**

```python
# 定义
async def _toast(text: str, alert: bool = True) -> None: ...
# 调用（3 处）
await _toast(err or "生成失败", show_alert=True)
```

`alert` 与 `show_alert` 不匹配 → `TypeError: _toast() got an unexpected keyword argument 'show_alert'`。

受影响的三条路径都是**错误提示**，也就是说「出了错要告诉用户」的那条路本身会炸：

| 位置 | 场景 |
|---|---|
| `gb:inv` | 专属入群链接生成失败 |
| `a:gblink` | 管理员重发接单员链接失败 |
| `a:gblink` | 链接生成成功但私聊发送失败 |

用户看到的是「点了没反应」，而不是真实原因。修法：参数名对齐 Bot API 的 `answerCallbackQuery(show_alert=...)`，并加 `**_ignored` 兜住历史别名。

这个 bug 是写 `test_coverage_boost.py` 时被测出来的——原来的 7207 项自检没覆盖到这三条失败分支。

### 新增 test_coverage_boost.py（75 项）

补之前完全没走到的模块：

| 组 | 覆盖内容 |
|---|---|
| **供需全流程** | 发布扣费 / 空内容 / 超长 / 暂停 / 积分不足 / 封禁 / 日限 / 频道未配置 / 审核通过 / 审核拒绝退款 / 重复审核幂等 / bot 广播 / 频道发送失败退腿 / 删帖退款 / 统计 |
| **抢单回调** | `gb:m` `gb:doing` `gb:done` `gb:money` `gb:wd` `gb:wdnew` `gb:inv` `gb:rank` `gb:aps` + 管理端 `a:grab` `a:gbers` `a:gbadd` `a:gords` `a:gwds` `a:gaps` `a:gset` |
| **后台任务** | 5 个常驻协程各跑完一轮（把 `asyncio.sleep` 换成立即返回，第二轮抛 `CancelledError` 退出） |
| **链上异常** | 网络异常 / 坏 JSON / 单条脏数据不影响整批 |
| **输入状态机** | 无状态消息 / 群聊消息被忽略 / 搜索状态 / 未知状态清理 |

### 覆盖率

| 模块 | 之前 | 现在 |
|---|---|---|
| `supply_service.py` | 26% | **90%** |
| `chain_watch.py` | 37% | **74%** |
| `cb_grab.py` | 34% | 48% |
| `jobs.py` | 37% | 通过 |
| **总计** | 71% | **76%** |

CI 门槛从 70% 提到 **75%**。

### 测试策略上的两个坑（记录备查）

1. **常驻协程不能直接 await**
   `jobs.py` 里都是 `while True: await asyncio.sleep(N)`，直接 `await` 会永久挂住测试进程（实测卡到超时）。做法是 patch `asyncio.sleep` 成「第一次立即返回、第二次抛 `CancelledError`」，跑完一轮循环体后正常退出。

2. **后台任务的完成时间要按数据量算**
   `_broadcast_bot` 是 `create_task`，每发一个用户 `sleep(0.05)`。测试库里 8 个用户 → 至少 0.4 秒，原来只等 0.3 秒，`bot_ok` 还没置位就断言了。改成按用户数估时。

```
test_bot_full.py          → ✅ 7207 项
verify_issues.py          → ✅ 43 项
test_extra.py             → ✅ 71 项
test_coverage_boost.py    → ✅ 75 项
ruff check                → All checks passed
coverage                  → 76%
```

---

## v2.6.4 · 2026-10-20 · 结构重构（数据层与回调分发拆分）

纯重构，零行为变更。三个测试套件全绿，其中 7207 项业务断言一项未改即通过——这是「重构没改行为」的证明。

### 数据层：2639 行 God Class → 12 个领域 mixin

`examples/models.py` 原本是单个 `Database` 类，102 个方法、2639 行，所有 SQL 全堆在一起。

现在拆成 `examples/db/` 包：

| mixin | 领域 | 方法数 |
|---|---|---|
| `CoreMixin` | 连接 / 事务 / 建表 / 迁移 | 5 |
| `UserMixin` | 用户 | 5 |
| `ProductMixin` | 商品 | 5 |
| `OrderMixin` | 订单 | 9 |
| `RechargeMixin` | 充值单与链上防重 | 7 |
| `AdminMixin` | 管理员 | 5 |
| `ChannelMixin` | 频道 | 4 |
| `SettingMixin` | 设置项 | 4 |
| `PointMixin` | 积分流水与操作日志 | 5 |
| `SupplyMixin` | 付费供需 | 6 |
| `StockMixin` | 库存池与到货提醒 | 13 |
| `GrabMixin` | 抢单派单 | 34 |

`Database` 变成多重继承门面，自身不再定义任何业务方法（有测试断言这一点）。`examples/models.py` 退化成 12 行兼容层，**所有 `from .models import ...` 调用点零改动**。

分组唯一依据是「表」：同一张表的读写落在同一个 mixin，跨表事务仍在 `core.atomic()` 里完成。显式 import（不用星号），每个 mixin 只导入自己用到的名字。

### 回调分发：1919 行 if 链 → 4 个命名空间 dispatcher

`handlers_callback.py` 原本是一个函数、152 个 `if data.startswith(...)`。

现在按回调命名空间拆成 4 个模块，入口只剩 78 行：

| 模块 | 命名空间 | 行数 |
|---|---|---|
| `cb_user.py` | `m:` `d:` `buy:` `cbuy:` `od:` `urge:` `redeliver:` `rsub:` `rc:` `rcpay:` `rcchk:` | 453 |
| `cb_supply.py` | `sup:` `a:sup*`（供需大厅/发布向导/开关/审核） | 554 |
| `cb_admin.py` | `padd_*` `a:*`（上架向导/商品/订单/用户/设置） | 644 |
| `cb_grab.py` | `gb:` `a:g*`（抢单/接单员/提现/申诉） | 565 |

分发方式是「顺序短路」：`_dispatch_xxx(...) -> bool`，返回 `True` 即命中，后续不再执行。分支逻辑逐字未改，只把裸 `return` 换成 `return True`。

### 顺带把权限校验改成 fail-closed

原实现的管理员校验是 `a:` 分支里的一行，位置在文件中部（原 695 行），而 `a:sup*`（供需管理，原 1173 行）排在它后面——原代码里两者顺序正确，校验有效。

但这个正确性完全依赖「校验那行必须排在所有 `a:` 分支之前」这个隐式约定。拆成多模块后，`cb_supply` 在 `cb_admin` 之前执行，`a:sup*` 就绕过了校验——拆分过程中测试的 12 项越权断言立刻变红，正是这个原因。

改成入口集中做 fail-closed 闸门，把隐式约定变成显式代码：

```python
if data.startswith("a:") and not db.is_admin(uid):
    await send_or_edit(query, "❌ 无权限", kb([[("🏠 主菜单", "m:root")]]))
    return
```

这样以后新增任何 `a:` 前缀分支、调整任何 dispatcher 顺序，都不可能漏检。回归测试的 12 项越权断言（`a:prod` / `a:fin` / `a:sup` …）全部通过。

### 新增 test_extra.py（71 项）

补主自检没走到的分支：

- **UI 降级路径**：HTML 解析失败 → 纯文本重发；`Message is not modified` → 静默忽略
- **供需定价**：`both` = 两价之和 - 优惠、优惠大于总价时仍 >= 1、频道腿不为负、渠道可用性随配置变化
- **地址校验**：TRON 长度/前缀/校验和三类错误、TON 长度/前缀/校验和、未知链拒绝
- **金额解析**：6 位小数、超大金额、非法输入、负数
- **门面完整性**：`Database` 继承各 mixin、自身零业务方法、`models.*` 全部名字仍可导入

### 验证

```
test_bot_full.py   → ✅ 7207 项通过，❌ 0 项（重构前后完全一致）
verify_issues.py   → ✅ 43 项通过，❌ 0 项
test_extra.py      → ✅ 71 项通过，❌ 0 项
ruff check         → All checks passed!
coverage           → 71%（CI 门槛 70%，低于则红）
```

### 规模对比

| 项 | 重构前 | 重构后 |
|---|---|---|
| 最大文件 | `models.py` 2639 行 | `shop_service.py` 1202 行 |
| 最大单函数 | `on_callback` 1919 行 | `_dispatch_admin` 644 行 |
| `Database` 方法数 | 102（集中） | 102（分散在 12 个 mixin，每个 4~34） |
| 函数返回注解覆盖 | 94% | 94%（拆出的新文件全 100%） |

---

## v2.6.3 · 2026-10-20 · 尾数唯一性 + TON 协议对齐 + 补账通道

第二批修复，issue #4 ~ #7。全部为「静默出错」型缺陷：不报错、不告警，钱错了也看不出来。

### 修复

- **[资金安全] 待支付单超过 200 笔时 TRON 尾数重复分配**
  `_alloc_tron_suffix()` 从 `list_pending_recharges()` 取占用集合，而这个方法 `ORDER BY created_at DESC LIMIT 200`。待支付单超过 200 笔后，更早的单不在列表里，尾数被重复分配，两笔不同订单生成完全相同的应付金额（如都分配 `10.001`），付款会记到别人账上。
  修法：新增 `pending_tron_suffixes(base_amount)` 按「同一整数金额」全表扫描（不同整数金额不可能误匹配，可安全复用尾数）；`_alloc_tron_suffix(base_amount, conn)` 接收金额与事务连接。
  另一个并发缺陷：原实现「先分配、后插入」在写锁前互相看不见，两个并发请求会分到同一尾数。现把分配与落库放进同一个 `BEGIN IMMEDIATE` 事务。

- **[充值] TON 解析与 TonCenter v3 实际协议不兼容**
  三个独立错误叠加，导致 TON 充值**从来不会自动入账**：
  1. 收币方参数写成 `destination_wallet`，官方是 `owner_address`（配 `direction=in`）。错参数被静默忽略，返回任意地址的转账。
  2. 响应 `jetton_master` 是 raw 格式（`0:B113A9...`），代码拿 friendly 常量（`EQCxE6mU...`）直接比，**永不相等**，所有记录被过滤。
  3. 备注在 `decoded_forward_payload.comment`，代码读顶层 `comment`，永远拿到空串。
  修法：参数改 `owner_address`；新增 `ton_normalize_address()` 把 friendly/raw 统一成 `workchain:HEX64` 再比较；备注按 `decoded_forward_payload.comment` 优先、顶层 `comment` 兜底；额外检查 `transaction_aborted`（失败交易不入账）与 `destination`（双保险核对收款方）。

- **[充值] 超时订单未查账即标失败，已付款订单永久丢失**
  `check_all_pending_recharges()` 对超过 2 小时的单直接 `status = "failed"` 后跳过；`check_recharge_status()` 又只接受 `pending`。服务停机、支付接口故障、队列拥堵期间用户已付款的单，从此没有任何补账途径。
  修法：标 failed 之前先查一次账，查到即入账；`check_recharge_status(..., allow_failed=True)` 允许 failed 单补记；管理员/用户点「查询支付状态」时传 `allow_failed=True`。

- **[资金安全] 供需分发失败退款按当前价格，调价后可超额退款**
  `_refund_channel_leg()` 用 `channel_leg_value(post.channel)` 实时算价。原扣 1 分、管理员把频道价调到 100 后分发失败，会退 100 分（余额 100 → 199）。
  修法：`SupplyPost` 新增 `channel_leg` 字段，发帖时快照实付金额；退款优先取快照，老数据（0）回退旧口径，且 `min(amount, post.cost)` 硬性封顶，退款绝不超过实付总额。配套迁移：老库自动 `ALTER TABLE supply_posts ADD COLUMN channel_leg`。

### 测试

- `verify_issues.py` 从 21 项扩到 **43 项**，覆盖 #1 ~ #7 全部七个缺陷。每项都先断言「修复前的行为确实会出错」，再断言修复后正确——避免出现「测试通过但 bug 仍在」的假绿。
- `test_bot_full.py` 两处 mock 补 `allow_failed` 参数，7207 项全绿。

```
python3 test_bot_full.py   → ✅ 7207 项通过，❌ 0 项
python3 verify_issues.py   → ✅ 43 项通过，❌ 0 项
ruff check                 → All checks passed!
```

---

## v2.6.2 · 2026-10-20 · 资金安全修复 + 工程化

本次为缺陷修复与工程规范补强，不含新功能。三个资金/风控级缺陷来自公开 issue，全部附回归用例。

### 修复

- **[资金安全] 自动解冻与手动解冻交错执行导致佣金重复入账**
  `release_due_commissions()` 一次性读取到期佣金后逐笔处理，在发送 Telegram 通知处 `await` 让出执行权。若管理员此时手动解冻了尚未处理的佣金，自动任务恢复后无条件执行 `UPDATE grab_commissions SET status='released'` 并再次增加 `balance_available`，同一笔佣金被计两次（100 → 300）。
  修法：`set_commission_status()` 增加 `expect` 参数做 CAS，`WHERE id=? AND status=?`，`rowcount == 0` 直接跳过记账。手动解冻同样走 CAS，连点两次也只生效一次。

- **[资金安全] 手动解冻受列表分页限制**
  `release_commission_manual()` 用 `list_grab_commissions(status="frozen", limit=500)` 再线性查找目标，冻结记录超过 500 笔后更早的佣金永远解冻不了。
  修法：新增 `get_grab_commission(cid)` 按主键取单。

- **[充值] TRON 监听缺分页与时间游标**
  TronGrid 查询固定 `limit=50` + `order_by=block_timestamp,asc`，调用处既不传 `min_timestamp` 也不翻页。地址历史匹配转账超过 50 笔后，每轮都反复读最早一页，新充值永远进不来。
  修法：按 `meta.fingerprint` 循环翻页（最多 5 页）；游标持久化到 settings（`tron_scan_cursor_ms` / `ton_scan_cursor_ms`），每轮只扫游标之后，起点取「游标」与「最早待支付单前 10 分钟」更早者并回退 60 秒重叠，兼顾增量与不漏单。TON 侧同步加 `start_utime`。

- **[风控] `run_polling()` 未显式订阅 `chat_member`**
  Bot API 的默认更新集合明确不含 `chat_member`（官方文档：空 `allowed_updates` 表示除 `chat_member` / `message_reaction` / `message_reaction_count` 外的全部）。项目注册了 `ChatMemberHandler.CHAT_MEMBER` 却从未声明订阅，导致退群停权、专属入群链接校验、入群识别这些风控逻辑在新部署上静默失效。
  修法：`run_polling(allowed_updates=ALLOWED_UPDATES)`，显式列出 MESSAGE / CALLBACK_QUERY / CHAT_MEMBER / MY_CHAT_MEMBER。

- **[金额] 充值积分换算精度**
  `int(pay_amount * rate)` 走 float。穷举 0.001~0.999 尾数 × 常见汇率，`0.29 / 0.57 / 0.58` 在 rate=100 时 `int()` 少算 1 积分（`float("0.29") * 100 == 28.999999999999996`）。
  修法：`int(Decimal(str(amount)) * Decimal(str(rate)))`。

- **[金额] 提现汇率银行家舍入**
  `round(points / rate, 2)` 是银行家舍入，2.5 会进成 2，少付 0.5 USDT。
  修法：`Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`。

- **[金额] 限额校验改 Decimal**
  `validate_recharge_amount` 的 min/max 比较改 Decimal，避免边界值因浮点误差漂移。

### 工程化

- **依赖锁版本**：`>=` → `==`（python-telegram-bot 22.8 / python-dotenv 1.2.4 / httpx 0.28.1）。PTB 21→22 有破坏性变更，`>=` 是定时炸弹。
- **新增 CI**（`.github/workflows/ci.yml`）：Python 3.10 / 3.11 / 3.12 三版本矩阵，跑 ruff → 语法编译 → 7200+ 项自检 → 回归测试 → 导入烟雾测试。
- **新增 `pyproject.toml`**：项目元数据 + ruff 规则（E/F/W/I/UP/B，行长 120）。
- **新增 `verify_issues.py`**：本次三个 issue 的回归用例，21 项断言，全离线（临时 SQLite + HTTP stub）。
- **清理 30 处 lint 问题**：未使用导入 26 处、未使用变量 3 处、无占位符 f-string 4 处；顺带删掉 `page_admin_grabbers` 里 4 次无人使用的 `COUNT(*)` 查询。
- **移除硬编码管理员 ID**：`runtime.py` 的 `ADMIN_ID` 默认值从真实 ID 改为 `0`，必须由 `.env` 提供。

### 验证

```
python3 test_bot_full.py   → ✅ 7207 项通过，❌ 0 项
python3 verify_issues.py   → ✅ 21 项通过，❌ 0 项
ruff check main.py examples → All checks passed!
```

---

---

## v2.6.1 · 2026-09-11 —— OK钱包提现 + 专属入群链接 + 界面网格化

- 提现打款 OK 钱包：接单员申请提现只需一行发送「积分 OK钱包ID」（不绑定任何账号），系统按「积分兑U汇率」（默认 100 积分 = 1 U，抢单设置在线可调）实时换算 USDT 落库为打款依据；管理员审核页直接看到「应付 USDT + OK钱包ID」，人工打款后填 txid 一键通过，拒绝自动退回积分
- 专属入群链接：每个接单员一条一次性邀请链接（member_limit=1），本人进群即自动作废并三方提示（本人私聊「您已加入接单群，专属邀请链接已作废」/ 群内欢迎 / 管理员通报）；他人冒用链接进群自动踢出并通报管理员；链接泄漏可一键重发；接单员中心与管理员详情页实时展示「已进群/未进群」状态
- 机器人入群自动识别接单群：被拉进群或升为管理员时，未配置过接单群则自动设群 + 打开派单总开关（零配置）；已配置过则给管理员「一键换群」按钮
- 添加接单员三连提示：欢迎卡（编号/佣金比例/回单时限）+ 专属链接卡 + 接单须知五条
- 界面网格化：管理面板/接单管理/系统设置/支付设置/抢单设置等全部改为 2~3 列网格按钮，告别竖条
- 修复 `grab_points_per_usdt` 未进 `get_settings()` 白名单导致汇率改了不生效的问题
- 自检新增 v2.6.1 板块 16 项断言（汇率换算/OK钱包ID落库/链接持久化与反查/本人进群/冒名识别/自行入群/陌生人通报/一键换群/退群归零闭环），全套 7207 项通过

## v2.6.0 · 2026-09-11 —— 抢单派单 + 匿名防偷老板

- 抢单派单闭环：报单类订单自动派到接单群（匿名卡片：只有项目/需求/佣金/时限，绝无买家信息）→ 接单员群内点「立即抢单」→ 原子抢单（UPDATE...WHERE grab_status='waiting'，并发不双抢）→ 深链跳回机器人私聊回单（文字/图片/文件任意类型，富文本原样进订单）→ 完单佣金自动结算。商品类付款即自动发货走库存池，不进派单流程
- 佣金链路：全局比例 + 商品单独比例（-1 跟随全局 / 0 无佣金单）→ 完单冻结（小时数可配，0=立即到账）→ 后台每分钟扫描自动解冻 → 接单员申请提现（最低额可配、在审单拦截重复申请）→ 管理员一键打款/拒绝（拒绝自动退回余额）
- 时限风控：最晚回单时限（小时可配，从抢中起算）→ 超时前私聊预警（分钟可配）→ 超时自动释放重派 + 记超时 + 冷静期禁抢（分钟可配）→ 超过最大重派次数交还管理员人工回单；主动放弃同样记数 + 冷静期
- 防偷老板：匿名派单不透买家；退群/被踢自动停权（ChatMemberHandler 监听，机器人需为接单群管理员）；群内公示只显示接单员编号 #0001
- 接单员体系：管理员建档发编号、在岗/请假/停权/黑名单四态管理、准时率/完单/超时/放弃业绩卡、申诉通道（支持申诉自动清冷静期）、接单排行榜
- 管理后台新增「接单管理」大分类：接单员列表/详情/状态管理、抢单订单监控、佣金台账（手动解冻）、提现审核、申诉裁定、抢单设置中心（总开关/群ID/佣金比例/冻结小时/回单时限/预警分钟/抢单冷却/惩罚冷静期/最大重派/最低提现/自动解冻/管理员通报，全部在线可调）
- 星耀高级会员表情库（richtext.py）：A/B 两套 200+ 枚定制图标进按钮（icon_custom_emoji_id）与正文（tg-emoji），无权限自动降级普通 emoji，消息必达
- 自检新增抢单板块 40+ 断言（建档/原子抢/冷却/回单/冻结解冻/超时/预警/放弃/提现/申诉/退群停权/15 个页面渲染），全套 7179 项通过

## v2.5.0 · 2026-09-09 —— 冷钱包充值 + 支付安全体系

- 新增 TRON TRC20 / TON 两条链的冷钱包充值：
  - TRON 无 memo：下单分配 0.001~0.999 唯一金额尾数（如 10.437），按「收款地址 + 金额精确匹配」识别订单；尾数池 999 个，占满拒单绝不复用；2 小时未支付自动过期释放尾数
  - TON 有 memo：memo = 充值单号，按「memo + 金额」双条件匹配
- 后台 45 秒轮询 TronGrid / TonCenter 自动入账（`chain_watch.py`，只读 API，不碰私钥）
- 幂等防重放：`chain_txids` 表以 txid 为主键，入账 `INSERT OR IGNORE`，重启/重复轮询不重复加分
- 地址保存即校验：TRON Base58Check（T 开头 34 位）、TON friendly CRC16（EQ/UQ 48 位），不合法拒存
- 管理端新增「💳 支付设置」面板：四渠道独立开关、冷钱包地址 / API Key 在线修改、统一单笔限额（默认 1~1000 USDT）；渠道关闭后前台自动隐藏
- 安全修复：OkPay/KKPay 自定义金额此前只校验下限，限额校验下沉到 service 层 `validate_recharge_amount`，所有渠道必经，改包绕不过
- 金额全程 Decimal + 字符串，禁止 float
- 自检从 7031 项扩到 7200+ 项（链上 API 走 mock，零外网依赖；断言数随覆盖分支动态浮动）；新增用例抓到 `save_recharge` upsert 不覆盖 created_at 的测试回填坑，已记入事故馆

## v2.4.0 · 2026-09-09 —— 库存池 + 模块化大重构

- 库存池系统：四种内容格式（文字/图片/文件/图册）、补货向导、图册多图自动合并为一件、原子售卖不超卖、发货失败自动回滚退款、到货提醒、内容重发
- 3023 行单体 `main.py` 拆成 14 个模块（handlers/pages/service/models 单向依赖）
- 新增商品分类/搜索、操作日志、经营日报（默认 08:05，时刻可在系统设置调整）、自动备份（04:30，保留最近 7 份）、广播限流重试

## v2.3.0 · 2026-09-08 —— 供需开关面板 + 富文本体系

- 供需四项管理开关 + 人工审核队列
- `richtext.py` 自定义表情图标库与「解析失败自动降级纯文本」安全发送
- 内容双写（纯文本 + text_html）

## v2.2.0 · 2026-09-07 —— 付费供需 + 地基修复

- 供需大厅上线：三渠道分发、匿名、双发优惠、防刷
- 修复全站性隐患：用户昵称带 `& < >` 导致整条 Telegram 消息解析失败 → 全链路 `h()` 转义 + `send_html` 统一出口
- 事故：测试脚本误写正式库 → 自此测试强制 `SHOP_DB_PATH` 环境变量隔离

## v2.1.0 —— 充值双通道

- 充值新增 KKPay 渠道，与 OkPay 同口径轮询到账；未配置的渠道前台自动隐藏
