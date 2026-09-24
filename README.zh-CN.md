# DualSPHysics MCP

**语言：** [English](README.md) | 中文

[![Listed on mcpservers.org](https://mcpservers.org/badge.svg)](https://mcpservers.org/servers/vegetableno1/dualsphysics-mcp)
[![vegetableno1/DualSPHysics-mcp MCP server](https://glama.ai/mcp/servers/vegetableno1/DualSPHysics-mcp/badges/score.svg)](https://glama.ai/mcp/servers/vegetableno1/DualSPHysics-mcp)
[![CI](https://github.com/vegetableno1/DualSPHysics-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/vegetableno1/DualSPHysics-mcp/actions/workflows/ci.yml)

DualSPHysics MCP 将本机 [DualSPHysics](https://dual.sphysics.org/) SPH 流体
求解器工具链封装为十个 [MCP](https://modelcontextprotocol.io/) 工具：**无需手写
XML 的案例创作**（`create_case` / `edit_case` / `describe_case`，纯 Python、强
校验）、案例前处理（GenCase）、带进度解析的后台 CPU 求解、后处理
（PartVTK / MeasureTool），以及对照 Koshizuka & Oka (1996) 实验的溃坝定量验证。
目标用户是需要"设计、跑通并验证" SPH 仿真的 agent（或驱动 MCP 客户端的人），
而不只是看动画。

本 server 只以子进程方式调用 DualSPHysics 命令行工具，
**不分发** DualSPHysics 本体：DualSPHysics 是 **LGPL-2.1-or-later** 自由软件，
本包既不链接也不分发其代码/二进制，因此本仓库的 MIT 许可证不产生额外义务。
若在发表的工作中使用 DualSPHysics，请引用
*Dominguez et al. (2022), "DualSPHysics: from fluid dynamics to multiphysics
problems", Computational Particle Mechanics 9:867–895,
[doi:10.1007/s40571-021-00404-2](https://doi.org/10.1007/s40571-021-00404-2)。*

## 工具

| 工具 | 作用 |
| --- | --- |
| `check_environment` | 探测 GenCase / 求解器 / PartVTK / MeasureTool：解析到的路径、来源（环境变量 / PATH / 扫描）、版本、求解器特性（如无造波功能的构建）；缺失时给安装指引。 |
| `create_case` | 用"模板 + 参数覆盖"设计案例 `*_Def.xml`——不手写 XML、不需要求解器。2D 溃坝家族：水柱、水槽、下游障碍物、dp、域边距、TimeMax/TimeOut、SWL gauges；返回格点法粒子数估算供自检。 |
| `edit_case` | 对已有 `*_Def.xml` 应用同一套覆盖词汇表（就地修改或另存 `save_path`），合并后整体重校验。 |
| `describe_case` | 只读摘要 `*_Def.xml`：dp、域、水柱/水槽/障碍物、时序、gauges、粒子估算——agent 的自检闭环。 |
| `gencase` | 运行 GenCase：案例 `*_Def.xml` → `Case.xml` + `Case.bi4`（+ 预览 VTK）。返回从 VTK 头与控制台解析的粒子数（总/流体/边界）。 |
| `run_case` | **后台**启动 CPU 求解器（jobs/ 目录、`status.json`、`Run.out`、`data/Part_*.bi4`），立即返回 `job_id`。 |
| `job_status` | 轮询作业：状态、`t`/`tmax`、百分比、步数计数、求解器 `Time/Sec` 吞吐与其自估完成时间、粒子数、`Run.out` 尾部。 |
| `partvtk` | PartVTK：`Part_*.bi4` → VTK 粒子文件（可直接 ParaView），筛选与变量透传。 |
| `measure_tool` | MeasureTool：点位 SPH 插值 → CSV 时序（强制 `-csvsep:1` 逗号，保证解析确定）。 |
| `validate_dambreak` | 纯 Python：从 MeasureTool CSV 重建溃坝前锋位置，与内置 Koshizuka & Oka (1996) 序列对比；逐时刻误差 + MAE/RMSE/最大误差。 |

工作流：`check_environment` → `create_case`（设计）→ `describe_case`（自检）→
`gencase`（离散化、真实粒子数）→ `run_case` → 轮询 `job_status` →
`partvtk`（可视化）+ `measure_tool`（定量）→ `validate_dambreak`。

## 案例创作（不写 XML 设计实验）

`create_case` / `edit_case` / `describe_case` 覆盖"设计"阶段：agent 用结构化
参数描述一个 2D 溃坝家族实验，server 渲染出 GenCase 的 `*_Def.xml`。
`dambreak_val2d` 模板内置官方验证布局（dp = 0.01 m、1 m × 2 m 水柱、4 m × 3 m
水槽、TimeMax = 2 s、x = 0.2 与 z = 0.03 两根 gauges）；模板是数据不是代码，
后续加布局很便宜。覆盖键（未知键会被拒并附上词汇表清单）：

| 键 | 含义 |
| --- | --- |
| `dp` | 粒子间距（m）；粒子数按 dp⁻² 缩放 |
| `column_length`、`column_height` | 原点处的水柱尺寸 |
| `tank_length`、`tank_height` | 敞顶水槽尺寸 |
| `obstacle` | `{"x", "width", "height"}` 立在水槽底面的实心方柱（须在水柱下游），或 `null` |
| `margins` | 域 = 水槽 + 边距（`x_min`/`z_min`/`x_max`/`z_max`，默认 1/1/0.5/0.5 m 飞溅余量） |
| `domain` | 显式域盒 `{"pointmin": [x, z], "pointmax": [x, z]}`（优先于 margins） |
| `time_max`、`time_out` | TimeMax / TimeOut（秒） |
| `gravity`、`rhop0`、`cfl`、`visco` | 物理常数 |
| `gauges` | `{"type": "vertical", "x", "z_top"?}` / `{"type": "horizontal", "z", "x_end"?}` SWL 探针列表 |

三道防线直接来自实测的 GenCase 行为：几何越出域盒会被**静默裁剪**
（退出码 0），因此域必须严格包住水槽——边距必须为正、显式 `domain` 覆盖要做
包含性校验（否则 `DSPH_BAD_INPUT`）；`<setdrawmode mode="full"/>` 固定携带
（缺了流体会少一层格点：20,000 → 19,701）；2D 即 XZ 平面 y 钉死为 0，且每个
盒子都跨过 y = 0（不跨该平面的几何会静默产生 0 个粒子）。

设计一个变体——水柱 1.5 m + 下游障碍物：

```
create_case(out="cases/CaseObst_Def.xml",
            overrides={"column_height": 1.5,
                       "obstacle": {"x": 2.5, "width": 0.1, "height": 0.1}})
  -> particle_estimate: fluid=15000 bound=1111 total=16111
describe_case(path="cases/CaseObst_Def.xml")     # 自检摘要
gencase(xml_path="cases/CaseObst_Def.xml")
  -> particle_counts: total=16111 fluid=15000 bound=1111   # 估算 == 实测
edit_case(path="cases/CaseObst_Def.xml", overrides={"time_max": 3.0})
```

| 案例（GenCase v5.4.354 实测） | 流体 | 边界 | 总数 |
| --- | --- | --- | --- |
| 基准（模板默认） | 20,000 | 1,001 | 21,001 |
| 水柱 1.5 m + x=2.5 处 0.1×0.1 障碍物 | 15,000 | 1,111（990 + 121） | 16,111 |
| 同上但 dp = 0.008 | 31,250 | 1,251 | 32,501 |

估算算式（流体每轴 `round(L/dp)`；敞顶水槽壳 `(round(L/dp)+1) + 2·round(H/dp)`；
实心障碍物 `(round(w/dp)+1)·(round(h/dp)+1)` 并顶替其底面格点行）已对照真实
GenCase 八组配置验证，且结果里注明以 GenCase 自己的粒子汇总为准。程序化演示见
[`examples/create_case_demo.py`](examples/create_case_demo.py)。

## 领域契约（踩坑实录）

以下是各工具 `-h` 里看不出来的行为，代码逐一依赖：

- **Run.out 行格式随版本不同。** v5.0/v5.2 打印
  `Part_0001  0.010016  314  314  494.50  21-06-2022 22:48:14`（6 列）；
  v5.4 打印 `00001  0.010017  314  314  21,001  2,736  216.96  <日期> <时间>`
  （8 列、`%05d` 裸编号、计数带**千位分隔符**）。解析器两者兼容；
  进度 = `PartTime / TimeMax`。
- **`Time/Sec` 不是步/秒。** 求解器源码（`JSph::SaveData`）证实：它是最近一个
  PART 的"每模拟秒的墙钟秒数"；行尾两个 token 是求解器自己预估的
  **完成日期时间**（ETA）。`job_status` 原样透出两者。
- **完成标志**为 `Finished execution (code=N).`（`main.cpp`）； N=0 成功；
  致命错误前面会出现 `*** Exception(exc): ...` 行。
- **GenCase 参数惯例**：传不带 `.xml` 的路径基名
  （`GenCase CaseX_Def OUT/CaseX`）。本封装两种写法都收并自动去后缀；
  默认输出名去掉 `_Def`，默认输出目录 `<name>_out`。GenCase 自己的日志写在
  案例旁（`CaseX.out`），**不是** `Run.out`。
- **粒子数**优先取 `-save:all` 生成的 VTK 头（即使数据体是二进制，
  `POINTS <n> float` 行也是 ASCII），回退到控制台行
  `Total particles: 21,001 (bound=1001 ... fluid=20000)` ——解析时要小心
  形近行（`MassFluid=[0.1]`）。
- **DualSPHysics 的 CSV 默认分号分隔**（`DsphConfig.xml` 的 `-csvsep:0`）。
  `measure_tool` 一律追加 `-csvsep:1`（逗号），调用方自带 `-csvsep` 时除外；
  `validate_dambreak` 两种都嗅探。
- **MeasureTool 点位文件不接受行首 `#` 注释行**（报 "There are not valid
  points in file"）；只有行尾内联注释安全——见
  `examples/dambreak_val2d/points_damtip.txt`。`-points` 路径按工具的工作目录
  （作业目录）解析，封装层会先转绝对路径。报错信息打在 **stdout** 而非 stderr。
- **MeasureTool 的 `-savecsv <prefix>` 写出 `<prefix>_<Var>.csv`**（每个变量
  一个文件），表头是转置的：PosX/PosY/PosZ 行 + `Part,Time [s],Var_0,...`
  头行，数据行是 `part,time,值...`。`validate_dambreak` 兼容该布局
  （也支持普通的 time 开头布局）。
- **求解器依赖同目录的 `.so`**（`libChronoEngine.so`、`libdsphchrono.so`）。
  有的安装带 rpath、有的没有——`run_case` 因此总是把求解器目录前置到
  `LD_LIBRARY_PATH`。
- **2D 案例**通过域定义 `pointmin 的 y == pointmax 的 y` 声明；
  几何的 y 范围被钳制到该平面。
- **以 `-DDISABLE_WAVEGEN` 构建的求解器**（从 GitHub 源码自编译时常见，
  仓里没有 `libjwavegen_64`）不能跑造波/波浪类案例；`check_environment`
  读取求解器 `-info` JSON 并标注。溃坝等重力驱动案例不受影响。
- **`-ver` / `-info` 可能以非零码退出**，但横幅内容正确
  （GenCase 的 `-ver` 退出码是 1）。`check_environment` 以打印出的横幅为准，
  不看退出码。
- **断点续算/取消**未实现。接口就是 `extra_args` 透传
  （如 `-partbegin:<n>`）——求解器 CLI 接受的都能传。

## 安装

Python 3.10+；DualSPHysics 二进制从环境变量、PATH 或常规安装根
（`/opt`、`/usr/local`、`~/softwares`、`~`、当前目录 →
`<根>/DualSPHysics*/bin/linux`）发现。

```bash
# server + 开发工具
uv sync                 # 或：python -m venv .venv && .venv/bin/pip install -e .
```

获取求解器（GitHub 仓预编译了 GenCase/PartVTK/MeasureTool，但**不含**求解器）：

1. 官网完整包 <https://dual.sphysics.org/downloads/>（浏览器下载，含全部），或
2. `git clone https://github.com/DualSPHysics/DualSPHysics` 源码编译 CPU 版：
   `make -f Makefile_cpu`（仅需 g++，CPU 版无需 CUDA），
   再取 `bin/linux/` 下的预编译工具。

示例目录结构（`.env.example` / `examples/mcp_config.example.json` 同款）：

```
/home/<user>/softwares/DualSPHysics/bin/linux/
├── GenCase_linux64  DualSPHysics5.4CPU_linux64  PartVTK_linux64
└── MeasureTool_linux64  libChronoEngine.so  libdsphchrono.so ...
```

用环境变量配置（不做 .env 解析；变量清单见 `.env.example`）：

```
DSPH_GENCASE=/home/<user>/softwares/DualSPHysics/bin/linux/GenCase_linux64
DSPH_SOLVER=/home/<user>/softwares/DualSPHysics/bin/linux/DualSPHysics5.4CPU_linux64
DSPH_PARTVTK=/home/<user>/softwares/DualSPHysics/bin/linux/PartVTK_linux64
DSPH_MEASURETOOL=/home/<user>/softwares/DualSPHysics/bin/linux/MeasureTool_linux64
DSPH_JOBS_DIR=/绝对路径/jobs      # 可选，默认 ./jobs
DSPH_OMP_THREADS=8               # 可选，默认全部核心
```

## 运行

```bash
.venv/bin/python mcp_server.py    # hub 风格入口（stdio）
.venv/bin/dualsphysics-mcp        # console script
uvx dualsphysics-mcp              # PyPI 发布后
```

客户端注册模板（uvx 与本地 venv 两个版本）见
[`examples/mcp_config.example.json`](examples/mcp_config.example.json)：

```json
{ "mcpServers": { "dualsphysics-mcp": { "command": "uvx", "args": ["dualsphysics-mcp"] } } }
```

```bash
claude mcp add dualsphysics-mcp -- /绝对路径/.venv/bin/python /绝对路径/mcp_server.py
```

## 示例：2D 溃坝验证（Koshizuka & Oka 1996）

`examples/dambreak_val2d/` 是亮相案例：程序化生成的 2D 溃坝案例
（dp = 0.01 m；1 m × 2 m 水柱置于 4 m × 3 m 水箱；TimeMax = 2 s、
TimeOut = 0.01 s；Verlet/Wendland/人工粘度 0.02/Fourtakas DDT 0.1——即官方
验证案例的布局，由 `gen_dambreak_val2d.py` 重新生成，MIT）、追踪前锋的
MeasureTool 点位文件（z = 0.03 m 线上 401 个点）、以及数字化实验序列。

参考数值（GenCase v5.4.354 / 求解器 v5.4.355 实测，8 CPU 线程，i5-10210U）：

| 量 | 值 |
| --- | --- |
| 总粒子数 | **21,001**（流体 20,000 + 边界 1,001） |
| Part 文件 | 201 个（`Part_0000` … `Part_0200`） |
| 求解墙钟时长（TimeMax = 2 s） | 约 14 分钟 |
| 前锋 MAE vs 实验（全窗口 0.09–0.75 s） | 0.220 m（水柱的 22.0 %） |
| 前锋误差，坍塌早期（t ≤ 0.2 s） | 约 ±0.01–0.16 m |
| 撞壁 | 前锋于 t ≈ 0.67 s 钉在 3.98 m |

前锋提取与求解器自带的 SWL gauge（`GaugesSWL_Swl_z003.csv`）交叉核对过
（平均偏差 8 mm——小于 10 mm 点距），因此与实验的残差是 SPH 物理本身
（DBC 前锋在坍塌中段略超前实验），不是测量方法的问题。实验序列止于
X/a ≈ 4.13，超出 4 m 水箱，故撞壁后对比饱和——`validate_dambreak` 会给出
`impact_time_s` 并在 `notes` 中说明。

Agent 操作序列：

```
check_environment()                                  # 四个工具全部找到
gencase("examples/dambreak_val2d/CaseDambreakVal2D_Def.xml")
  -> particle_counts: total=21001 fluid=20000 bound=1001
run_case("<out>/CaseDambreakVal2D")                  # 返回 job_id
job_status(job_id)                                   # 轮询到 percent=100
partvtk(job_id=job_id)                               # 201 个流体 VTK
measure_tool(job_id=job_id, points_file="examples/dambreak_val2d/points_damtip.txt")
validate_dambreak(csv_path="<measure csv>",
                  points_file="examples/dambreak_val2d/points_damtip.txt")
```

`validate_dambreak` 返回逐时刻误差与 MAE / RMSE / 最大误差（米，及占 1 m
水柱的百分比）、撞壁时刻，以及对比饱和时的说明。

## 测试

```bash
uv run pytest -q          # 或：.venv/bin/python -m pytest
uv run ruff check .
```

**无求解器环境全套全绿**：工具发现、命令构造、Run.out 解析（真实 v5.0/v5.4
日志样本）、验证数学（手算合成 CSV）、案例生成（XML 结构、覆盖参数校验、
edit/describe 往返、对照真实 GenCase 实测的粒子估算锚点）、以及在完全没有
工具链的环境下拉起 server 走案例创作工具的 stdio 端到端测试。标记 `solver`
的用例在本机装有工具链时额外实测，否则自动跳过。

## 目录

```
DualSPHysics-mcp/
├── README.md, README.zh-CN.md     本文件 + 英文版
├── LICENSE                        MIT（本仓库）
├── pyproject.toml                 hatchling、src 布局、dualsphysics-mcp 入口
├── mcp_server.py                  stdio 入口 shim（hub 惯例）
├── .env.example                   DSPH_* 变量模板
├── src/dualsphysics_mcp/
│   ├── server.py                  MCPServer + 10 个工具（pydantic 返回）
│   ├── config.py                  环境变量 + 工具发现
│   ├── errors.py                  稳定错误码
│   └── tools/
│       ├── environment.py         check_environment
│       ├── casegen.py             create_case / edit_case / describe_case
│       ├── gencase.py             gencase
│       ├── runner.py + runout.py  run_case / job_status（+ Run.out 解析）
│       ├── postprocess.py         partvtk / measure_tool
│       └── validate.py            validate_dambreak（+ 内置实验数据）
├── examples/
│   ├── dambreak_val2d/            案例 XML 生成器、点位、实验 CSV
│   ├── create_case_demo.py        create_case 基准 + 变体演示
│   └── mcp_config.example.json    客户端注册模板
└── tests/                         pytest 套件（求解器用例自动跳过）
```

## 仓库规则

入库：源码、测试、示例（案例生成器 + 测量点位 + 注明出处的数字化实验数据）、
文档、模板。**不入库**：DualSPHysics 二进制或源码（LGPL 本体不进来）、
虚拟环境、`.env`、作业输出（`jobs/`）、生成结果（`*_out/`、`Part_*.bi4`、
VTK/CSV 工件）、缓存、内部工具目录。`examples/dambreak_val2d/` 中的实验数值
为注明 Koshizuka & Oka (1996) 出处的数字化事实（随 DualSPHysics 示例分发）；
生成的案例 XML 与全部代码为本仓库原创 MIT 内容。
