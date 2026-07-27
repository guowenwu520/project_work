# ChangeBlindnessRoom

一个基于 Unity 的桌面场景变化盲视数据集生成项目。

项目在固定桌面场景中放置两个物体，通过摄像机移开、场景发生变化、摄像机返回的方式生成变化盲视视频，并同步输出结构化标注和英文问答数据。

当前版本支持：

- 八种场景变化类型；
- 内置程序化物体和外部模型；
- Linux 无界面批量生成；
- 多进程并行渲染；
- PNG 序列自动编码为 MP4；
- 每个视频生成 8 组英文问答；
- 八种变化分别维护独立的均匀轮回池；
- 对已经生成的视频重新抽取问答，不需要重新渲染。

---

## 1. 场景设计

当前数据集统一使用：

```text
scene_type = tabletop
```

每个视角包含一个或两个桌面物体。凡是同一视角中出现两个物体，
两个物体的类别必须不同。

### 视频时间线

每段视频总时长为 31 秒：

| 阶段 | 时长 | 说明 |
|---|---:|---|
| 初始观察 | 8 秒 | 摄像机展示变化前的桌面场景 |
| 摄像机移开 | 5 秒 | 摄像机从桌面区域移开 |
| 场景变化 | 5 秒 | 在不可见阶段执行变化 |
| 摄像机返回 | 5 秒 | 摄像机重新回到桌面区域 |
| 最终观察 | 8 秒 | 展示变化后的桌面场景 |

固定帧率采样时，完整帧数为：

```text
总帧数 = 31 × FPS
```

例如：

```text
10 FPS → 310 帧
30 FPS → 930 帧
```

---

## 2. 八种变化类型

| 内部名称 | 中文说明 |
|---|---|
| `one_object_replacement` | 替换一个物体 |
| `same_object_color_change` | 同一个物体改变颜色 |
| `distance_increase` | 一个物体移动，使两个物体之间的距离增大 |
| `distance_decrease` | 一个物体移动，使两个物体之间的距离减小 |
| `swap_positions` | 两个物体交换位置 |
| `no_change` | 场景保持不变 |
| `object_adding` | 第一视角 1 个物体，第二视角增加为 2 个 |
| `object_deleting` | 第一视角 2 个物体，第二视角减少为 1 个 |

### 变化约束

- 物理位置 A 固定为第一视角左侧、第二视角右侧。
- 物理位置 B 固定为第一视角右侧、第二视角左侧。
- 替换、变色、距离增大和距离减小固定作用于 A（`changedSlot=left`）。
- 增加和删除固定作用于 B（`changedSlot=right`）；因此新增物体在第二视角中位于左侧。
- 交换固定使用 `changedSlot=both`，无变化固定使用 `changedSlot=none`。
- `object_adding` 严格使用 1→2 的物体数量变化。
- `object_deleting` 严格使用 2→1 的物体数量变化。
- 任一视角中同时出现的物体类别互不重复。
- 不再生成两个物体同时被替换的情况。
- 双物体交互时，位置直接描述为桌面左侧或桌面右侧。
- 不使用“相对于参考物体”的复杂位置表达。
- 位置交换只改变两个物体的位置，不改变身份和颜色。
- 距离增大或减小时，只移动第一视角左侧的物体 A。
- `no_change` 的最终状态与初始状态一致。

---

## 3. 问答系统

问答的唯一编辑入口是：

```text
QAs_v5_d.xlsx
```

`build_linux.sh`、`run_dataset.sh`、`test_all_cases.sh` 和
`regenerate_existing_qa.py` 都会先从 Excel 自动生成：

```text
Assets/StreamingAssets/tabletop_qa_templates.json
```

不要手工编辑该 JSON；下一次同步时它会被 Excel 内容覆盖。

转换只读取 `01_Replacement` 到 `08_Object_Deleting` 这八张分类表。
`Info` 等其他页不作为问答来源，因此说明页中的旧统计不会影响运行时。
运行时 JSON 中的英文问题、英文答案和回答类型直接采用八张分类表，
不对措辞做二次改写。`Variables` 页提供占位符的典型英文值。
输出时只把表格中的 `yes_no` 类型规范为
`question_type: "yes_or_no"`。

如果不增加新的 `{变量名}`，以后只需要修改 Excel 中的英文问题、
英文答案或回答类型，再运行构建、测试、正式生成或问答重生成脚本，
修改就会自动生效。如果加入当前代码不支持的新变量，同步会立即报错，
提示必须先修改场景变量代码。

当前问答配置：

```text
八种变化
替换 58，颜色变化 59
距离增大 30，距离减小 30
位置交换 60，无变化 59
增加物体 39，减少物体 39
总计 374 组模板
每个视频抽取 8 组问答
每个视频中的 8 个问题互不重复
```

JSON schema：

```text
eight-change-tabletop-xlsx-autosync-canonical-slots-metadata-v13
```

### 3.1 均匀轮回抽取

八种变化分别维护独立的问答轮回池。每类所需场景数由
`ceil(模板数 / 8)` 自动计算。

以 60 题的位置交换池连续生成视频为例：

```text
第1个视频：从60题中随机抽8个未出现问题
第2个视频：从剩余52题中抽8个
……
第7个视频：累计覆盖56个不同问题
第8个视频：先抽剩余4个未出现问题
           再从本轮已经使用的56题中随机补4个
```

第 8 个视频完成后：

```text
60个模板均至少出现一次
本轮使用记录清零
下一个同类视频开始新的随机轮回
```

所以每轮 8 个同类视频中：

```text
56个模板出现1次
4个模板出现2次
60个模板全部得到覆盖
```

对于任一 30 题距离变化池，最小覆盖为 4 个同类视频：

```text
28个模板出现1次
2个模板出现2次
30个模板全部得到覆盖
```

当前这份 Excel 的八种变化共 374 条模板，在每视频 8 问的约束下，
最小完整覆盖量是 50 个场景：

```text
8 + 8 + 4 + 4 + 8 + 8 + 5 + 5 = 50
```

通过 `sampling_salt` 可以得到另一套稳定的随机顺序。

---

## 4. 最终数据格式

最终汇总文件：

```text
Output/videodata.json
```

格式如下：

```json
[
  {
    "video_id": "scene_000123",
    "video": "data/video_000123.mp4",
    "video_path": "data/video_000123.mp4",
    "scene_type": "tabletop",
    "metadata": {
      "change_type": "replacement",
      "change_exists": true,
      "view_a_object_count": 2,
      "view_b_object_count": 2,
      "view_a_position_a": ["apple"],
      "view_a_position_b": ["camera"],
      "view_b_position_a": ["cup"],
      "view_b_position_b": ["camera"],
      "view_a_color_a": ["red"],
      "view_a_color_b": ["Null"],
      "view_b_color_a": ["blue"],
      "view_b_color_b": ["Null"],
      "changed_positions": ["position_a"],
      "object_replaced": true,
      "object_added": false,
      "object_removed": false,
      "color_changed": false,
      "position_changed": false,
      "distance_changed": false,
      "distance_change": "none"
    },
    "questions": [
      {
        "question": "Were any objects added or removed?",
        "answer": "No objects were added or removed; the apple was replaced by the cup.",
        "question_type": "yes_or_no"
      }
    ]
  }
]
```

每条记录必须满足：

- `video_id` 非空且唯一；
- `video` 与 `video_path` 保存同一个相对 MP4 路径；
- `video_path` 指向对应 MP4；
- `scene_type` 为 `tabletop`；
- `metadata` 完整列出两个视角中 position A/B 的物体、颜色和数量；
- 空位置使用空数组 `[]`；
- 有物体但该物体没有颜色时，颜色数组使用字符串 `["Null"]`；
- `position_a` 和 `position_b` 是固定桌面位置，不会因为第二视角
  位于桌子对面而互换字段名；
- `metadata.change_type` 使用 `replacement`、`color_change`、
  `distance_increase`、`distance_decrease`、`position_swap`、
  `no_change`、`object_adding` 或 `object_deleting`；
- `questions` 恰好包含 8 组问答；
- 每组问答包含 `question_type`，取值只能为 `descriptive`
  或 `yes_or_no`；
- 同一个视频中的问题不能重复。

---

## 5. 项目结构

```text
ChangeBlindnessRoom/
├── Assets/
│   ├── Editor/                     # Unity编辑器工具
│   ├── Scenes/                     # Unity场景
│   ├── Scripts/                    # 运行时脚本
│   ├── Shaders/                    # Shader
│   ├── StreamingAssets/
│   │   ├── batch_jobs.json
│   │   ├── dataset_config.json
│   │   └── tabletop_qa_templates.json
│   ├── RawModels/                  # 本地原始模型，不提交Git
│   ├── ModelPacks/                 # 模型生成结果，不提交Git
│   └── Resources/
│       ├── BuiltInProps/           # 内置物体生成结果
│       └── ImportedProps/          # 外部物体生成结果
├── ModelBundles/                   # Linux Player运行时模型包
├── Packages/
├── ProjectSettings/
├── Tools/
│   ├── build_linux.sh
│   ├── run_dataset.sh
│   ├── test_all_cases.sh
│   └── regenerate_existing_qa.py
├── Build/                          # Unity编译结果，不提交Git
└── Output/                         # 生成的数据集，不提交Git
```

---

## 6. 环境要求

### Unity

推荐使用项目当前验证过的版本：

```text
Unity 2022.3.58f1c1
```

### Linux 批量运行依赖

```bash
sudo apt update
sudo apt install -y ffmpeg xvfb python3
```

可选检查：

```bash
ffmpeg -version
ffprobe -version
xvfb-run --help
python3 --version
```

### 脚本权限

```bash
chmod +x \
  Tools/build_linux.sh \
  Tools/run_dataset.sh \
  Tools/test_all_cases.sh \
  Tools/regenerate_existing_qa.py
```

---

## 7. 模型准备

仓库默认不提交模型、生成网格和生成 Prefab。

被 Git 忽略的主要目录包括：

```text
Assets/RawModels/
Assets/ModelPacks/
Assets/Resources/BuiltInProps/Data/
Assets/Resources/BuiltInProps/Generated/
Assets/Resources/ImportedProps/
ModelBundles/ 中的实际模型文件
```

### 7.1 外部模型

支持将 FBX、OBJ、GLB 等模型放入本地模型目录：

```text
Assets/RawModels/
```

项目中的编辑器工具负责模型预处理、Prefab 生成、模型校验和模型包构建。相关编辑器代码位于：

```text
Assets/Editor/
```

主要工具脚本包括：

```text
RawModelPreprocessor.cs
RawModelRenameSynchronizer.cs
ImportedPropManifestBuilder.cs
ImportedPropValidator.cs
ModelBundleBuilder.cs
ModelLibraryManagerWindow.cs
```

生成后的模型资源通常位于：

```text
Assets/ModelPacks/Generated/
Assets/Resources/ImportedProps/
ModelBundles/
```

### 7.2 内置程序化物体

内置物体由项目代码和编辑器工具生成。

相关代码：

```text
Assets/Scripts/ProceduralPropFactory.cs
Assets/Scripts/BuiltInPropCatalog.cs
Assets/Scripts/BuiltInPropLibrary.cs
Assets/Editor/BuiltInPropPrefabBuilder.cs
```

生成结果位于：

```text
Assets/Resources/BuiltInProps/Data/
Assets/Resources/BuiltInProps/Generated/
```

这些目录不提交 Git。首次克隆项目后，需要通过项目内的生成工具重新生成。

### 7.3 Linux运行时模型包

批量脚本默认从以下目录读取模型：

```text
ModelBundles/
```

必须至少存在：

```text
ModelBundles/prop_manifest.json
```

实际模型文件需要在运行前由使用者自行准备或构建。

---

## 8. 构建 Linux Player

批量脚本默认查找：

```text
Build/Linux/ChangeBlindnessRoom.x86_64
```

完整的构建目录应类似：

```text
Build/Linux/
├── ChangeBlindnessRoom.x86_64
├── ChangeBlindnessRoom_Data/
│   └── StreamingAssets/
│       └── tabletop_qa_templates.json
└── dataset_schema_version.txt
```

构建完成后增加执行权限：

```bash
chmod +x Build/Linux/ChangeBlindnessRoom.x86_64
```

批量运行前，脚本会：

1. 从 `QAs_v5_d.xlsx` 自动生成最新问答 JSON；
2. 检查 Linux Player 是否存在；
3. 检查 `dataset_schema_version.txt`；
4. 检查模型清单；
5. 将最新问答 JSON 同步到 Player 的 `StreamingAssets`。

首次升级到 v13 时需要重新构建 Linux Player。之后如果只修改现有
Excel 问答且不新增变量，可以直接运行 `run_dataset.sh`，它会自动同步
Excel 和 Player 的问答 JSON，不需要重新编译 C#。

---

## 9. 唯一测试入口

`Tools` 中只保留一个测试脚本：

```text
Tools/test_all_cases.sh
```

完整渲染测试：

```bash
./Tools/test_all_cases.sh
```

快速问答逻辑测试（不启动 Unity）：

```bash
QA_ONLY=1 ./Tools/test_all_cases.sh
```

两种模式都会先读取当前 Excel，再根据八张分类表动态计算刚好覆盖
全部模板所需的最小场景数。当前 58、59、30、30、60、59、39、39
条模板分别需要 8、8、4、4、8、8、5、5 个场景，共 50 个。
以后调整模板数量时，测试场景数也会自动更新。测试会检查运行时 JSON
确实由当前 Excel 生成、只使用 `01`–`08`、Variables 全量替换、
固定位置英文、`Null` 颜色、单场问题唯一性、A/B 槽位对应、
物体数量变化、完整 metadata、question_type，以及任一视角内
物体类别不重复。完整渲染模式会通过 `FORCE_CHANGED_SLOT`
明确传入每类变化唯一允许的规范槽位。

指定输出目录和帧率：

```bash
OUTPUT=/data/test_all_cases \
FPS=30 \
./Tools/test_all_cases.sh
```

---

## 10. 正式批量生成

基础运行：

```bash
./Tools/run_dataset.sh
```

推荐示例：

```bash
UNITY_JOB_WORKERS=2 \
WORKERS=8 \
FFMPEG_THREADS=2 \
RANDOM_START=1 \
COUNT=100 \
USE_XVFB=1 \
FPS=30 \
./Tools/run_dataset.sh
```

### 常用参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `COUNT` | `24` | 生成视频数量 |
| `START_INDEX` | `0` | 起始编号 |
| `RANDOM_START` | `0` | 是否随机生成起始编号 |
| `WORKERS` | `2` | 同时运行的 Unity Player 数量 |
| `UNITY_JOB_WORKERS` | `2` | 每个 Player 内部 Unity Job Worker 数量 |
| `FPS` | `30` | 视频帧率 |
| `WIDTH` | `384` | 固定分辨率宽度 |
| `HEIGHT` | `384` | 固定分辨率高度 |
| `RANDOM_RESOLUTION` | `1` | 是否随机选择预设分辨率 |
| `USE_XVFB` | `1` | 是否使用虚拟显示 |
| `OUTPUT` | `项目目录/Output` | 输出目录 |
| `MODEL_BUNDLE_DIR` | `项目目录/ModelBundles` | 模型包目录 |
| `CLEAN_OUTPUT` | `0` | 运行前是否清空输出目录 |
| `RESUME` | `0` | 已有MP4时是否跳过 |
| `DELETE_FRAMES` | `1` | MP4编码成功后是否删除PNG |
| `CRF` | `16` | H.264编码质量 |
| `PRESET` | `medium` | FFmpeg编码预设 |
| `FFMPEG_THREADS` | `2` | 每个FFmpeg任务的线程数 |
| `FORCE_CHANGE_TYPE` | 空 | 强制生成指定变化类型 |
| `FORCE_CHANGED_SLOT` | 空 | 测试参数；必须与变化类型的规范槽位一致，否则立即报错 |
| `SEED` | 空 | 固定场景随机种子 |

### 随机分辨率

`RANDOM_RESOLUTION=1` 时，每个视频会稳定地选择以下一种分辨率：

```text
CLIP   336 × 336
DFN    378 × 378
SigLIP 384 × 384
```

关闭随机分辨率：

```bash
RANDOM_RESOLUTION=0 \
WIDTH=384 \
HEIGHT=384 \
./Tools/run_dataset.sh
```

### 断点续跑

```bash
RESUME=1 \
START_INDEX=100000 \
COUNT=500 \
./Tools/run_dataset.sh
```

当目标 MP4 已存在且非空时，该编号会被跳过。

### 强制变化类型

```bash
FORCE_CHANGE_TYPE=swap_positions \
COUNT=20 \
./Tools/run_dataset.sh
```

---

## 11. 已有视频重新抽取问答

该脚本不会修改 MP4 和 PNG。只有原视频本身已遵守本版本的
A/B 槽位对应关系时，才能无需重新运行 Unity。

输出目录至少需要包含：

```text
Output/
├── data/video_*.mp4
└── Batch_*/annotation.json
```

推荐先执行只读检查：

```bash
./Tools/regenerate_existing_qa.py \
  Output \
  --dry-run
```

只读检查会验证：

- 替换、变色、距离增大和距离减小是否实际标记为第一视角左侧 A；
- 增加和删除是否实际发生在第一视角右侧 B；
- 两次视角中的物体状态是否与变化类型一致。

如果旧视频的 `changedSlot` 或变化前后状态与固定表格描述不一致，
脚本会报错并要求重新渲染。它不会为了通过检查而改写表格问答，
也不会把不匹配的视频伪装成正确数据。

### 正式重新抽取

```bash
./Tools/regenerate_existing_qa.py \
  Output \
  --require-all-videos
```

这会先从 `QAs_v5_d.xlsx` 自动更新运行时 JSON，再重新均匀抽取问答，
并同步更新 `metadata`、`question_type` 和最终的 `videodata.json`。

只想检查 Excel 并生成运行时 JSON，不处理视频时：

```bash
./Tools/regenerate_existing_qa.py --sync-templates-only
```

### 指定其他输出目录

```bash
./Tools/regenerate_existing_qa.py \
  /data/change_blindness/Output \
  --require-all-videos
```

### 更换随机轮回

```bash
./Tools/regenerate_existing_qa.py \
  Output \
  --sampling-salt 2
```

相同的 `sampling_salt` 会产生相同的均匀轮回顺序。

### 允许跳过缺少 MP4 的记录

不写 `--require-all-videos` 时，脚本会跳过缺少或为空的 MP4。
正式数据建议保留该参数，让缺失视频直接报错。

### 不创建旧问答备份

```bash
./Tools/regenerate_existing_qa.py \
  Output \
  --no-backup
```

默认情况下，旧问答会备份到：

```text
Output/qa_backup_before_regenerate_时间戳/
```

该脚本只更新：

```text
Batch_*/annotation.json
Batch_*/qa_entries.json
Batch_*/qa.txt
Output/videodata.json
```

不会修改：

```text
Output/data/*.mp4
Batch_*/frames/
Unity场景
模型文件
```

---

## 12. 输出目录

典型输出结构：

```text
Output/
├── data/
│   ├── video_000001.mp4
│   ├── video_000002.mp4
│   └── ...
├── logs/
│   ├── batch_000001.log
│   └── ...
├── Batch_000001_*/
│   ├── annotation.json
│   ├── qa_entries.json
│   ├── qa.txt
│   └── frames/                     # DELETE_FRAMES=0时保留
└── videodata.json
```

### 单场景文件

`annotation.json` 保存：

- 场景编号和随机种子；
- 变化类型；
- 变化前左右物体；
- 变化后左右物体；
- 当前视频的 8 组问答；
- 视频相对路径。

`qa_entries.json` 保存最终汇总格式的一条视频记录。

`qa.txt` 提供便于人工查看的文本版本。

---

## 13. Git提交范围

建议提交：

```text
Assets/Editor/
Assets/Scripts/
Assets/Scenes/
Assets/Shaders/
Assets/StreamingAssets/
Packages/
ProjectSettings/
Tools/
.gitignore
.gitattributes
README.md
ModelBundles/prop_manifest.json
```

必须提交 Unity 的 `.meta` 文件，否则资源引用 GUID 会丢失。

不提交：

```text
Assets/RawModels/
Assets/ModelPacks/
Assets/Resources/BuiltInProps/Data/
Assets/Resources/BuiltInProps/Generated/
Assets/Resources/ImportedProps/
ModelBundles/中的实际模型
Build/
Output/
Library/
Temp/
Logs/
UserSettings/
```

提交前可检查：

```bash
git ls-files |
grep -E \
'(^Assets/RawModels/|^Assets/ModelPacks/|^Assets/Resources/BuiltInProps/(Data|Generated)/|^Assets/Resources/ImportedProps/|\.(fbx|obj|glb|gltf|blend|dae|3ds|stl|ply)$)'
```

没有输出表示模型没有被 Git 跟踪。

---

## 14. 常见问题

### 14.1 提示找不到 Linux Player

确认以下文件存在并具有执行权限：

```bash
ls -lh Build/Linux/ChangeBlindnessRoom.x86_64
chmod +x Build/Linux/ChangeBlindnessRoom.x86_64
```

### 14.2 提示 schema 不一致

重新构建 Linux Player，并确认：

```text
Build/Linux/dataset_schema_version.txt
```

内容为：

```text
eight-change-tabletop-xlsx-autosync-canonical-slots-metadata-v13
```

### 14.3 提示找不到模型清单

确认：

```bash
ls -lh ModelBundles/prop_manifest.json
```

### 14.4 测试时显示310帧但提示不完整

当前正确判断为：

```text
31秒 × 10 FPS = 310帧
```

请确认使用的是最新的 `Tools/run_dataset.sh`。

### 14.5 编码失败

确认 FFmpeg 可用：

```bash
ffmpeg -version
ffprobe -version
```

编码失败时，PNG帧会保留，便于重新编码和排查。

### 14.6 服务器没有图形界面

安装并启用 Xvfb：

```bash
sudo apt install xvfb
USE_XVFB=1 ./Tools/run_dataset.sh
```

### 14.7 不想删除PNG帧

```bash
DELETE_FRAMES=0 \
./Tools/run_dataset.sh
```

### 14.8 已有视频只想修改问答

```bash
./Tools/regenerate_existing_qa.py Output
```

请先加 `--dry-run` 检查。通过规范槽位校验的视频不需要重新运行
Unity；未通过的视频必须重新渲染。

---

## 15. 当前限制

- 当前生成单物体或双物体桌面场景；
- `scene_type` 固定为 `tabletop`；
- 每个视频固定输出 8 组英文问答；
- 当前变化类型固定为八种；
- 问答模板依赖场景标注中的物体名称、颜色和位置；
- 仓库不包含外部模型和生成模型资源；
- 克隆仓库后需要在本地恢复模型并重新构建 Linux Player；
- Unity编译结果和生成数据不纳入版本控制。
