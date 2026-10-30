# 第三方与许可范围

除另有明确标识的第三方内容外，自主 Python、网页、UI、端口、RTL 和参考配置使用根 `LICENSE` 的 MIT 许可。这不改变任何上游组件的权利。

| 组件 | 许可与处理 |
| --- | --- |
| LVGL 9.4.0 | MIT，原文见 `LICENSES/LVGL-MIT.txt`。完整上游源码不内置，setup 脚本按固定哈希获取 |
| 中文字模 | SIL Open Font License 1.1，见 `firmware/lvgl_ui/assets/noto_license.txt`，来源版权说明见 `LICENSES/Noto-CJK-NOTICE.txt` |
| OpenCV、NumPy | requirements 安装，不内置依赖代码，保留各上游许可 |
| 参考 recipe | 自主配置为 MIT；LVGL 配方保留字体 OFL 标识和 LVGL 原许可校验 |
| AMD/Vivado/PetaLinux、厂商内核/IP | 不分发 SDK、IP 实现、内核树、BSP、bit/XSA/rootfs/BOOT；使用外部工具需自行遵守其条款 |

原内部 `ut_platform.c` 声明 MODULE_LICENSE("GPL")，其 recipe 却标记 CLOSED。为避免擅自改变不明许可，**本次不包含该驱动及 ut-platform recipe**，不以 MIT 覆盖它们。引用该驱动的端口和参考配置不意味着已经提供完整驱动或闭环能力。

板卡手册、厂商原理图、EDA 原件、比赛原始材料、私人记录及签名也没有默认再分发，不在本次公开范围。
