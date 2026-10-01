# Automata Explorer

**自动机可视化练习与反馈系统 · Python / Flask / SQLite / JavaScript / vis-network**

Automata Explorer is a web-based learning tool for finite automata. It supports teacher-authored exercises, graphical DFA construction, equivalence checking, shortest counterexamples, and minimisation feedback.

自动机结构不同，接受的语言却可能相同。这个项目用来辅助检查学生构造的 DFA，并解释答案为什么正确或错误：教师发布题目，学生画出自动机，系统返回最短反例、状态轨迹和最小化反馈。

## 功能

- **教师端**：使用 DFA、正则表达式或 NFA JSON 出题；预览转换后的 DFA；设置确定性、最小性要求；管理学生、分组、开放时间及练习访问权限；导出 CSV。
- **学生端**：使用图形编辑器或结构化文本构建 DFA，提交答案、查看反馈和历史记录。
- **题目版本**：修改目标或评分要求后，旧提交保留为历史版本，当前完成状态需要重新提交；修改标题、分组或开放时间不改变评分版本。
- **权限与导出**：筛选后的权限表只修改当前显示的题目，并保留已归档分组的原有分配；进度 CSV 区分旧版本答卷，空筛选不会导出其他题目。
- **算法反馈**：检查语言等价性、转移完整性和最小性；返回最短反例、双方执行轨迹、可合并状态组及划分快照。
- **题目示例**：偶数个 `a`、包含子串 `ab`、可被 3 整除的二进制串，以及正则表达式和 NFA 来源题目。

## 原理

1. 教师提供目标 DFA，或先将正则表达式转换为 ε-NFA，再通过子集构造生成 DFA。
2. 学生提交的自动机先经过结构解析，再检查每个状态在各输入符号下是否有合法转移。
3. 等价性检查对两个自动机补全缺失转移，在乘积自动机上执行 BFS；遇到接受状态不一致的状态对时，返回最短区分字符串。
4. 最小性检查结合可达性分析和 Hopcroft 风格的划分细化；反馈展示可合并状态及中间划分。
5. Flask 处理角色与练习流程，SQLite 保存题目和提交记录，前端展示自动机及检查结果。

## 本地运行

[在线 DFA 演示](https://gabriel6324.github.io/automata-explorer/)提供单个自动机的输入串模拟。完整教师、学生系统按下面的步骤运行。

建议使用 **Python 3.10 或更高版本**，已在 Python 3.12 下测试。无需 LLM API 或 API key。

解压或克隆仓库后，在包含 `app.py` 的目录执行：

```bash
python -m venv .venv
```

Windows PowerShell 激活环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS / Linux 激活环境：

```bash
source .venv/bin/activate
```

然后安装依赖并启动：

```bash
python -m pip install -r requirements.txt
python app.py
```

浏览器访问 [http://127.0.0.1:5000](http://127.0.0.1:5000)。首次运行自动创建本地数据库、两个演示账号和一道基础题目。

| 角色 | 演示用户名 | 演示密码 |
| --- | --- | --- |
| 教师 | `teacher1` | `teacher123` |
| 学生 | `student1` | `student123` |

这些账号仅供本地演示。原始数据库、学生记录、论文和视频不包含在源码仓库中。

如需五道演示题，在启动服务前运行：

```bash
python seed_demo.py
python app.py
```

`seed_demo.py` 在基础题目上补充演示题，重复执行不会重复添加同名题目，也不会清空已有提交。生成的数据只保存在本地。

## 建议演示流程

1. 使用教师账号登录，查看题目列表；新建题目时选择正则表达式来源，输入 `(a|b)*ab` 并预览。
2. 使用学生账号打开偶数个 `a` 的题目，切换到文本编辑，填写状态 `q0,q1`、初始状态 `q0`、接受状态 `q0`，以及下列转移。字母表 `a,b` 由题目固定；导入 JSON 时也需要保持相同字母表。

   ```text
   q0,a=q1
   q0,b=q0
   q1,a=q0
   q1,b=q1
   ```

3. 提交后查看等价、确定性和最小性结果。将接受状态改成 `q1` 再提交，可观察空串反例 `ε` 和执行轨迹。
4. 返回教师端查看学习进度及提交记录。

## 检查与复现

```bash
python -m unittest discover -s tests -v
node tests/test_frontend.cjs
```

Python 测试覆盖算法、输入校验、角色访问、首次改密、分组归属、权限表筛选和题目版本。Node.js 检查编辑器清空、撤销、JSON 导入、固定字母表、文本消息和异步预览；无效 JSON 不会覆盖当前草稿，过期预览不会覆盖较新的输入。它使用隔离 DOM，并非完整浏览器测试。GitHub Actions 自动运行两类检查，测试使用临时数据库。

## 文件结构

| 路径 | 内容 |
| --- | --- |
| `app.py` | Flask 路由、数据库初始化和自动机算法 |
| `templates/` | 教师端、学生端及登录页面 |
| `static/` | 图形编辑器逻辑和页面样式 |
| `seed_demo.py` | 可重复执行的演示题目初始化脚本 |
| `tests/` | 算法回归检查与 Web 流程检查 |
| `requirements.txt` | Python 依赖 |
| `index.html` | 仓库早期的单文件 DFA 演示，可直接用浏览器打开；完整系统入口为 `app.py` |

## 配置与运行边界

- `AUTOMATA_SECRET_KEY`：可通过操作系统环境变量设置会话密钥；未设置时每次启动随机生成，重启后需重新登录。应用不会自动读取 `.env` 文件。
- `AUTOMATA_DB_PATH`：可选的 SQLite 路径，默认是项目目录中的 `automata.db`；自定义路径的父目录需已存在。
- 图形编辑器从 CDN 加载固定版本 `vis-network@9.1.9`，需要能够访问该 CDN。若图形未显示，请检查网络；结构化文本输入仍可用于练习。
- 输入和轨迹以单字符符号为单位，拒绝多字符符号；空字母表可用于空串语言。状态名不能包含逗号、分号、等号或换行；这些分隔符与 `ε` 不能作为输入符号。正则支持连接、`|`、`*` 和括号，`ε` / `e` 表示空串，不是完整的 Python 正则语法。
- CSV 导出对可能被电子表格识别为公式的文本加上前置单引号；数字保持原值。
- 默认服务仅监听本机，并检查 Host、Origin 和跨站请求标记，使用 SameSite=Lax 会话 Cookie。退出登录使用 POST。当前版本保留演示账号，尚未实现完整的 CSRF 令牌机制和登录限流；教师创建学生时会暂存初始密码供分发，学生修改后清除。使用真实学生数据或部署到公网前需要进一步改造。

## 许可证

本项目采用 [MIT License](LICENSE)。第三方依赖遵循各自的许可证。
