# CHANGELOG — G Shop Bot

## v2.6.7 · 2026-10-22

### 新增

- `bench_concurrency.py`：多线程并发压测，覆盖抢单原子性、佣金解冻、尾数分配、提现审核、读写吞吐五项。与单元测试的区别在于使用多线程加独立连接访问同一个库，形成真实写锁竞争。

### 性能

- **数据库连接复用**。原 `_get_conn()` 每次调用执行 `sqlite3.connect()` + 2 条 PRAGMA + `close()`，实测单次 3.63 ms，其中 PRAGMA 占 76%。`journal_mode=WAL` 属于数据库级持久设置，写入文件头后对所有连接生效，逐次重设无实际作用。现改为每线程复用一条持久连接（`threading.local`），初始化只在首次连接时执行；`close()` 由 `_PooledConn` 代理改为归还语义，60 余处调用点无需修改。

  | 指标 | 调整前 | 调整后 |
  |---|---|---|
  | 1000 次读写（20 线程） | 14 次/秒 | 249 次/秒 |
  | 100 线程抢同一单 | 6820 ms | 208 ms |

### 修复

- **`close()` 回滚未提交事务导致外层事务失效**。`atomic()` 事务块内的读操作（如 `get_setting()` 自行取连接后 `close()`）会命中同一条池化连接，将外层尚未提交的事务一并回滚，现象为回单返回成功但订单状态未落库。`close()` 现仅标记归还，事务由 `atomic()` 与 `_maybe_conn()` 显式 commit / rollback。
- **WAL 下读事务升级写事务立即返回 `database is locked`**。该错误绕过 `busy_timeout` 等待，压测中 1000 次读写出现 5 次。`execute()` 增加 3 次退避重试（20 / 40 ms），`busy_timeout` 由 5 秒调整为 10 秒。

### 索引

充值表原先无索引，链上轮询与尾数分配均走全表扫描。新增：

```sql
CREATE INDEX idx_recharge_status_chain ON recharges(status, chain);
CREATE INDEX idx_recharge_user ON recharges(user_id, created_at);
```

### 压测结果

| 场景 | 断言 | 结果 |
|---|---|---|
| 100 线程抢同一单 | 仅 1 人成功 | 通过，库中 `grabber_id` 与胜者一致 |
| 自动解冻 × 4 + 手动解冻 × 50 并发 | 余额等于已解冻总额 | 通过，1000 = 1000 |
| 200 线程并发建 TRON 充值单（同金额） | 尾数互不重复 | 通过，200 笔 200 个尾数 |
| 提现「通过」与「拒绝」20 轮并发 | 无双生效 | 通过，0 轮双生效 |
| 1000 次读写（20 线程） | 吞吐达标、无锁错误 | 通过，249 次/秒，0 次 locked |

### 验证

```
test_bot_full.py        7207 项
verify_issues.py          47 项
test_extra.py             85 项
test_coverage_boost.py    75 项
bench_concurrency.py      10 项
ruff check              All checks passed
```

---

## v2.6.6 · 2026-10-21

### 新增

- **主界面欢迎图常驻**。`send_or_edit` 改为媒体感知更新，按当前消息承载的媒体决定处理方式：

  | 当前消息 | 目标 | 处理 |
  |---|---|---|
  | 图文 | 同一张图 | `edit_message_caption` 就地改图注 |
  | 图文 | 另一张图 | `edit_message_media` 原地换图 |
  | 图文 | 无图 | 删除旧消息后重发 |
  | 文字 | 有图 | 删除旧消息后重发 |
  | 文字 | 无图 | `edit_message_text` |

  原实现仅有「有图发新消息 / 无图改文字」两条路径，而 Telegram 的 `editMessageText` 不保留图片，`editMessageCaption` 无法在纯文字消息上创建图片，因此从子页面返回主界面后图片无法恢复。

- `pages_user.home_photo()`：统一读取欢迎图 `file_id`，`m:root`、`m:checkjoin` 与未知回调兜底三处回主界面均带入。
- 文案超过 1024 字符（caption 上限）时自动放弃带图，退回纯文字，避免编辑失败导致页面无法打开。

### 修复

- **普通充值入口积分精度**。`points = int(amount * rate)` 使用 float 计算，`0.29 USDT @ 100 积分/U` 得 28 分（应为 29）、`0.57` 得 56、`0.58` 得 57。v2.6.2 仅修复冷钱包入口，主入口遗漏。改为 `int(Decimal(str(amount)) * Decimal(str(rate)))`。
- **充值确认页与实际入账不一致**。`page_recharge_confirm` 使用相同的 float 算法，页面显示「获得 100 积分」而实际入账 99，改为与 `create_recharge` 共用 Decimal 算法。
- **提现审核「通过」缺少事务与 CAS**。`approve_withdrawal` 的读取、判断、写入三步不在事务内。当「通过」与「拒绝」并发执行时，拒绝方在事务内退回余额，通过方随后无条件覆盖为 `paid`，导致同一笔提现既完成打款又退回积分。改为 `atomic()` + `set_withdrawal_status(expect="pending")`。
- **管理员回单未放入事务**。`complete_order` 同样为读改写分离，双击或两名管理员同时操作会各写入一次，用户收到两条回单通知。改为整体置于 `atomic()` 内。
- **自动备份开关无效**。`auto_backup()` 未读取 `daily_backup_enabled`，面板关闭后仍每日 04:30 执行。循环内增加开关判断。
- **广播用户数上限**。商品广播与供需广播使用 `list_users(limit=10000)`，用户数超过一万时后续用户不会收到且无任何提示。新增 `db.iter_users(batch=500)` 分批遍历。
- **按用户名查找用户仅覆盖前 500 条**。补货提醒使用 `list_users(limit=500)` 线性比对，用户数超过 500 后新用户无法被找到。新增 `get_user_by_username()` 走 SQL 精确查询。
- **订单管理页总数显示错误**。`len(orders)` 为 `LIMIT 200` 截断后的长度，订单超过 200 笔时显示「共 200 条」。改用 `count_orders()`。
- 移除未实现的设置项 `order_expire_minutes`。该键仅在 `get_settings()` 中定义默认值，全项目无读取处，界面亦无对应开关。

### 性能

- 强制关注检查增加 60 秒 TTL 缓存，键为 `(user_id, 频道 ID 元组)`，频道配置变化时自动失效。原实现对每个强制频道各调用一次 `get_chat_member`，配置 3 个频道时每次进入主界面产生 3 次 Telegram API 请求。

### 验证

```
test_bot_full.py        7207 项
verify_issues.py          47 项
test_extra.py             85 项（新增 14 项覆盖欢迎图媒体切换路径）
test_coverage_boost.py    75 项
ruff check              All checks passed
```

---

## v2.6.5 · 2026-10-21

### 修复

- **`_toast()` 参数名不匹配导致三处错误提示抛出 TypeError**。定义参数为 `alert`，三处调用传入 `show_alert`，触发 `TypeError: _toast() got an unexpected keyword argument 'show_alert'`。受影响位置：

  | 位置 | 场景 |
  |---|---|
  | `gb:inv` | 专属入群链接生成失败 |
  | `a:gblink` | 管理员重发接单员链接失败 |
  | `a:gblink` | 链接生成成功但私聊发送失败 |

  三处均为错误提示路径，异常被吞后用户仅看到按钮无响应。参数名改为与 Bot API `answerCallbackQuery(show_alert=...)` 一致，并增加 `**_ignored` 兼容历史别名。

  该缺陷未被原有 7207 项自检覆盖，原因是自检中的模拟 Bot 始终成功返回，不会进入失败分支。

### 新增

- `test_coverage_boost.py`：75 项断言，覆盖供需全流程、抢单回调、后台任务、链上异常、输入状态机。

  | 分组 | 覆盖内容 |
  |---|---|
  | 供需全流程 | 发布扣费、空内容、超长、暂停、积分不足、封禁、日限额、频道未配置、审核通过、审核拒绝退款、重复审核幂等、机器人广播、频道失败退腿、删帖退款、统计 |
  | 抢单回调 | `gb:m` `gb:doing` `gb:done` `gb:money` `gb:wd` `gb:wdnew` `gb:inv` `gb:rank` `gb:aps`，管理端 `a:grab` `a:gbers` `a:gbadd` `a:gords` `a:gwds` `a:gaps` `a:gset` |
  | 后台任务 | 5 个常驻协程各执行一轮 |
  | 链上异常 | 网络异常、JSON 解析失败、单条脏数据不影响整批 |
  | 输入状态机 | 无状态消息、群聊消息忽略、搜索状态、未知状态清理 |

### 覆盖率

| 模块 | 调整前 | 调整后 |
|---|---|---|
| `supply_service.py` | 26% | 90% |
| `chain_watch.py` | 37% | 74% |
| `cb_grab.py` | 34% | 48% |
| 总计 | 71% | 76% |

CI 覆盖率门槛由 70% 调整为 75%。

### 说明

常驻协程（`while True: await asyncio.sleep(N)`）无法直接 await，否则测试进程永久阻塞。处理方式为将 `asyncio.sleep` 替换为「第一次立即返回、第二次抛出 `CancelledError`」，执行完一轮循环体后正常退出。

后台任务耗时需按数据量估算。`_broadcast_bot` 为 `create_task`，每个用户之间 `sleep(0.05)`，8 个用户至少需要 0.4 秒，测试等待时间不足时会在 `bot_ok` 置位前断言。

---

## v2.6.4 · 2026-10-20

纯结构重构，无行为变更。7207 项业务断言未经修改即通过。

### 数据层拆分

`examples/models.py` 原为单个 `Database` 类，102 个方法、2639 行。现拆分为 `examples/db/` 包：

| 模块 | 领域 | 方法数 |
|---|---|---|
| `mixins/core.py` | 连接、事务、建表、迁移 | 5 |
| `mixins/user.py` | 用户 | 5 |
| `mixins/product.py` | 商品 | 5 |
| `mixins/order.py` | 订单 | 9 |
| `mixins/recharge.py` | 充值单与链上防重 | 7 |
| `mixins/admin.py` | 管理员 | 5 |
| `mixins/channel.py` | 频道 | 4 |
| `mixins/setting.py` | 设置项 | 4 |
| `mixins/point.py` | 积分流水与操作日志 | 5 |
| `mixins/supply.py` | 付费供需 | 6 |
| `mixins/stock.py` | 库存池与到货提醒 | 13 |
| `mixins/grab.py` | 抢单派单 | 34 |

`Database` 改为多重继承门面，自身不再定义业务方法。`base.py` 存放常量与工具函数，`schema.py` 存放全部 dataclass。分组依据为数据表：同一张表的读写集中在同一个 mixin，跨表事务仍在 `core.atomic()` 内完成。各 mixin 使用显式导入。

`examples/models.py` 保留为 12 行兼容层，全部 `from .models import ...` 调用点无需修改。

### 回调分发拆分

`handlers_callback.py` 原为单一函数，152 个 `if data.startswith(...)` 分支，1919 行。现按回调命名空间拆分为四个模块：

| 模块 | 命名空间 | 行数 |
|---|---|---|
| `cb_user.py` | `m:` `d:` `buy:` `cbuy:` `od:` `urge:` `redeliver:` `rsub:` `rc:` `rcpay:` `rcchk:` | 453 |
| `cb_supply.py` | `sup:` `a:sup*` | 554 |
| `cb_admin.py` | `padd_*` `a:*` | 644 |
| `cb_grab.py` | `gb:` `a:g*` | 565 |

入口 `on_callback` 缩减至 78 行，按 `_dispatch_xxx(...) -> bool` 顺序短路分发，返回 `True` 即命中。分支逻辑逐字保留，仅将裸 `return` 替换为 `return True`。

### 权限校验

原实现对 `a:` 前缀的管理员校验位于文件中部，正确性依赖「该行必须位于所有 `a:` 分支之前」这一隐式约定。拆分后 `cb_supply` 先于 `cb_admin` 执行，`a:sup*` 绕过校验。现改为入口集中判断：

```python
if data.startswith("a:") and not db.is_admin(uid):
    await send_or_edit(query, "❌ 无权限", kb([[("🏠 主菜单", "m:root")]]))
    return
```

### 新增

`test_extra.py`：71 项断言，覆盖 UI 降级路径、供需定价边界、TRON 与 TON 地址校验、金额解析、门面完整性。

### 规模

| 指标 | 重构前 | 重构后 |
|---|---|---|
| 最大文件 | `models.py` 2639 行 | `shop_service.py` 1202 行 |
| 最大函数 | `on_callback` 1919 行 | `_dispatch_admin` 644 行 |

### 验证

```
test_bot_full.py        7207 项（重构前后一致）
verify_issues.py          43 项
test_extra.py             71 项
ruff check              All checks passed
coverage                  71%
```

---

## v2.6.3 · 2026-10-20

### 修复

- **待支付单超过 200 笔时 TRON 尾数重复分配**。`_alloc_tron_suffix()` 的占用集合来自 `list_pending_recharges()`，该方法为 `ORDER BY created_at DESC LIMIT 200`，第 201 笔及之后的订单不在集合内，尾数可被重复分配，两笔订单生成相同的应付金额，付款将记入错误账户。改为新增 `pending_tron_suffixes(base_amount)` 按整数金额全表扫描（不同整数金额不会产生相同应付金额，尾数可安全复用）。另将分配与落库合并至同一个 `BEGIN IMMEDIATE` 事务，原实现先分配后插入，写锁获取前互相不可见，并发时会分配到相同尾数。
- **TON 解析与 TonCenter v3 协议不兼容**。三处独立错误叠加，导致 TON 充值无法自动入账：

  | 问题 | 原实现 | 修正 |
  |---|---|---|
  | 收币方参数 | `destination_wallet` | `owner_address`（配合 `direction=in`） |
  | 地址格式 | `jetton_master` 响应为 raw 格式 `0:B113A994...`，与 friendly 常量 `EQCxE6mU...` 直接比较，恒不相等 | 新增 `ton_normalize_address()` 统一转为 `workchain:HEX64` |
  | 备注字段 | 读取顶层 `comment` | 读取 `decoded_forward_payload.comment` |

  同时增加 `transaction_aborted` 判断（失败交易不入账）与 `destination` 校验。
- **超时订单未查账即标记失败**。`check_all_pending_recharges()` 对超过 2 小时的订单直接置为 `failed` 并跳过查询，`check_recharge_status()` 仅接受 `pending` 状态。服务停机或支付接口故障期间已付款的订单因此无法补记。改为标记失败前先查询一次，并新增 `allow_failed` 参数保留补账通道。
- **供需分发失败退款按当前价格计算**。`_refund_channel_leg()` 调用 `channel_leg_value()` 实时取价，原扣 1 分、管理员将频道价调整为 100 后分发失败会退还 100 分。`SupplyPost` 新增 `channel_leg` 字段保存发帖时的实付金额，退款优先取快照，并以 `min(amount, post.cost)` 封顶。配套迁移：`ALTER TABLE supply_posts ADD COLUMN channel_leg INTEGER DEFAULT 0`。

### 测试

`verify_issues.py` 由 21 项扩展至 43 项，覆盖 #1 至 #7 全部缺陷。每项先断言修复前行为确实出错，再断言修复后正确。

---

## v2.6.2 · 2026-10-20

### 修复

- **佣金解冻竞态导致重复入账**。`release_due_commissions()` 读取到期佣金后逐笔处理，在发送 Telegram 通知处 `await` 让出执行权。若管理员此时手动解冻了尚未处理的佣金，自动任务恢复后无条件执行 `UPDATE` 并再次增加可提现余额，同一笔佣金计入两次（100 → 300）。`set_commission_status()` 增加 `expect` 参数实现 CAS，`rowcount == 0` 时跳过记账。
- **手动解冻受列表分页限制**。`release_commission_manual()` 使用 `list_grab_commissions(status="frozen", limit=500)` 再线性查找，冻结记录超过 500 笔后更早的佣金无法解冻。新增 `get_grab_commission(cid)` 按主键取单。
- **TRON 监听缺少分页与时间游标**。查询固定 `limit=50` 与 `order_by=block_timestamp,asc`，调用处未传 `min_timestamp` 且未翻页。地址历史匹配转账超过 50 笔后，每轮轮询重复读取最早一页，新转账无法进入匹配流程。改为按 `meta.fingerprint` 翻页（最多 5 页），游标持久化至 settings（`tron_scan_cursor_ms` / `ton_scan_cursor_ms`），扫描起点取「游标」与「最早待支付单前 10 分钟」中较早者并回退 60 秒。TON 侧同步增加 `start_utime`。
- **`run_polling()` 未显式订阅 `chat_member`**。Bot API 的默认更新集合不包含 `chat_member`，项目虽注册 `ChatMemberHandler.CHAT_MEMBER` 但未声明订阅，导致退群停权、专属入群链接校验、入群识别在新部署上无效。改为 `run_polling(allowed_updates=ALLOWED_UPDATES)`，显式列出 `MESSAGE`、`CALLBACK_QUERY`、`CHAT_MEMBER`、`MY_CHAT_MEMBER`。
- **充值积分换算精度**。`int(pay_amount * rate)` 使用 float，`0.29`、`0.57`、`0.58` 在 rate=100 时少算 1 积分（`float("0.29") * 100 == 28.999999999999996`）。改为 `int(Decimal(str(amount)) * Decimal(str(rate)))`。
- **提现汇率银行家舍入**。`round(points / rate, 2)` 采用银行家舍入，2.5 进位为 2，少付 0.5 USDT。改为 `Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`。
- **限额校验改用 Decimal**。`validate_recharge_amount` 的最小值与最大值比较改为 Decimal，避免浮点误差导致边界判断偏移。

### 工程化

- 依赖锁定版本：`python-telegram-bot==22.8`、`python-dotenv==1.2.4`、`httpx==0.28.1`。
- 新增 CI（`.github/workflows/ci.yml`）：Python 3.10 / 3.11 / 3.12 矩阵，依次执行 ruff、语法编译、7207 项自检、回归测试、导入烟雾测试。
- 新增 `pyproject.toml`：项目元数据与 ruff 规则（E / F / W / I / UP / B，行宽 120）。
- 新增 `verify_issues.py`：21 项断言，使用临时 SQLite 与 HTTP stub，全程离线。
- 清理 30 处 lint 问题。
- 移除硬编码管理员 ID，改为必须由 `.env` 提供。

### 验证

```
test_bot_full.py        7207 项
verify_issues.py          21 项
ruff check              All checks passed
```

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
