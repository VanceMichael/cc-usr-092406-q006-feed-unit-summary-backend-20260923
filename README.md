# q006 水产养殖服务

本项目是水产养殖管理后端，维护塘口、养殖批次、投苗、投喂、水质、用药、成本、销售与周期分析数据。业务数据保存在 SQLite 文件中，HTTP 接口由 FastAPI 提供。

## 投喂计量链（原始数量 → 生效规格版本 → 公斤）

现场可能以**袋、克、公斤**三种单位登记投喂，并把产品全名、简称、生产批号混写在
"饲料类型"字段；供应商还会更换包装规格。服务补齐了从投喂记录到周期分析的完整计量链：

- **原始留痕**：每条投喂记录永久保留原始数量 `quantity`、原始单位 `unit`、
  当时包装 `package_kg`（袋计量）与拆出的生产批号 `package_batch_no`。
- **产品与规格版本**：`feed_products` 维护产品全名/简称/别名；包装规格以
  **生效版本**（`effective_from`/`effective_to` 半开区间）管理。供应商换包装
  即新增版本，旧版本自动截止；有限更正区间结束后自动追加延续版本，保证生效期连续。
- **确定性裁定**：按投喂日期选取当日生效版本（同日重叠取版本号更大者），
  不做模糊猜测。匹配到多个产品（同名冲突）或当日无生效规格时，记录进入
  **待复核(review)**，公斤数留空，绝不靠猜测比例对平数字。
- **精度**：所有换算经 `Decimal` 计算，公斤按 `ROUND_HALF_UP` 保留三位小数
  （精确到克）；克 ÷1000、袋 ×每袋公斤数。
- **签署隔离**：周期可签署；已签署记录的计量链冻结，规格更正只重算
  **未签署且换算指纹真正变化**的记录（指纹含产品、规格版本、原始计量与当时包装）。
- **分批回填**：旧库记录登记为 `pending`（原数值按原始公斤保留），经
  `/api/reconciliation/backfill/` 分批换算，可中断、可凭批次 id 重启续跑；
  只拾取 pending 记录，防止重复换算。
- **并发版本取舍**：规格裁定（新增版本/合并）与新增投喂以投喂日期落在哪个
  生效期为准；版本插入与受影响记录重算在同一事务内完成。
- **盘点对账**：`/api/reconciliation/variance/` 对比饲料仓按袋核出量与分析
  公斤数，差额与每一笔明细都可回到原记录；pending/review 记录隔离列出、不进合计。
- **可追溯**：周期分析与追溯响应对每笔投喂逐笔说明采用的产品、规格版本号、
  生效期、采用的袋规格与换算后的公斤数。

### 主要接口

| 方法 & 路径 | 说明 |
| --- | --- |
| `POST /api/feed-products/` | 建产品（可同时给首版袋规格与生效日） |
| `POST /api/feed-products/{id}/versions/` | 新增生效规格版本（自动衔接旧版本） |
| `POST /api/feed-products/correct-package/` | 包装更正（新版本+只重算未签署受影响记录，同事务） |
| `POST /api/feed-products/merge/` | 同名产品裁定合并 |
| `POST /api/feeding-records/` | 登记投喂（原始数量/单位/当时包装），即时换算或进复核 |
| `POST /api/feeding-records/sign/` | 签署（仅 converted 可签，签署后冻结） |
| `POST /api/feeding-records/{id}/resolve-review/` | 人工复核：显式裁定产品/修正原始计量后重算 |
| `GET  /api/reconciliation/review/` | 待复核记录清单（隔离） |
| `GET  /api/reconciliation/variance/` | 仓库出库量 vs 分析公斤数差额，逐笔回原记录 |
| `POST /api/reconciliation/backfill/` | 旧记录分批换算（limit/interrupt，可带批次 id 续跑） |
| `GET  /api/analysis/cycle/{batch_id}/` | 周期分析（仅 converted 公斤，按产品×版本汇总） |
| `GET  /api/analysis/traceability/{batch_id}/` | 逐笔追溯（原始计量+规格版本+换算公斤） |

启动时对既有 SQLite 做幂等迁移：补齐计量链列；旧表 `feed_quantity NOT NULL`
会通过重建表解除（待复核记录需置空公斤数），历史数值登记为原始公斤、待换算。

## 测试命令

```bash
python3 -m unittest discover -s tests -v
```

## 编译与构建命令

```bash
python3 -m compileall -q backend/app
```

## 启动命令

```bash
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动后可访问 `/health` 检查服务状态。开发环境不得提交真实账号、连接凭据或生产数据。
