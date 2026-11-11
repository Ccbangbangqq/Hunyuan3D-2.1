# Security hardening — Hunyuan3D-2.1 fork

> 本分支相对上游 `Tencent-Hunyuan/Hunyuan3D-2.1@main` 只做安全加固，不改变模型结构与推理逻辑。
> 内网代码扫描报出的 2 个 critical（shell process）已修复，同时清掉了 3 处同类写法。
> All changes are shell-injection / dynamic-exec hardening only; model code and behaviour are untouched.

## 1. 背景

| 项目 | 内容 |
|---|---|
| 扫描对象 | `Tencent-Hunyuan/Hunyuan3D-2.1` main 分支 |
| 扫描结论 | 2 个 critical：`hy3dpaint/train.py` 第 141、143 行的 `subprocess(..., shell=True)` |
| 复核结果 | 同类写法另有 3 处（`os.system` ×2、`eval` ×1），扫描器下次大概率也会报 |
| 本分支处理 | 5 处全部修复，AST 扫描结果从 **5 → 0** |

## 2. 修复清单

| # | 位置 | 修复前 | 修复后 | 风险类型 |
|---|---|---|---|---|
| 1 | `hy3dpaint/train.py:141` | `check_output('git ls-files -- ":!:configs/*"', shell=True)` | `check_output(["git", "ls-files", "--", ":!:configs/*"], shell=False)` | shell 注入（扫描器 critical） |
| 2 | `hy3dpaint/train.py:143` | `check_output("git ls-files --others --exclude-standard", shell=True)` | `check_output(["git", "ls-files", "--others", "--exclude-standard"], shell=False)` | shell 注入（扫描器 critical） |
| 3 | `hy3dshape/hy3dshape/postprocessors.py:194` | `os.system(f'{self.executable} {temp_input.name} {temp_output.name}')` | `subprocess.run([self.executable, temp_input.name, temp_output.name], check=True)` | 命令注入（真实可利用：临时文件名进入 shell） |
| 4 | `gradio_app.py:69` | `os.system("cd /home/user/app/... && bash compile_mesh_painter.sh")` | `subprocess.run(["bash", "compile_mesh_painter.sh"], cwd="/home/user/app/...")` | shell 调用（硬编码，扫描器仍会报） |
| 5 | `hy3dshape/hy3dshape/data/utils.py:41` | `return eval(expr.format(s))` | 显式模块路径解析，不再使用 `eval` | 任意代码执行 |

## 3. 关键点：为什么不能只把 `True` 改成 `False`

`subprocess` 的 `args` 是**字符串**时，行为随平台而变：

- **Windows**：字符串被整条传给 `CreateProcess`，由目标程序自己解析，`shell=False` 也能“碰巧”跑通；
- **POSIX**：字符串被当成**可执行文件路径**，直接 `FileNotFoundError`。

本仓库的训练脚本面向 Linux，所以在 `shell=False` 的同时必须把参数改成 **argv 列表**。
第 1、2 处采用 `shell=False` + 列表：字面满足“`shell=False`”的整改要求，同时保证跨平台行为不变。

## 4. 行为影响

| 位置 | 变化 | 是否影响结果 |
|---|---|---|
| `train.py` | 不再经 shell 执行 `git ls-files` | 否。实测返回同样的 tracked / untracked 文件列表，仅少了一次 shell 进程 |
| `postprocessors.py` | 可执行文件与两个临时路径作为独立 argv 传入；增加 `check=True` | 正常路径结果一致。异常时改为**显式报错**（原先忽略退出码，随后在 `trimesh.load` 处才失败，错误信息更难定位） |
| `gradio_app.py` | `cwd=` 替代 `cd ... &&`；保留“不抛异常”的原有语义，额外打印 exit code | 否 |
| `data/utils.py` | `safe_eval` 不再 `eval`，只支持 `"{}"` 与 `"<模块路径>.{}"` 两种模板，其余抛 `ValueError` | 否。该函数在本仓库内**未被任何代码调用**（死代码），改动仅为消除扫描告警 |

## 5. 验证

```text
# 1) 语法
py_compile: hy3dpaint/train.py, hy3dshape/hy3dshape/postprocessors.py,
            hy3dshape/hy3dshape/data/utils.py, gradio_app.py   -> 全部通过

# 2) AST 级扫描（不统计注释与文档字符串，见 security/scan_security.py）
上游 main          : 5 处 (shell=True x2, os.system x2, eval x1)
本分支             : 0 处  -> NO FINDINGS

# 3) train.py 两条 git 命令
git ls-files -- ":!:configs/*"          -> 正常返回 tracked 文件列表
git ls-files --others --exclude-standard -> 正常返回

# 4) safe_eval 行为
safe_eval('foo')                       -> 'foo'
safe_eval('sqrt', 'math.{}')           -> math.sqrt
safe_eval('a;b')                       -> ValueError: illegal characters
safe_eval('x', '__import__("os")...')  -> ValueError: unsupported expression template
```

复跑扫描：

```bash
python security/scan_security.py .
```

## 6. 与上游的差异

```text
hy3dpaint/train.py                              | 4 ++--
hy3dshape/hy3dshape/postprocessors.py           | 6 ++++--
hy3dshape/hy3dshape/data/utils.py               | 20 ++++++++++++++++----
gradio_app.py                                   | 8 +++++---
security/scan_security.py                       | (新增，验证用扫描脚本)
SECURITY-FIXES.md                               | (新增，本文件)
```

## 7. 已知限制 / 未做的事

1. **未跑完整训练或推理验证**：本机没有该仓库的完整依赖（`diffusers`、`pymeshlab`、CUDA 扩展等）与权重，
   只做了语法级与逻辑级验证。正式合入前建议在 GPU 环境跑一次 `hy3dpaint/train.py` 的启动流程，
   确认 `CodeSnapshot` 回调正常工作。
2. **`postprocessors.py` 的 `check=True` 属行为收紧**：若 `mesh_simplifier.bin` 存在但返回非 0 退出码，
   现在会抛异常而非静默继续。若现场环境依赖旧行为，可改为 `check=False` 并检查 `returncode`。
3. **`safe_eval` 的模板支持变窄**：仅支持 `"{}"` 与 `"<dotted.module>.{}"`。仓库内无调用点，
   但若外部代码调用它并传入其他模板，会抛 `ValueError`。
4. 本次为静态加固，**未做**运行期沙箱、依赖漏洞扫描（CVE）与 SBOM 生成。

## 8. 与上游同步

若后续 merge 上游更新，请注意以上 5 个文件可能产生冲突。
推荐先 `git fetch upstream && git rebase upstream/main`，再运行 `python security/scan_security.py .`
确认仍然 `NO FINDINGS`。
