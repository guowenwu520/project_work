#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

UNITY_VERSION="${UNITY_VERSION:-2022.3.58f1c1}"
UNITY_BIN="${UNITY_BIN:-}"
UNITY_JOB_WORKERS="${UNITY_JOB_WORKERS:-4}"

LIGHT_PROJECT="${LIGHT_PROJECT:-$PROJECT_DIR/.LightBuildProject}"
BUILD_DIR="$PROJECT_DIR/Build/Linux"
LIGHT_BUILD_DIR="$LIGHT_PROJECT/Build/Linux"
LOG_FILE="$PROJECT_DIR/Build/build_linux.log"

SCHEMA_VERSION="${SCHEMA_VERSION:-eight-change-tabletop-xlsx-autosync-physical-ab-compact-json-v15}"
QA_WORKBOOK="$PROJECT_DIR/QAs_v5_d.xlsx"
QA_REGENERATOR="$PROJECT_DIR/Tools/regenerate_existing_qa.py"
QA_SOURCE="$PROJECT_DIR/Assets/StreamingAssets/tabletop_qa_templates.json"
QA_DEST="$BUILD_DIR/ChangeBlindnessRoom_Data/StreamingAssets/tabletop_qa_templates.json"
PLAYER="$BUILD_DIR/ChangeBlindnessRoom.x86_64"


find_unity() {
  local candidate

  if [[ -n "$UNITY_BIN" ]]; then
    printf '%s\n' "$UNITY_BIN"
    return 0
  fi

  for candidate in \
    "$HOME/Unity/Hub/Editor/$UNITY_VERSION/Editor/Unity" \
    "/home/asher/Unity/Hub/Editor/$UNITY_VERSION/Editor/Unity" \
    "/Unity/Hub/Editor/$UNITY_VERSION/Editor/Unity"
  do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}


require_directory() {
  local path="$1"

  if [[ ! -d "$path" ]]; then
    echo "Missing Unity project directory: $path" >&2
    exit 1
  fi
}


require_file() {
  local path="$1"

  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 1
  fi
}


if ! UNITY_BIN="$(find_unity)"; then
  echo "Unity executable was not found." >&2
  echo "Specify it explicitly, for example:" >&2
  echo >&2
  echo "  UNITY_BIN=\"$HOME/Unity/Hub/Editor/$UNITY_VERSION/Editor/Unity\" \\" >&2
  echo "  ./Tools/build_linux.sh" >&2
  exit 1
fi

if [[ ! -x "$UNITY_BIN" ]]; then
  echo "Unity executable is not executable: $UNITY_BIN" >&2
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required:" >&2
  echo "  sudo apt install rsync" >&2
  exit 1
fi

if ! [[ "$UNITY_JOB_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "UNITY_JOB_WORKERS must be a positive integer: $UNITY_JOB_WORKERS" >&2
  exit 2
fi

require_directory "$PROJECT_DIR/Assets"
require_directory "$PROJECT_DIR/Packages"
require_directory "$PROJECT_DIR/ProjectSettings"
require_file "$QA_WORKBOOK"
require_file "$QA_REGENERATOR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to synchronize the QA workbook." >&2
  exit 1
fi

echo "Synchronizing runtime QA from QAs_v5_d.xlsx..."
python3 "$QA_REGENERATOR" \
  --workbook "$QA_WORKBOOK" \
  --templates "$QA_SOURCE" \
  --sync-templates-only
require_file "$QA_SOURCE"

mkdir -p \
  "$PROJECT_DIR/Build" \
  "$LIGHT_PROJECT/Assets" \
  "$LIGHT_PROJECT/Packages" \
  "$LIGHT_PROJECT/ProjectSettings"

echo "Preparing lightweight Linux Player build project..."
echo "  source project : $PROJECT_DIR"
echo "  light project  : $LIGHT_PROJECT"
echo "  Unity          : $UNITY_BIN"
echo "  schema         : $SCHEMA_VERSION"
echo "  Unity workers  : $UNITY_JOB_WORKERS"
echo

# 只同步构建 Player 所需的 Unity 源工程内容。
# 不复制原始外部模型、导入模型生成结果、运行输出和其他项目根目录内容。
#
# .LightBuildProject/Library 会保留，用于加速后续重复构建。
rsync -a --delete \
  --exclude '/RawModels/' \
  --exclude '/ModelPacks/' \
  --exclude '/Resources/ImportedProps/' \
  --exclude '/Resources/ImportedProps.zip' \
  "$PROJECT_DIR/Assets/" \
  "$LIGHT_PROJECT/Assets/"

rsync -a --delete \
  "$PROJECT_DIR/Packages/" \
  "$LIGHT_PROJECT/Packages/"

rsync -a --delete \
  "$PROJECT_DIR/ProjectSettings/" \
  "$LIGHT_PROJECT/ProjectSettings/"

# 清理旧版本构建脚本可能复制到轻量工程根目录的其他内容。
# 保留 Library 缓存，以及本次同步的三个 Unity 输入目录。
find "$LIGHT_PROJECT" \
  -mindepth 1 \
  -maxdepth 1 \
  ! -name 'Assets' \
  ! -name 'Packages' \
  ! -name 'ProjectSettings' \
  ! -name 'Library' \
  ! -name 'Build' \
  -exec rm -rf -- {} +

rm -rf "$LIGHT_BUILD_DIR" "$BUILD_DIR"
mkdir -p "$LIGHT_PROJECT/Build" "$PROJECT_DIR/Build"

echo "Building Linux Player..."
echo "  log: $LOG_FILE"
echo

set +e
"$UNITY_BIN" \
  -batchmode \
  -nographics \
  -quit \
  -accept-apiupdate \
  -job-worker-count "$UNITY_JOB_WORKERS" \
  -projectPath "$LIGHT_PROJECT" \
  -executeMethod BatchExperimentBuild.BuildLinux \
  -logFile "$LOG_FILE"
build_status=$?
set -e

if [[ "$build_status" -ne 0 ]]; then
  echo >&2
  echo "Unity Linux build failed with exit code $build_status." >&2
  echo "Compiler/build errors:" >&2

  grep -nE \
    'error CS[0-9]+|Scripts have compiler errors|Build failed|Exception:|executeMethod method.*not found' \
    "$LOG_FILE" |
    tail -n 120 || true

  echo >&2
  echo "Last 120 log lines:" >&2
  tail -n 120 "$LOG_FILE" 2>/dev/null || true

  exit "$build_status"
fi

if [[ ! -f "$LIGHT_BUILD_DIR/ChangeBlindnessRoom.x86_64" ]]; then
  echo "Unity exited successfully, but the Linux Player was not created:" >&2
  echo "  $LIGHT_BUILD_DIR/ChangeBlindnessRoom.x86_64" >&2
  tail -n 120 "$LOG_FILE" 2>/dev/null || true
  exit 1
fi

mkdir -p "$BUILD_DIR"
cp -a "$LIGHT_BUILD_DIR/." "$BUILD_DIR/"

if [[ ! -f "$PLAYER" ]]; then
  echo "Failed to copy the Linux Player to: $PLAYER" >&2
  exit 1
fi

chmod +x "$PLAYER"

# 确保已构建 Player 使用项目当前最新的英文问答库。
mkdir -p "$(dirname "$QA_DEST")"
cp -f "$QA_SOURCE" "$QA_DEST"

printf '%s\n' \
  "$SCHEMA_VERSION" \
  > "$BUILD_DIR/dataset_schema_version.txt"

{
  printf 'Built at: %s\n' "$(date --iso-8601=seconds)"
  printf 'Unity: %s\n' "$UNITY_BIN"
  printf 'Unity version: %s\n' "$UNITY_VERSION"
  printf 'Schema: %s\n' "$SCHEMA_VERSION"
  printf 'Build mode: lightweight model-free mirror\n'
  printf 'Source project: %s\n' "$PROJECT_DIR"
  printf 'Light project: %s\n' "$LIGHT_PROJECT"
} > "$BUILD_DIR/build_info.txt"

if [[ ! -f "$PROJECT_DIR/ModelBundles/prop_manifest.json" ]]; then
  echo
  echo "Warning: Linux Player was built successfully, but this file is missing:"
  echo "  $PROJECT_DIR/ModelBundles/prop_manifest.json"
  echo "Prepare ModelBundles before running the dataset generator."
fi

echo
echo "Linux Player build completed."
echo "  Player : $PLAYER"
echo "  Data   : $BUILD_DIR/ChangeBlindnessRoom_Data"
echo "  Schema : $BUILD_DIR/dataset_schema_version.txt"
echo "  QA JSON: $QA_DEST"
echo "  Log    : $LOG_FILE"
echo
echo "RawModels, ModelPacks and ImportedProps were not imported into the"
echo "lightweight build project."
