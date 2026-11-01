# 真实孔快速补录

目标：一次摆稳后默认保存五个不同frame id，显示完成和诊断计数后即可松开探头，不为等待离线分析一直按住。五个连续帧只算一次摆位，不算五次独立试验。

## 现场顺序

1. 在本地屏幕主动开始真实采集，由操作员按现场需要打开模拟链路/HV。
2. 探头在已确认的检测面和孔位垂直耦合，稳定后运行下述命令。
3. 看到 `COMPLETE` 和 `frames_saved: 5` 即完成该批；然后松开、抬起探头。失败或超时也不要求继续按住，先检查保存的原因。
4. 抬起、重放、重新耦合后，用新的 `Placement` 和输出目录再录一次；每个目标先做三次独立摆位。
5. 补录结束由操作员在屏幕停止；PC录入工具没有停止、开始或保活权限，不延长采集租约。

## 当前电脑命令

在工程根目录执行；`OutputDirectory` 必须不存在。脚本从原WSL私有文件读取凭据，不把口令写入命令行或采录文件。专用直连网口当前为ifIndex24；其他电脑用 `-EthernetIfIndex` 显式指定实际物理以太网接口，不改WiFi和默认路由。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File petalinux/record_acceptance.ps1 -Specimen UT45-02-R3 -Placement d8_p1 -Role HOLE_TARGET -Hole h1 -NominalDepth 8 -OutputDirectory firmware/inspection/runs/accept_d8_p1 -RecouplingConfirmed
```

`-ExecutionPolicy Bypass` 只作用于这一次脚本进程，不更改系统执行策略。默认五帧、12秒截止；可用 `-Frames`、`-Deadline` 在规定范围内调整。HTTP正在响应的单次读取有8秒网络超时，因此总体截止可能被最后一次在途请求增加最多约8秒；实际耗时写入JSON。

孔旁正常区把 `-Role` 改为 `NORMAL_CONTROL`，孔ID仍可保留，使用新的摆位ID和目录。完整块用 `UNDRILLED_REFERENCE`，不要把孔上记录标成完整块。

## 最小补录表

| 优先级 | 目标 | 录法 |
| --- | --- | --- |
| 1 | 2#、Φ3、检测面深8mm | 先确认正反面；孔上三次独立重耦合，孔旁正常区三次 |
| 2 | 3#、Φ3、深15mm | 独立孔上和孔旁对照，明确孔ID |
| 2 | 3#、Φ4、深15mm | 同上，不与2#深15混标 |
| 3 | 3#、Φ5、正面名义深13mm | 背钻17mm不是正面17mm；补少量独立重压 |
| 3 | 已有深15和深22mm | 少量独立重压验收，不再大量堆同次连续帧 |

目前8mm仍未确认。不能通过移动全局零点或直接使用孔标签让候选落到8mm。三维结果仍是反射指示点，孔径/孔高不由这批波形直接推导。

## 保存与检查

每批保存 `frame_001.csv` 等文件及 `acquisition.json`。样本SHA256与板端记录核对，CSV保存哈希独立记录；TEST/PASSIVE、重复ID内容改变、增益/启动身份改变、采样时间倒退被拒绝。质量无效帧照样保存，避免只留下好看的数据。中途错误保留已写文件，状态为 `ABORTED`；帧数不足为 `INCOMPLETE`。不会覆盖旧目录。

CSV中的OTR是整帧聚合标志重复填入列，不是逐样本过量程轨迹。记录保留板端诊断和未批准标定状态。标签只进入采录元数据，不参与预测；原始二进制头未通过此GET接口提供，因此不声称核验了二进制头哈希。

软件测试使用合成数据，不访问设备：

```powershell
python -m unittest discover -s tools/tests -p test_acceptance_batch.py -v
```
