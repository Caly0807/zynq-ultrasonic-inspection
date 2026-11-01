# 可复用技能包

本包是AMD自主选题指南3.3.5.2意义上的可复用流程、校验脚本与案例，不是已安装的Codex插件，也不是PYNQ Overlay或PYNQ Skill。

## 内容与适用场景

| 条目 | 适用场景 | 输入与输出 |
| --- | --- | --- |
| check_capture.py | 无符号整数型规则采样ADC/传感器CSV检查 | CSV→点数、幅度、时间/索引、SHA256与明确错误 |
| echo_gate_review.md | 脉冲回波门区设计和错例复核 | 声速、零点、范围→预期时间、边界样点与验收记录 |
| artifact_verification.md | FPGA+Linux多产物发布核验 | 源码/产物/日志→分层证据和放行清单 |
| fast_capture_review.md | 一次摆位快速采录与独立重复 | 已运行原始帧接口→短批次、哈希、质量状态和摆位记录 |

## 脚本用法

Python 3.10及以上，无第三方库。脚本只读CSV，不访问网络、设备、寄存器或Flash。实际采样文件示例：

python check_capture.py frame.csv --value-column "ad_data_r[7:0]" --radix 16 --index-column "Sample in Buffer" --period-ns-column "Sample Period ns" --expected-period-ns 40 --expected-count 4096 --bits 8 --skip-radix-row

通用CSV可改用value/index列及十进制，不与超声或某块开发板绑定；也支持带time_us列的等间隔采样。命令细节见python check_capture.py --help。

## 验证效果与边界

验证由生成脚本执行并记录到data/skill_validation.json：对本项目55份原始CSV检查完整性、索引、周期、范围与保存时SHA256，并以合成越界、乱序、非均匀时钟和错误预期哈希四种反例确认拒绝。该检查不批准测深标定，不判断耦合、工件有无缺陷、标签是否正确，也不把相邻连续帧当独立实验。

来源是实际观察到的“文件完整但诊断仍可不成立”、22mm门区漏覆盖、环境/发布错配和8mm错例。陌生队伍可改列名与参数检查其他规则采样传感器，不需要本项目正常参考包络或私有镜像。

## 失效条件

不适用于变采样率、非均匀时间戳、有符号/浮点传感器值、压缩编码或无明确列契约的文件；需要先适配解析契约。没有时间/周期列时无法独立证明实际采样时钟。脚本对同一次读取的字节做哈希与解析，采录仍应先结束写入并保存原始校验和。校验和只能证明文件身份一致，不能证明其物理标签、算法准确率或独立重复性。
