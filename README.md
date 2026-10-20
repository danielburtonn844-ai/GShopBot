<div align="center">

# G Shop Bot

**Telegram 自助报单 + 积分商城 + 抢单派单机器人**

版本 `v2.6.1` · Python 3.10+ · SQLite · python-telegram-bot 21+

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

### 4. 跑自检（推荐）

```bash
venv/bin/python3 test_bot_full.py
```

看到 `✅ 通过 7200+ 项   ❌ 失败 0 项` 即可用。自检强制走 `SHOP_DB_PATH` 隔离，不会碰正式库。

### 5. 启动

```bash
venv/bin/python3 main.py
```

首次启动自动建表，生成全新空库。看到启动日志即成功。

> ⚠️ 一个 Bot Token 只能跑一个轮询实例，多开会导致 Telegram 报 `Conflict`。

### 6. 部署为服务

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

单向依赖，无循环引用。

```
main.py                     唯一入口（装配 + 接单群成员监听），不要运行其他任何 .py
examples/
├── runtime.py              运行时单例装配间（db / service / stock / supply / grab / VERSION）
├── ui.py                   键盘构造、HTML 转义、安全发送、输入状态管理
├── models.py               数据层：所有 SQL 与自动迁移（SQLite WAL + 原子事务）
├── richtext.py             富文本 + 星耀高级会员表情库（A/B 两套 200+ 枚）
├── handlers_command.py     /start /admin /grabber、抢单深链落地页、全局错误兜底
├── handlers_callback.py    所有按钮回调的唯一入口（含群内「立即抢单」按钮）
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
```

---

## 技术要点

| 项 | 说明 |
|---|---|
| **金额精度** | 全程 `Decimal` + 字符串，禁用 float |
| **资金安全** | 所有余额变动在事务内；`balance_frozen` 与 `merchant_income(status='frozen')` 恒等 |
| **并发抢单** | `UPDATE ... WHERE grab_status='waiting'` 条件更新，天然原子，无锁 |
| **链上幂等** | `chain_txids` 表以 txid 为主键，入账 `INSERT OR IGNORE`，重启或重复轮询不重复加分 |
| **地址校验** | TRON Base58Check（T 开头 34 位）、TON friendly CRC16（EQ/UQ 48 位），不合法拒存 |
| **限额下沉** | 限额校验在 service 层 `validate_recharge_amount`，所有渠道必经，改包绕不过 |
| **尾数识别** | TRON 无 memo，用 `0.001~0.999` 唯一金额尾数匹配，尾数池 999 个，占满拒单不复用 |
| **防偷老板** | 匿名派单卡片不含买家任何信息；群内公示只显示接单员编号 |

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
