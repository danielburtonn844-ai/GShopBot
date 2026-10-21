<div align="center">

# G Shop Bot

**Telegram 自助报单 + 积分商城 + 抢单派单机器人**

版本 `v2.6.6` · Python 3.10+ · SQLite · python-telegram-bot 22.8

[![CI](https://github.com/danielburtonn844-ai/GShopBot/actions/workflows/ci.yml/badge.svg)](https://github.com/danielburtonn844-ai/GShopBot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)

<br>

作者 **[ @fenghaogou ](https://t.me/fenghaogou)** &nbsp;·&nbsp; 频道 **[ @TvT_GG ](https://t.me/TvT_GG)**

</div>

---

## 这是什么

一个跑在 Telegram 上的完整自助售货 + 派单系统。买家下单，系统把需求匿名派到接单群，接单员抢单回单赚佣金，管理员只管审核提现。

支持 **USDT 四通道充值**（OkPay / KKPay / TRON TRC20 / TON 冷钱包）、**库存池一件一卖**、**匿名派单防偷老板**、**佣金冻结与提现风控**。

---

## 功能总览

### 买家端

- 商品购买：分类、搜索、自动发货、库存/限购/售罄处理
- 库存池一件一卖：文字 / 图片 / 文件 / 图册四种格式，原子售卖不超卖，发货失败自动回滚退款
- 报单类订单：下单后自动进派单流程，不占库存
- USDT 充值四通道
  - OkPay / KKPay 支付平台轮询到账
  - TRON TRC20 冷钱包：无 memo，用 `0.001~0.999` 唯一金额尾数识别订单
  - TON 冷钱包：`memo = 充值单号`，45 秒链上轮询自动入账
- 我的订单：状态筛选、详情、催单、回单内容查看
- 余额 / 充值记录 / 个人中心 / 帮助 / 供需大厅
- 强制关注频道（未关注无法使用）、维护模式

### 接单员端

- 群内抢单：匿名派单卡片一键抢，`UPDATE ... WHERE grab_status='waiting'` 原子操作，并发不双抢
- 深链回单：抢中自动推回单卡，文字/图片/文件任意类型直接发，富文本原样进订单
- 我的钱包：可提余额 / 冻结中 / 累计佣金，全程「积分 ≈ U」双口径
- 申请提现：一行发送 `积分 OK钱包ID`，不绑定任何账号，按汇率自动换算 USDT
- 专属入群链接：一次性邀请链接（`member_limit=1`），本人进群即作废，他人冒用自动踢出
- 进行中 / 完单记录 / 接单排行 / 我的申诉
- 退群自动停权，回群后联系管理员恢复

### 管理端

- **接单管理**：接单员建档发编号、四态管理（在岗/请假/停权/黑名单）、业绩卡、入群状态、一键重发链接
- **抢单监控**：待抢/进行中/已完单筛选，超时手动释放重派
- **佣金台账**：冻结中 / 已解冻明细，手动解冻
- **提现审核**：直接看到「应付 USDT + OK钱包ID」，一键打款记 txid / 拒绝自动退回
- **申诉裁定**：支持（自动清冷静期）/ 驳回
- **抢单设置**：总开关、接单群 ID、佣金比例、冻结小时、回单时限、预警分钟、抢单冷却、冷静期、最大重派、最低提现、积分兑 U 汇率、自动解冻、管理员通报 —— 全部在线改立即生效
- 商品管理 / 库存池管理 / 订单管理 / 用户管理 / 充值审核
- 💳 支付设置：四渠道独立开关、冷钱包地址与 API Key 在线修改、统一单笔限额
- 管理员管理、频道管理、系统设置、广播、财务概览、操作日志、数据备份

### 自动化任务

| 任务 | 频率 |
|---|---|
| 支付平台轮询（OkPay / KKPay） | 60 秒 |
| 链上轮询（TRON / TON） | 45 秒 |
| 抢单时效三件套（超时释放重派 / 超时预警 / 佣金解冻） | 60 秒 |
| 经营日报 | 08:05（可调 0~23 点） |
| 自动备份（保留最近 7 份） | 04:30 |

---

## 快速开始

### 1. 环境要求

```
Python 3.10+
SQLite 3
```

### 2. 安装

```bash
git clone <your-repo-url>
cd report-bot
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 3. 配置

```bash
cp .env.example .env
vim .env
```

| 变量 | 说明 |
|---|---|
| `BOT_TOKEN` | Telegram Bot Token，从 [@BotFather](https://t.me/BotFather) 获取 |
| `ADMIN_ID` | 你的 Telegram 数字 ID，给 [@userinfobot](https://t.me/userinfobot) 发一条消息可查 |
| `OKPAY_ID` / `OKPAY_TOKEN` | OkPay 商户号与密钥，不用可留空 |
| `KKPAY_ID` / `KKPAY_TOKEN` | KKPay 商户号与密钥，不用可留空 |
| `SHOP_DB_PATH` | 可选，自定义数据库路径，默认 `data/shop.db` |

冷钱包地址、链 API Key、渠道开关、单笔限额 **不在 `.env`**，在管理面板「💳 支付设置」里在线配置，存数据库 `settings` 表。

### 4. 跑测试

```bash
python3 test_bot_full.py           # 业务全链路自检，7207 项
python3 verify_issues.py           # 缺陷回归，47 项
python3 test_extra.py              # 分支补强，85 项
python3 test_coverage_boost.py     # 深度覆盖，75 项
```

全部离线运行（链上 API 走 mock，支付接口走 stub），强制走 `SHOP_DB_PATH` 隔离，不会碰正式库，也不需要联网。

覆盖率（当前 76%）：

```bash
pip install coverage
coverage run --source=examples,main test_bot_full.py
coverage run -a --source=examples,main test_extra.py
coverage run -a --source=examples,main test_coverage_boost.py
coverage report --skip-covered
```

代码风格检查：

```bash
pip install ruff
ruff check main.py examples verify_issues.py test_extra.py test_coverage_boost.py
```

### 5. 启动

```bash
venv/bin/python3 main.py
```

首次启动自动建表，生成全新空库。看到启动日志即成功。

> ⚠️ 一个 Bot Token 只能跑一个轮询实例，多开会导致 Telegram 报 `Conflict`。

### 6. 发版后预热预览图（重要）

GitHub 的 Release 页面用 `opengraph.githubassets.com` 生成卡片图，**懒生成**：第一次请求才渲染。分享得太早会抓到 404，Telegram 就会把这个链接缓存成「无图」，之后一直不显示缩略图。

发完 Release 立刻跑一次预热：

```bash
python3 scripts/warm_og.py --tag v2.6.7
```

看到 `✓ 可以分享了` 再发链接。已经发出去且没图的，把链接发给 Telegram 官方的 **@WebpageBot** 可强制刷新缓存。

### 7. 部署为服务

```ini
# /etc/systemd/system/gshopbot.service
[Unit]
Description=G Shop Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/report-bot
ExecStart=/path/to/report-bot/venv/bin/python3 /path/to/report-bot/main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now gshopbot
```

---

## 抢单派单模块开通

1. **建接单群**：新建一个私密群，把机器人拉进去并设为 **管理员**（必须是管理员，否则收不到群成员变动，退群停权 / 专属链接 / 进群识别全部失效）
2. **自动识别**：机器人入群即自动识别为接单群并打开派单总开关。若已配置过别的群，管理员会收到「一键换群」按钮
3. **手动填群 ID**（自动识别失败时）：在群里发一条消息，浏览器访问 `https://api.telegram.org/bot<你的TOKEN>/getUpdates`，找 `"chat":{"id":-100xxxxxxxxxx}`
4. **调设置**：管理面板 → 🥷 接单管理 → ⚙️ 抢单设置
5. **加接单员**：管理面板 → 🥷 接单管理 → 接单员管理，输入对方 TG 数字 ID，系统自动发编号 `#0001` 起，并推三连提示（欢迎卡 + 专属链接卡 + 接单须知）

此后全自动：买家报单 → 匿名卡片派到接单群 → 接单员抢单 → 私聊回单 → 买家收到 → 佣金冻结到期自动进可提余额。

---

## 项目结构

单向依赖，无循环引用。数据层与回调分发已按领域拆分，单文件最大 1200 行。

```
main.py                     唯一入口（装配 + 接单群成员监听），不要运行其他任何 .py
examples/
├── db/                     数据层（v2.6.4 从 2639 行 God Class 拆出）
│   ├── base.py             常量与工具函数（时区 / 单号 / 格式化 / 状态字典）
│   ├── schema.py           全部 dataclass（纯数据，无业务逻辑）
│   ├── __init__.py         Database 门面 = 12 个 mixin 组合
│   └── mixins/
│       ├── core.py         连接 / 事务 / 建表 / 迁移
│       ├── user.py         用户
│       ├── product.py      商品
│       ├── order.py        订单
│       ├── recharge.py     充值单与链上防重
│       ├── admin.py        管理员
│       ├── channel.py      频道
│       ├── setting.py      设置项
│       ├── point.py        积分流水与操作日志
│       ├── supply.py       付费供需
│       ├── stock.py        库存池与到货提醒
│       └── grab.py         抢单派单（接单员/佣金/提现/申诉）
├── models.py               兼容层：转导出 db 包，所有 `from .models import ...` 照旧
├── handlers_callback.py    回调总入口（78 行：建档 → 清状态 → 权限闸门 → 分发）
├── cb_user.py              用户端：主菜单 / 商品 / 购买 / 订单 / 充值
├── cb_supply.py            付费供需：大厅 / 发布向导 / 管理端开关与审核
├── cb_admin.py             管理端：上架向导 / 商品 / 订单 / 用户 / 设置
├── cb_grab.py              抢单派单：群内抢单 / 接单员 / 提现 / 申诉
├── runtime.py              运行时单例装配间（db / service / stock / supply / grab / VERSION）
├── ui.py                   键盘构造、HTML 转义、安全发送、输入状态管理
├── richtext.py             富文本 + 星耀高级会员表情库（A/B 两套 200+ 枚）
├── handlers_command.py     /start /admin /grabber、抢单深链落地页、全局错误兜底
├── handlers_message.py     输入状态机 + 接单员回单 + 管理员引用回单 + 补货向导
├── pages_user.py           用户端页面
├── pages_supply.py         供需页面
├── pages_admin.py          管理端页面
├── pages_stock.py          库存池页面
├── pages_grabber.py        接单员页面
├── shop_service.py         商城核心（购买 / 退款 / 充值 / 冷钱包 / 广播）
├── grab_service.py         抢单派单核心（匿名派单 / 原子抢单 / 佣金结算 / 提现 / 风控）
├── stock_service.py        库存池（补货 / 原子售卖 / 失败回滚 / 到货提醒）
├── supply_service.py       供需大厅
├── chain_watch.py          TRON / TON 链上只读 API + 地址校验
├── okpay.py / kkpay.py     支付渠道
└── jobs.py                 后台任务（支付轮询 / 链上轮询 / 抢单时效 / 日报 / 备份）
data/
└── shop.db                 数据库（自动生成，已 gitignore）

test_bot_full.py            自检脚本（7207 断言，全离线）
verify_issues.py            缺陷回归（47 断言：佣金竞态 / 链上分页 / 更新订阅 / 金额精度 / 尾数唯一性 / TON 协议 / 补账 / 退款快照 / 普通充值精度）
test_extra.py               分支补强（85 断言：UI 降级 / 欢迎图媒体切换 / 供需定价 / 地址校验 / 门面完整性）
test_coverage_boost.py      深度覆盖（75 断言：供需全流程 / 抢单回调 / 后台任务 / 链上异常 / 状态机）
.github/workflows/ci.yml    CI：ruff + 语法 + 四套测试 + 覆盖率门槛 75%（Python 3.10/3.11/3.12）
pyproject.toml              项目元数据 + ruff 规则
```

---

## 技术要点

| 项 | 说明 |
|---|---|
| **金额精度** | 充值积分换算、提现汇率、限额校验全程 `Decimal`；提现用 `ROUND_HALF_UP` 而非 Python 默认的银行家舍入 |
| **资金安全** | 所有余额变动在事务内；佣金解冻走 CAS（`WHERE status='frozen'`），自动与手动解冻交错时不会重复入账 |
| **并发抢单** | `UPDATE ... WHERE grab_status='waiting'` 条件更新，天然原子，无锁 |
| **链上幂等** | `chain_txids` 表以 txid 为主键，入账 `INSERT OR IGNORE`，重启或重复轮询不重复加分 |
| **链上增量扫描** | 游标持久化在 settings（`tron_scan_cursor_ms` / `ton_scan_cursor_ms`），每轮只扫游标之后；TronGrid 按 `meta.fingerprint` 翻页，历史超过单页容量也不会漏新单 |
| **更新订阅** | `run_polling(allowed_updates=...)` 显式订阅 `chat_member`——Bot API 默认集合不含它，不显式声明则退群停权/邀请链接校验全部静默失效 |
| **地址校验** | TRON Base58Check（T 开头 34 位）、TON friendly CRC16（EQ/UQ 48 位），不合法拒存 |
| **限额下沉** | 限额校验在 service 层 `validate_recharge_amount`，所有渠道必经，改包绕不过 |
| **尾数识别** | TRON 无 memo，用 `0.001~0.999` 唯一金额尾数匹配，尾数池 999 个，占满拒单不复用 |
| **防偷老板** | 匿名派单卡片不含买家任何信息；群内公示只显示接单员编号 |
| **媒体感知更新** | `send_or_edit` 按「当前消息承载什么媒体」决定走 `edit_caption` / `edit_media` / `edit_text` / 删旧发新，主界面欢迎图在来回切换中不会丢 |
| **强制关注缓存** | 频道检查结果按 `(user_id, 频道集合)` 缓存 60 秒，避免每点一次按钮就打 N 次 Telegram API |

### 已修复的历史缺陷

| 缺陷 | 影响 | 修法 |
|---|---|---|
| 自动解冻与手动解冻交错执行 | 同一笔佣金被两次计入可提现余额（100 → 300） | `set_commission_status(..., expect="frozen")` 做 CAS，未命中则跳过记账 |
| 手动解冻只查前 500 条冻结记录 | 超过 500 笔后更早的佣金无法解冻 | 新增 `get_grab_commission(cid)` 按主键取单 |
| TRON 监听无分页、无时间游标 | 历史转账超过 50 笔后，新充值永远进不来 | `meta.fingerprint` 翻页 + 持久化扫描游标 + 60 秒重叠窗口 |
| `run_polling()` 未指定 `allowed_updates` | `chat_member` 默认不投递，退群停权/专属链接风控静默失效 | 显式订阅 MESSAGE / CALLBACK_QUERY / CHAT_MEMBER / MY_CHAT_MEMBER |
| 充值积分 `int(float(x) * rate)` | 尾数 0.29 / 0.57 / 0.58 时少算 1 积分 | 改 `int(Decimal(x) * Decimal(rate))` |
| 提现 `round()` 银行家舍入 | 2.5 → 2，少付 0.5U | `Decimal.quantize(..., ROUND_HALF_UP)` |
| 尾数分配依赖 `LIMIT 200` 列表 | 待支付单超 200 笔后尾数重复，付款记到别人账上 | 按整数金额全表扫描 + 分配与落库同事务 |
| TON 解析与 TonCenter v3 不兼容 | `destination_wallet` 参数无效、friendly/raw 地址比不相等、备注读错字段 → TON 充值从不入账 | 改 `owner_address`；`ton_normalize_address()` 统一格式；读 `decoded_forward_payload.comment`；检查 `transaction_aborted` |
| 超时单直接标 failed | 停机/接口故障期间已付款订单永久丢失 | 标 failed 前先查账；`allow_failed=True` 保留补记通道 |
| 供需退款按当前价格 | 调价后分发失败超额退款（扣 1 退 100） | 发帖时快照 `channel_leg`，退款按快照且封顶实付总额 |
| 普通充值入口 `int(amount * rate)` | 0.29 / 0.57 / 0.58 少算 1 积分（v2.6.2 只修了冷钱包入口） | 改 `Decimal` 计算 |
| 充值确认页同算法 | 页面写「获得 100」实际入账 99 | 与写入侧共用 Decimal |
| 提现审核「通过」无事务 | 与「拒绝」并发时用户既拿到打款又拿回积分 | `atomic()` + `set_withdrawal_status(expect="pending")` CAS |
| 管理员回单无事务 | 双击产生两条回单通知 | 放入 `atomic()` |
| 自动备份开关无效 | 关掉后仍每天 04:30 备份 | 循环内读 `daily_backup_enabled` |
| 广播 `limit=10000` | 超万用户静默漏人 | `iter_users(batch=500)` 分批遍历 |
| 按 username 找人查前 500 | 新用户永远找不到 | 走 SQL `get_user_by_username` |
| 订单页总数用 `len(orders)` | 超 200 单显示「共 200 条」 | 改 `count_orders()` |

以上全部有回归用例，`python3 verify_issues.py` 可复现验证（47 项断言）。

---

## 注意事项

1. **群聊功能需关闭 Group Privacy**
   `@BotFather` → `/mybots` → 你的 bot → Bot Settings → Group Privacy → **Turn off**
   否则机器人收不到群内普通消息，抢单按钮和群命令都失效。

2. **高级会员表情**需机器人 owner 账号有 Telegram Premium，否则自动降级为普通 emoji，消息仍能正常发送。

3. **数据备份**：SQLite 为 WAL 模式，用 `sqlite3 .backup` 而非直接 `cp`。

4. **首次部署**需在管理面板「💳 支付设置」填入你自己的 TRON / TON 收款地址并打开对应渠道开关。链 API Key 不填则走公共免费额度，量大时申请自有 Key。

---

## 许可

[MIT License](LICENSE) — 可自由使用、修改、分发，保留版权声明即可。

---

<div align="center">

**作者 [ @fenghaogou ](https://t.me/fenghaogou)** &nbsp;·&nbsp; **频道 [ @TvT_GG ](https://t.me/TvT_GG)**

</div>
