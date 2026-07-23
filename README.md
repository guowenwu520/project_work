# ChangeBlindnessRoom

一个基于 Unity 的桌面场景变化盲视数据集生成项目。

项目在固定桌面场景中放置两个物体，通过摄像机移开、场景发生变化、摄像机返回的方式生成变化盲视视频，并同步输出结构化标注和英文问答数据。

当前版本支持：

- 六种场景变化类型；
- 内置程序化物体和外部模型；
- Linux 无界面批量生成；
- 多进程并行渲染；
- PNG 序列自动编码为 MP4；
- 每个视频生成 8 组英文问答；
- 每种变化维护独立的 60 题均匀轮回池；
- 对已经生成的视频重新抽取问答，不需要重新渲染。

---

## 1. 场景设计

当前数据集统一使用：

```text
scene_type = tabletop
```

每个场景固定包含两个桌面物体，分别位于桌面左侧和右侧。

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

## 2. 六种变化类型

| 内部名称 | 中文说明 |
|---|---|
| `one_object_replacement` | 替换一个物体 |
| `two_objects_replacement` | 同时替换两个物体 |
| `same_object_color_change` | 同一个物体改变颜色 |
| `distance_increase` | 两个物体之间的距离增大 |
| `swap_positions` | 两个物体交换位置 |
| `no_change` | 场景保持不变 |

### 变化约束

- 当前场景中始终只有两个桌面物体。
- 双物体交互时，位置直接描述为桌面左侧或桌面右侧。
- 不使用“相对于参考物体”的复杂位置表达。
- 位置交换只改变两个物体的位置，不改变身份和颜色。
- 距离增大时两个物体分别向桌面左右两侧移动。
- `no_change` 的最终状态与初始状态一致。

---

## 3. 问答系统

问答模板位于：

```text
Assets/StreamingAssets/tabletop_qa_templates.json
```

当前问答配置：

```text
六种变化
每种变化 60 组英文问答模板
每个视频抽取 8 组问答
每个视频中的 8 个问题互不重复
```

JSON schema：

```text
six-change-tabletop-8qa-60pool-v7
```

### 3.1 均匀轮回抽取

六种变化分别维护独立的 60 题轮回池。

以一种变化连续生成视频为例：

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
    "video_path": "data/video_000123.mp4",
    "scene_type": "tabletop",
    "questions": [
      {
        "question": "What changed during the video?",
        "answer": "The apple was replaced by the cup."
      }
    ]
  }
]
```

每条记录必须满足：

- `video_id` 非空且唯一；
- `video_path` 指向对应 MP4；
- `scene_type` 为 `tabletop`；
- `questions` 恰好包含 8 组问答；
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
│   ├── run_dataset.sh
│   ├── test_six_changes.sh
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
  Tools/run_dataset.sh \
  Tools/test_six_changes.sh \
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

1. 检查 Linux Player 是否存在；
2. 检查 `dataset_schema_version.txt`；
3. 检查模型清单；
4. 将最新问答 JSON 同步到 Player 的 `StreamingAssets`。

如果 schema 不一致，需要重新构建 Linux Player。

---

## 9. 六种变化测试

运行：

```bash
./Tools/test_six_changes.sh
```

默认行为：

- 使用 10 FPS；
- 依次生成六种变化各一个视频；
- 输出到带时间戳的测试目录；
- 调用正式批量脚本完成渲染和编码；
- 检查每种变化是否各出现一次；
- 检查每个视频是否有 8 组不重复问答；
- 检查 MP4 和 `videodata.json` 是否存在。

指定输出目录：

```bash
OUTPUT=/data/test_six_changes \
./Tools/test_six_changes.sh
```

指定测试帧率：

```bash
FPS=30 \
./Tools/test_six_changes.sh
```

测试成功时会输出：

```text
[six-change validation passed]
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

无需重新运行 Unity，也不会修改 MP4 和 PNG。

### 先检查

```bash
./Tools/regenerate_existing_qa.py \
  Output \
  --dry-run
```

### 正式重新抽取

```bash
./Tools/regenerate_existing_qa.py \
  Output
```

### 指定其他输出目录

```bash
./Tools/regenerate_existing_qa.py \
  /data/change_blindness/Output
```

### 更换随机轮回

```bash
./Tools/regenerate_existing_qa.py \
  Output \
  --sampling-salt 2
```

相同的 `sampling_salt` 会产生相同的均匀轮回顺序。

### 严格检查视频完整性

```bash
./Tools/regenerate_existing_qa.py \
  Output \
  --require-all-videos
```

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
six-change-tabletop-8qa-60pool-v7
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

不需要重新运行 Unity。

---

## 15. 当前限制

- 当前只生成双物体桌面场景；
- `scene_type` 固定为 `tabletop`；
- 每个视频固定输出 8 组英文问答；
- 当前变化类型固定为六种；
- 问答模板依赖场景标注中的物体名称、颜色和位置；
- 仓库不包含外部模型和生成模型资源；
- 克隆仓库后需要在本地恢复模型并重新构建 Linux Player；
- Unity编译结果和生成数据不纳入版本控制。
