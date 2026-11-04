# 视声探伤：Zynq 超声探伤与视觉定位工作站

面向 Zynq-7020 的视觉引导超声探伤项目：手持探头扫描钢制试块，在 ARM Linux 上关联回波与位置，通过电脑网页和 800×480 LVGL 小屏查看结果。

关键词：**用户友好、视觉引导超声探伤、FPGA–ARM 异构协同**。

> **r13 公开源码快照，2026-11-04 核对更新。** 自主应用、UI、RTL 与参考配置按 MIT 开放；本仓库是完整参赛源码的公开子集，不包含可直接烧写的镜像、厂商平台或专用内核驱动。

## r13 版本与报告核对

- 同步 r13 的视觉黑点标记、接触位置配准、LVGL 显示与同帧诊断代码，补齐 `specimen_reference.py` 及相应测试。
- 视觉采用工件平面配准与探头标记跟踪；已知孔位图层只供对照，`feeds_diagnosis=false`，不参与选峰或测深。
- 回波分析保留后段静区搜索、正常参考对比和质量拒绝原因。未批准声学标定时显示“参考深度”，不将其写入正式 `depth_mm`。
- 本次报告采用冻结的 8 组、50 帧真实记录；原配置重放 50/50 一致。此前 55 帧不与本批混计，连续帧也不作为独立准确率证据。
- 匹配 r13 镜像已写入 Flash 并独立读回核验；已有启动记录为 JTAG 引导 U-Boot 后从 QSPI 读取 FIT，物理断电冷启动仍待统一验收。
- 当前装置使用手持探头，不配置单轴滑台。历史运动接口保留为扩展代码，不列为当前整机成果。

完整状态、真实数据统计和版本来源见 [r13 最终版本核对](docs/FINAL_R13_STATUS.md)。公开源码不包含原始测量归档、现场正常参考包络、私人照片或设备凭据。

### 复现与快速补录

完整的私有参赛源码集合已从新目录重新综合实现，并在新建PetaLinux工程完成3898项构建任务；FIT、设备树、rootfs、驱动与源码一致性审核通过。当前LIVE平台LUT4978、寄存器8337、BRAM11.5、DSP0，setup/hold裕量6.273/0.042ns。DRC有警告并达到单规则报告上限，不能写成“零警告”。这份公开仓库仍不分发专用驱动、实测参考或镜像，公开范围不因此扩大到整个私有交付包。

`petalinux/capture_acceptance_batch.py` 与 `record_acceptance.ps1` 提供快速补录：只读取已经启动的真实波形，默认收集五帧，拒绝重复frame id、TEST/PASSIVE来源、参数改变和样本哈希错误。质量无效帧也保留，便于复核；一次连续五帧不算五次独立重新耦合。工具不开始、不保活、不改增益，不操作运动或Flash。使用前在本地屏幕明确开始采集，收集后仍由操作员停止。

`skill/` 包含可独立使用的标准库采样完整性校验、门区复核流程及四个合成CSV样例。`report/llm_collaboration.md` 记录真实协作节选，不是完整原始聊天导出。新增补录测试可执行 `python -m unittest discover -s tools/tests -p test_acceptance_batch.py -v`。

## 系统结构

```mermaid
flowchart LR
    Camera["USB UVC 摄像头"] --> ARM["ARM Linux：视觉、会话、回波分析"]
    ADC["ADC / 采集 PL"] --> DMA["AXI DMA → DDR"]
    DMA --> ARM
    ARM --> Web["以太网 → 电脑网页上位机"]
    ARM --> LVGL["LVGL → VDMA → 7 寸 RGB LCD"]
    Touch["电容触控 / I²C"] --> LVGL
```

原型工件顶面 80×80 mm、厚度 30 mm；这些只是默认参数，并非视觉自动量尺结果。

## 功能与真实边界

| 模块 | 当前能力 | 尚待完成 |
| --- | --- | --- |
| 视觉定位 | 四角平面配准、红胶带中央黑点跟踪、接触位置配准与失标处理 | 最终装配下的位置误差、光照与视差实测 |
| 手动扫描 | 停稳录入、位置与采集时间匹配、覆盖提示、二维结果与正式深度点三维显示 | 连续手扫、位置映射与真实回波的统一验收 |
| 回波分析 | 门区、静区质量检查、正常参考对比、反射候选、标定审核与增益绑定 | 正式声学标定、独立重新耦合验证和检出能力评估 |
| 双端 UI | 网页与 LVGL 共用板端诊断，区分实时、上一帧、回放和演示 | 按钮响应与显示停滞的长时稳定性验收 |
| ARM Linux | 板端采集调度、相机取流、分析、认证 API 和本地交互 | 物理断网冷启动与结果断电恢复 |

DEMO 是合成数据；RECORDED 是导入记录；LIVE 必须来自实际采集源。DMA ramp 只验证传输，不是真实超声。三维图是反射位置指示，不是孔形、孔径或孔高重建。未扫描区域始终未知。

## 独立运行与实时诊断

部署目标是板上 ARM Linux 负责相机、采集调度、回波分析及 LVGL，电脑只提供网页上位机。HTTP 监听不再以电脑是否连接或指定本机地址是否就绪作为小屏服务的启动条件；指定对端、Host、Origin 和认证检查仍然保留。

启动参数采用 `ip=off net.ifnames=0`：避免内核等待网线载波，同时禁止自动将 `eth0` 改名；板端由用户态静态配置取得局域网地址。已验证电脑直连网口禁用期间小屏/相机启动、恢复网线后 API 和真实图像正常。该验证仍通过 JTAG 发起启动，不等于 BOOT 拨码切换后的物理断电冷启动验收。

受控 LIVE 通路启动后默认 STOPPED，不自动发射、不自动恢复扫描。开始采集需要操作者明确确认并持续保活；停止、错误或租约到期均撤销发射。LCD 不依赖浏览器保活。这里的程序控制不是物理高压安全切断，也不包含电机限位和急停验收。

每帧只在板端计算一次诊断，以带序号和单调时钟的 72 字节 `UTD1` 记录送给小屏；网页读取同一结果。错帧、过期、未标定或无可信基线时，只保留明确标注的波形，不输出虚假的厚度或无缺陷结论。`trigger_index=0` 的新采集不能套用旧 ILA 的预触发基线。

小屏“声学设置”采用独立 100 字节 `UTE1` 状态桥接，打开页面或更新草案都不会应用标定或发射。参考检查必须来自明确启动的本地 LIVE 新帧；完整块在缺陷门越限时拒绝批准，不能靠屏蔽浅层或抬阈值冒充通过。检查通过仍需人工审核，应用前停采，应用后不自动重新启动。详见 [声学设置操作说明](firmware/inspection/docs/ACOUSTIC_SETUP.md)。

只读历史审计模块 `utinspect.reference_audit` 保留 ILA 与 ADC 的各自采样周期、原始校验和和 RECORDED 来源；审计不是新硬件采集或缺陷验收。

相机一小时证据只验证取流和服务稳定性，不代表视觉精度、真实回波、触控实按、运动闭环或冷启动通过。标定及扫描会话目前为易失数据；不能将浏览器缓存或 RAM 目录称为断电保存。

### 小屏脱机面扫入口

新增“视觉面扫”页面：相机预览、冻结后触选四角、固定确认、真实波形双确认、面扫双确认、已扫/未扫网格、结束二维、查看三维及新工件。进入页面或完成视觉标定不会发射。

位置录入要求至少三张不同图像、350 ms 停稳、同格漂移不超过 1 mm，以及图像出队/ADC 时间戳相差不超过 200 ms。丢标记、相机掉线、标定改变或时序不符时不生成新位置点。这不是相机曝光与超声触发的硬件同步，尚未验收物理定位精度。

详见 `firmware/inspection/docs/OFFLINE_MANUAL_SCAN.md`。声学参考审核流程已实现，正式校准与现场验证仍待完成；扫描会话的 SD 持久化及断电恢复尚未验收。

## 不接硬件体验

需要 Python 3.10 或以上。在 `firmware/inspection` 下执行：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m utinspect map-demo --out runs/quickstart-demo
python -m utinspect manual-map --mode DEMO --out runs/manual-demo
```

最后一个命令在终端打印本机网页入口。手动打开浏览器；这里的 DEMO 不接相机，不控制高压或电机。参数以 `python -m utinspect --help` 及各子命令的 `--help` 为准。

USB 相机本机调试：

```bash
python -m utinspect workpiece --camera 0 --preview
```

首次安装依赖需要联网。相机索引按现场设备调整；平面标定、探头标记与人工到位确认不能省略。

## 测试

在 `firmware/inspection` 下执行：

```bash
python -m unittest discover -s tests -v
```

这里保留便携业务测试；依赖私有交付镜像、原机下载环境或平台发布脚本的测试不属于本次源码，排除清单见 `SOURCE_MANIFEST.json`。测试通过不等于实板或测量精度验收。

## LVGL 与硬件源码

- `firmware/lvgl_ui`：自主 UI、触控解析与过滤、Linux/桌面/裸机端口。依赖 LVGL 9.4.0；`python tools/setup.py` 按固定哈希获取，不打包整个 vendor。
- Windows 桌面 UI 需要 PATH 中的 MinGW `gcc`/`ar`，随后在该目录执行 `python tools/build_desktop.py`。
- `Makefile.linux` 是原生 Linux 构建入口；交叉编译时由 SDK 提供 `CC`/`AR`。端口依赖板卡专用驱动，并非普通电脑即插即用界面。
- `fpga`：安全触控平台的自定义 RTL、约束与测试源。缺少重建完整 BD 所需的已审平台快照，**不能将这些源文件当成可直接烧写工程**。
- `petalinux/linux`、`petalinux/recipes`：内核/设备树配置和 recipe 参考。不分发厂商内核/IP、SDK、BSP、XSA、镜像、凭证或本机 JTAG 下载脚本。
- 专用内核驱动和 `ut-platform` recipe 因许可标注需单独核对，本次未公开。LVGL recipe 对该驱动仍有依赖，不能直接从这里完整构建板端 Linux。

## 安全与许可

先读 [SAFETY.md](SAFETY.md)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [PUBLICATION_CHECKLIST.md](PUBLICATION_CHECKLIST.md)。默认安全 PL 保持停发；真实采集只允许显式选择的 guarded LIVE 平台及匹配驱动。专用驱动未随本仓库发布，因此不能仅靠本仓库接入真实发射，也没有可用的运动闭环。不要绕过这些边界，也不要自动恢复来源未审查的旧固件。

自主代码采用根目录 [LICENSE](LICENSE) 的 MIT 许可；LVGL、字体等第三方内容保留各自许可。未提供启动镜像、私人照片、参赛签名材料、设备身份或口令。
