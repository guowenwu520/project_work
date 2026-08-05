#!/usr/bin/env bash
set -Eeuo pipefail

# Run from the ChangeBlindnessRoom repository root.
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$PROJECT_ROOT" ]]; then
  echo "当前目录不是Git仓库。" >&2
  exit 2
fi
cd "$PROJECT_ROOT"

echo "从Git索引移除模型和生成资源；不会删除本地文件。"

git rm -r --cached --ignore-unmatch \
  Assets/RawModels \
  Assets/ModelPacks \
  Assets/Resources/BuiltInProps/Data \
  Assets/Resources/BuiltInProps/Generated \
  Assets/Resources/ImportedProps \
  Assets/Resources/ImportedProps.zip \
  ModelBundles

git rm --cached --ignore-unmatch \
  Assets/RawModels.meta \
  Assets/ModelPacks.meta \
  Assets/Resources/BuiltInProps/Data.meta \
  Assets/Resources/BuiltInProps/Generated.meta \
  Assets/Resources/ImportedProps.meta \
  Assets/Resources/ImportedProps.zip.meta

# Re-add only lightweight model metadata that is intentionally versioned.
git add -f ModelBundles/prop_manifest.json 2>/dev/null || true
git add -f ModelBundles/README.md 2>/dev/null || true
git add -f ModelBundles/.gitkeep 2>/dev/null || true

git add .gitignore .gitattributes 2>/dev/null || true

echo
echo "当前仍被Git跟踪的疑似模型/生成资源："
tracked="$(
  git ls-files |
    grep -E '(^Assets/RawModels/|^Assets/ModelPacks/|^Assets/Resources/BuiltInProps/(Data|Generated)/|^Assets/Resources/ImportedProps/|\.(fbx|obj|glb|gltf|blend|dae|3ds|stl|ply)$)' \
    || true
)"

if [[ -n "$tracked" ]]; then
  printf '%s\n' "$tracked"
  echo
  echo "仍有文件未被清理，请检查后再提交。" >&2
  exit 1
fi

echo "没有发现仍被跟踪的模型和生成模型资源。"
echo
echo "待提交变化："
git status --short

echo
echo "如果当前只有第一次根提交，并且尚未推送，请执行："
echo "  git commit --amend --no-edit"
echo
echo "不要只创建第二个提交，否则大模型仍保留在旧提交历史中。"
