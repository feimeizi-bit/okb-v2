# S7便携离线候选包 v1

此包保留仓库布局，不宣称纯pip安装完整可用。它是内部离线候选材料，未完成许可证/权属确认或公开发布批准；也不是fresh openKylin、真实双Agent或独立语义准确率验收。

## 制备与边界

在源码工作区使用已有、完整安装的依赖目录，不下载或自动安装：

```text
python tools/build_portable.py --dependency-dir EXISTING_DEPENDENCY_DIRECTORY --out-dir NEW_EXTERNAL_DIRECTORY
```

依赖目录必须提供PyYAML、jsonschema、attrs、jsonschema-specifications、referencing、rpds-py、typing_extensions的发行元数据及RECORD列出的运行文件。直接解析原始RECORD，不采用会过滤缺失文件的便利属性；缺运行文件/越界记录拒绝。唯一不读取的外部脚本例外是jsonschema发行记录里的`../../bin/jsonschema[.exe]`，明确列入排除清单，不用于本入口。精确版本写入`requirements.portable.lock.txt`，复制文件及许可证由manifest逐个绑定；不读取`.env`，不复制Git、真实raw、私有人标/验证答案或原始运行目录。依赖`direct_url.json`和缓存字节码不携带。候选白名单是源码、schema、明确的公开开发夹具及本说明，不等于权属已经审定。

包不含Python解释器。由于PyYAML/rpds原生扩展，包绑定制备时的操作系统、架构与Python ABI；Windows包不能冒充可在openKylin直接运行。Linux/openKylin须以目标平台已有依赖重新制备并实际验收，不能将Windows结果写成Linux通过。

## 从任意工作目录启动

```text
python PATH_TO_BUNDLE/okb_portable.py --check
python PATH_TO_BUNDLE/okb_portable.py --selfcheck --out-dir NEW_OUTPUT_DIRECTORY
python PATH_TO_BUNDLE/okb_portable.py report-vnext --help
```

包路径可含空格；按shell规则引用路径。启动器从自身路径找tools/schema/vendor，不改变调用者工作目录。用户显式相对参数仍按调用者工作目录解释。输出必须为新目录；自检不向包内写入产物/字节码，不覆盖已有结果。

每次入口核对manifest成员、哈希、依赖版本、导入位置和平台ABI。它检测漂移，不是签名或恶意篡改认证；manifest和启动器一起被修改不在此哈希校验的保证范围内。Python audit guard阻断受审计的连接/DNS和子进程，且入口拒绝live/付费/凭据参数；它不是操作系统防火墙或安全沙箱，不能用来认证不可信外部代码不会联网。

默认仅允许validate、simulate、score-vnext的prepare/replay、report-vnext、report-multirun及列出的离线审阅命令。真实Agent执行、VM/部署/reset和live不从该入口开放；普通仓库入口的既有行为不变，真实运行继续遵守独立金额/范围批准。

## 自检证明什么

自检生成4条作者工程控制：客观通过、客观失败、文件语义的程序化回放、未获真实准入的记忆拒判。实际经过现有mock引擎、v2/scoped评分、两次同run回放和`--verify-traces`报告；保留计划/证据/完整结果/HTML和逐文件complete。语义回放答案是程序夹具，不是真人标签或判官推理；4条不计入120独立验证、24真实pilot或正式100题。

成功只说明该平台的这条公开离线链可运行、同run回放确定性和包未改变。真实记忆门仍关闭；没有模型HTTP、真实Agent或人标。不能从自检推出语义可靠、跨平台稳定或fresh openKylin验收。

私有全仓回归仍需要明确提供历史paper/run/交付材料，不能混入公共包或以本自检替代。S7真实报告、正式雷达与S10新openKylin/真实双Agent视频继续待验收。
