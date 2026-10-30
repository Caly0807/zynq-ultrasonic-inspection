# 手动面扫与双端显示数据契约

更新：2026-10-27。适用于 `utinspect/mapping.py`、`mapping_app.py` 与 LVGL `ut_map.c`；不是电机控制协议，也不是孔形重建算法。

## 1. 流程与坐标

```text
人工把探头放到工件 X/Y，保持不动并确认
  → 一次完整回波（4096 点）
  → 来源、标定、门区、饱和与底波检查
  → 原始波形 + 点位诊断 + session.json
  → 电脑二维图 / X-z 剖面 / 三维反射点
  → UTMAP/1 快照 → LVGL 二维 / 轻量三维反射点页
```

工件默认 80×80×30 mm；顶面左上角 `(0,0)`，X 向右、Y 向下，深度 z 从顶面向下。人工输入字段不能冒充机械或视觉标定坐标：

- `position_basis = OPERATOR_ENTERED_WORKPIECE_MM_UNVERIFIED`
- `position_accuracy_mm = null`
- `position_confirmed = true` 仅表示操作者确认，不是独立传感器证明。
- 网格进度是有点位记录的格数占比，不代表声束物理覆盖率、孔径或检测概率。
- 单轴一行数据只是一条剖面；未扫的其他行不能用插值填成正常。

## 2. 会话与逐点 JSON

权威文件：`session.json`，`schema = ut-manual-map/1`。写临时文件后原子替换。新建目录不覆盖旧会话，最多 2048 点、32×32 格。

会话含 `session_id`、`created_unix_ns`、`source`、`spec`、`echo`、`points`、完整 `cells`、计数和 `bridge_error`。`hardware_control=false`、`transmit_enabled_by_this_software=false`；没有输出发射使能。

点记录主要字段：

| 字段 | 含义 |
| --- | --- |
| `point_id` / `index` | 会话内唯一 ID / 记录顺序 |
| `submission_id` | 新客户端提交编号；旧调用可为 null，用于恢复同一次已提交结果 |
| `frame_id` | 采集帧唯一标识，重复帧不能用于另一个点位 |
| `source` | 固定会话来源 DEMO / RECORDED / LIVE，不混合 |
| `position_mm` | 操作者输入的 `[x,y]`，不等同格中心 |
| `diagnosis` / `status` | 原始诊断与去除 DEMO 前缀后的聚合状态 |
| `reflector_depth_mm` | 合法反射深度 z，否则 null |
| `distance_from_bottom_mm` | `thickness-z`，否则 null |
| `depth_reason` | 深度可显示/不可显示的原因 |
| `hole_height_mm` / `hole_diameter_mm` | 始终 null；当前没有可靠测量这两个量 |
| `waveform` / `waveform_sha256` | 完整归档 CSV 的相对路径 / 哈希 |
| `preview` | min/max 包络、步长、触发索引、采样周期；不是原始振荡波形 |

深度仅在疑似指示、回波标定通过、法向入射已确认且 `0<z<thickness` 时进入三维/剖面。一般时间差换算与零点/声速设置须先实物标定，不能靠 DEMO 验证检测精度。

同一格可以重复采集。聚合优先级为 `SUSPECT > UNCALIBRATED > INVALID > NO_INDICATION > UNSCANNED`，不会用后来的无指示点抹除疑似点。每个有效点仍单独保留，不计算假孔体积；小屏格中心只是该格位置提示，精确的人工输入坐标应查点记录。

文件导入后重新生成统一格式 CSV，保留 4096 个采样值、触发索引、周期、gain 和 overrange；下载接口返回该归档文件的原始字节并校验 SHA256，不在前端用包络重造 CSV。它不是输入文件原有排版的逐字节备份。

## 3. 来源与 LIVE 适配

- **DEMO**：全部软件合成，独立会话，标定 ID 明确 DEMO；不得用于实测判定。
- **RECORDED**：文件回放。即使 CSV 声称 LIVE，加载后仍按历史数据处理；含 DEMO 标记的文件不能混入 RECORDED 会话。
- **LIVE**：必须经已有 `scan.ExchangeCapture`。请求使用唯一 request_id，要求请求后完成且 request_id 匹配的 DMA/采集应答、唯一 LIVE frame_id、4096 点、同一主机 monotonic 时钟中的新鲜时间。映射提交时再次检查 2 秒新鲜度。
- 交换请求 `enable_transmit:false`；这一布尔值不是硬件 HV 已物理断开的证明，现场仍必须按安全流程确认。
- 上传 CSV、修改文件时间、创建交换目录，均不能成为 LIVE 驱动。
- ARM 与电脑的 monotonic 时间轴不能直接相减。后续板端桥必须建立请求/应答时序并在接收主机端建立可信时间，不把设备 uptime 填成 HOST monotonic。

## 4. 本机 HTTP 接口

监听 `127.0.0.1:8766`，接受本机 Host 和本机 Origin；不直接对局域网开放。摄像头服务仍使用 8765，二者相互独立。

| 方法 / 路径 | 作用 |
| --- | --- |
| `GET /` / `GET /api/state` | 页面 / 会话快照与适配器状态 |
| `GET /api/waveform?point_id=...` | SHA256 校验后的完整点位波形 JSON |
| `GET /export/session.json` | 权威字段快照 |
| `GET /export/map.csv` | UTMAP/1 小屏快照 |
| `GET /export/report.html` | 不依赖 CDN 的只读离线报告 |
| `GET /export/waveform.csv?point_id=...` | SHA256 校验后的归档 CSV 原始字节 |
| `POST /api/point` | 人工确认后记录一点，不移动、不连续采集 |
| `POST /api/stop` | 停止本机服务，不是硬件急停 |

`POST /api/point` 示例：

```json
{"session_id":"当前 GET /api/state 的 session_id","submission_id":"操作者本次提交的唯一UUID","position_mm":[27.5,32.5],"position_confirmed":true}
```

RECORDED 另外提供字符串字段 `csv`；DEMO/LIVE 不接受上传 CSV。请求体限 1 MiB，拒绝 NaN/Infinity，并发采集请求返回 409，重复帧提交返回 400。所有错误均不补造正常点。页面断线后禁用采集；重新填写或勾选位置不会绕过断线状态。离线报告永久禁用采集。

浏览器始终携带 `session_id`；后端在采集适配器调用之前核对，拒绝旧会话或错误类型。为兼容旧客户端，缺省仍接受；新调用端应总是绑定。一次请求期间不得变更人工位置、网格或输入文件。空输入不得转成 0。状态/采集请求分别在 4/15 秒超时，连同响应体读取一起中止等待，**不自动重发**。超时不证明板端或服务器未执行；应先查看会话记录。若点已保存而后续刷新失败，页面明确告知可能已经提交，不直接引导重采。同一会话较旧的点数与格式错误快照不能覆盖较新有效快照。

### 4.1 提交编号与凭证

`submission_id` 为 1..96 位 ASCII 字母、数字、`-`、`_`。浏览器为每次新人工提交生成 UUID；同一次请求恢复时绝不换编号。显式 null 或非法编号在采集前拒绝，旧客户端省略字段仍兼容，但没有编号级幂等保证。

在当前会话/HTTP handler 内，服务用编号保存规范化位置、完整 CSV 和来源的指纹：

| 情况 | HTTP / 字段 | 是否再次调用采集 |
| --- | --- | --- |
| 新编号成功归档 | 200，`point`、`submission_id`、`replayed:false` | 首次调用一次；DEMO/RECORDED 使用对应软件路径 |
| 同编号、同输入已提交 | 200，同一个 `point`，`replayed:true` | 否，即使原 LIVE 帧现在已过期 |
| 同编号换位置/CSV | 409，`submission_state:CONFLICT` | 否 |
| 有请求正在处理 | 409，`submission_state:BUSY` | 否；不排队、不自动重发 |
| 本次采集或归档失败 | 400，`submission_state:UNCONFIRMED` | 不自动补采 |
| 同编号此前未确认 | 409，`submission_state:UNCONFIRMED` | 否，先检查适配器与旧记录 |
| 会话/位置/参数/容量等前置拒绝 | 400，`submission_state:REJECTED` | 否 |

凭证在 HTTP 响应发送前标记已提交；客户端断开不会把已保存点改成失败。点记录/JSON 保留 `submission_id`。点位上限2048，台账上限4096；新请求容量检查在采集前执行，已提交凭证仍可以重放。一次归档失败会回滚点记录与已用编号，不输出伪造正常点；HTTP 台账保留“未确认”，避免同编号再次触发适配器。

**边界：台账是当前进程的内存状态，不是跨进程崩溃的 exactly-once 协议。** 服务重新启动应创建新会话并检查旧归档，不复用旧会话 ID。不同编号仍代表不同人工请求；这不是禁止重复测量或硬件急停。HTTP `submission_id` 与底层 `ExchangeCapture` 的 `request_id` 是两层独立标识，未实现板端持久事务。

### 4.2 浏览器异常恢复

1. 发送之前把来源、状态和完整请求写入本标签页 `sessionStorage`；写入失败不发送新采集。RECORDED 保留原 CSV，恢复不读取另一个文件。
2. 响应丢失、超时或 BUSY 后锁住原位置/文件，不自动 POST。只有操作者重新回到原位置并勾选确认，才可显式复用原编号和原请求。若原请求根本未执行，这次恢复仍可能触发一次采集，必须保持探头在原位置。
3. UNCONFIRMED/CONFLICT 锁住采集；先核对旧记录和适配器，再另开会话。COMMITTED 等待状态中对应点出现，不继续 POST。
4. 刷新页面先读状态；发现同编号、同位置的保存点便解除待核对标记，不补采、不自动进入下一格。已提交凭证和当前记录不匹配时保留锁定。
5. 旧会话待核对请求不会发送到新会话。核对旧记录后，需要明确点击“核对旧会话后解除锁定”，再确认新位置。损坏/不可读存储也不会静默丢弃；明确解除锁定不代表撤销已执行的请求。

本标签页存储不是持久采集日志；关闭标签页、清除浏览器数据或跨标签页操作不具备同样恢复边界。权威结果仍是会话原始 CSV/JSON 和服务端凭证。不得通过关闭页面绕过未确认状态，也不得靠换新编号“试试有没有采到”。

## 5. UTMAP/1

ASCII、逗号分隔、不使用引号或空格；每份快照包含完整所有格，可用 LF 或 CRLF。上限 90,000 字节；编码/格式错误、重复格或缺格均拒绝，保留上一次有效画面。

```csv
UTMAP,1,2,1,10,5,30,5,DEMO,MANUAL_UNVERIFIED
CELL,0,0,SUSPECT,1,12.000000
CELL,1,0,UNSCANNED,0,
```

首行固定 10 个字段：`UTMAP,版本,列数,行数,长度mm,宽度mm,厚度mm,格距mm,来源,MANUAL_UNVERIFIED`。行列须与向上取整后的尺寸/格距一致，最大各 32。

CELL 固定 6 个字段：`CELL,列索引,行索引,状态,该格记录数,最近有效深度或空`。索引从 0 开始。状态只允许 UNSCANNED / NO_INDICATION / SUSPECT / UNCALIBRATED / INVALID；UNSCANNED 必须 count=0，其他必须大于 0，总记录数不超过 2048。深度只允许出现在 SUSPECT 格，必须有限且在厚度内部。一个格多个反射深度时只导出最近值，不能把最浅/最深差称为孔高。

`session.json` 为权威提交；`map.csv` 写临时文件后替换。若 Windows 文件占用等导致 CSV 更新失败，点仍已保存到权威 JSON，`bridge_error` 报警，下一次更新重试。不做跨两个文件的虚假原子事务承诺。

LVGL 的 `ut_map_decode(buffer,length,...)` 不依赖 Python，可在 PS 软件或 Linux 网络接收任务中重用；`ut_ui_update_map` 须在 LVGL 所在线程调用。`ut_map_load_csv` 是电脑文件输入层。来源标签是采集来源，不是“板卡当前在线”或 freshness 认证；页面明确标为数据快照。

## 6. 迁移分工与未完成事项

| 层 | 建议职责 | 本轮状态 |
| --- | --- | --- |
| PL | ADC 时序、4096 点缓存、DMA、显示扫描输出、发射默认禁止 | RGB 基线保留；独立 touch XSA 构建并审计，OE/INA 恒 0；未下载 |
| ARM/PS | 采集应答、参数/标定管理、LVGL、未来数据传输 | 二维/轻量三维、触控协议和 standalone ELF 构建准备；无网络接收任务 |
| 电脑 | 记录、人工点位、二维/三维、报告、后续相机协同 | DEMO 与历史输入/适配契约验证 |
| 现场 | 固定基准、耦合、实测标定、已知孔/完整块对比 | 尚待配合，未宣称验收 |

本轮 ARM `build/zynq_rgb/ut_lvgl.elf` 和 `build/zynq_touch/ut_lvgl.elf` 都是 standalone 裸机 ELF，不是 Linux 用户态程序；只有离线构建和工件指纹验证。touch 分支的 LVGL 输入、GPIO/I²C 和方向诊断已经离线测试，实屏仍未验证。页面可编译不等于已把 PC map.csv 传到板上。网络传输、Linux DMA/触控 input 驱动、Linux 启动和小屏在线同步仍未完成。

## 7. 技术边界参考

Evident 官方《Sound Field》说明超声定位的声程/距离换算；《DGS/AVG》明确等效反射体尺寸不等于实际不规则缺陷尺寸。本项目因此只提供校准条件下的反射指示，不把点云包装成真实孔洞几何。参考地址：

- https://ims.evidentscientific.com/en/learn/ndt-tutorials/flaw-detection/sound-field
- https://ims.evidentscientific.com/en/learn/ndt-tutorials/flaw-detection/dgs-avg
