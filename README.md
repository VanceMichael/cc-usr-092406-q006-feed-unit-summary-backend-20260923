# q006 水产养殖服务

本项目是水产养殖管理后端，维护塘口、养殖批次、投苗、投喂、水质、用药、成本、销售与周期分析数据。业务数据保存在 SQLite 文件中，HTTP 接口由 FastAPI 提供。

## 饲料计量链（产品规格 / 单位换算 / 复核 / 回算）

现场存在“供应商更换包装规格、全名/简称/批号混写、所有数值被当公斤”的问题，计量链按以下规则闭环：

- **原始三要素不可丢**：每条投喂记录保留 `raw_quantity`、`raw_unit`(bag/g/kg)、`package_label`（当时包装原文）。
- **版本化规格**：`feed_products` 登记产品与别名（全名/简称/批号），`feed_product_versions` 按 `[effective_from, effective_to)` 左闭右开登记每袋净重，区间不允许交叠、同生效日唯一；换算时按投喂日期命中版本，袋数 × 该版 `kg_per_bag`，克 ÷1000，公斤保留 **3 位小数**（四舍五入，1 克精度）。
- **待复核隔离**：规格缺失、同名/别名冲突、单位非法等一律进入复核队列（`status=review`），**不计入任何汇总**；人工只能裁定“归哪个产品”，系统不接受猜测的换算比例，缺规格时复核保持开放。
- **签署锁定**：复核通过才能签署；签署后计量字段不可改、规格更正不回算签署记录，计量结果保留签署时的规格版本与每袋净重快照。
- **包装更正**：只允许更正版本净重/标注（生效日不可改），随后创建 `spec_change` 分批回算任务，仅重算未签署且落在受影响区间的记录。
- **分批可续跑**：回算任务按 `last_id` 水位推进、每条提交，进程中断/重启可续跑（心跳过期自动接管）；记录指纹(`calc_token`)保证指纹未变不重复换算。
- **并发裁定**：同生效日版本由数据库唯一约束裁决唯一赢家；投喂换算始终按投喂日期命中的已提交版本，新版登记后经回算收敛。
- **可追溯**：周期分析/批次追溯逐笔返回产品、规格版本号、生效日与采用的每袋净重；`feeding-variance` 差额报告逐行回到原记录（旧口径 vs 计量链口径）。

主要接口：`/api/feed-products/`（产品/别名/版本/更正/同名裁定）、`/api/feeding-records/`（含 `sign-off`）、`/api/feeding-reviews/`、`/api/recalc/jobs/`、`/api/analysis/cycle/{batch_id}/`、`/api/analysis/traceability/{batch_id}/`、`/api/analysis/feeding-variance/`。

旧版 SQLite 库启动时自动无损补列（ADD COLUMN），不改动历史数值；旧 `feed_quantity`(公斤) 行回算时原样认定为公斤。

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
