#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEngine;

public static class RawModelRenameSynchronizer
{
    private const string RawRoot = RawModelPreprocessor.RawModelRoot;
    private const string GeneratedRoot = RawModelPreprocessor.OutputPrefabRoot;

    [MenuItem("Tools/Change Blindness/Sync Renamed Raw Models")]
    public static void SyncFromMenu()
    {
        Sync(showDialog: true);
    }

    public static int Sync(bool showDialog)
    {
        if (!AssetDatabase.IsValidFolder(GeneratedRoot))
        {
            return 0;
        }

        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        List<Entry> entries = AssetDatabase.FindAssets("t:Prefab", new[] { GeneratedRoot })
            .Select(AssetDatabase.GUIDToAssetPath)
            .Select(BuildEntry)
            .ToList();

        int changed = 0;
        int deletedDuplicates = 0;
        int stale = 0;
        var report = new StringBuilder();
        report.AppendLine("RawModels 重命名同步报告");

        foreach (IGrouping<string, Entry> group in entries
                     .Where(item => !string.IsNullOrWhiteSpace(item.SourceGuid))
                     .GroupBy(item => item.SourceGuid, StringComparer.OrdinalIgnoreCase))
        {
            Entry[] sameSource = group.ToArray();
            Entry keeper = sameSource
                .OrderByDescending(item => item.ManuallyAdjusted)
                .ThenByDescending(item => PathsEqual(item.PrefabPath, item.DesiredPrefabPath))
                .ThenBy(item => item.PrefabPath, StringComparer.OrdinalIgnoreCase)
                .First();

            foreach (Entry duplicate in sameSource)
            {
                if (PathsEqual(duplicate.PrefabPath, keeper.PrefabPath))
                {
                    continue;
                }

                if (AssetDatabase.DeleteAsset(duplicate.PrefabPath))
                {
                    deletedDuplicates++;
                    changed++;
                    report.AppendLine($"[删除重复] {duplicate.PrefabPath}");
                }
            }

            string currentPath = keeper.PrefabPath;
            string desiredPath = keeper.DesiredPrefabPath;
            if (!PathsEqual(currentPath, desiredPath))
            {
                GameObject conflict = AssetDatabase.LoadAssetAtPath<GameObject>(desiredPath);
                if (conflict != null)
                {
                    Entry conflictEntry = BuildEntry(desiredPath);
                    if (string.Equals(conflictEntry.SourceGuid, keeper.SourceGuid, StringComparison.OrdinalIgnoreCase))
                    {
                        AssetDatabase.DeleteAsset(desiredPath);
                    }
                    else
                    {
                        desiredPath = AssetDatabase.GenerateUniqueAssetPath(desiredPath);
                    }
                }

                string moveError = AssetDatabase.MoveAsset(currentPath, desiredPath);
                if (string.IsNullOrEmpty(moveError))
                {
                    report.AppendLine($"[同步名称] {currentPath} -> {desiredPath}");
                    currentPath = desiredPath;
                    changed++;
                }
                else
                {
                    report.AppendLine($"[移动失败] {currentPath}：{moveError}");
                }
            }

            if (UpdateMetadata(currentPath, keeper.SourcePath, keeper.SourceGuid))
            {
                changed++;
            }
        }

        foreach (Entry entry in entries.Where(item => string.IsNullOrWhiteSpace(item.SourceGuid)))
        {
            stale++;
            report.AppendLine($"[失效项] {entry.PrefabPath}：没有找到对应的 RawModels 源模型。可手动删除此 Prefab。");
        }

        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        ImportedPropLibrary.ClearCache();

        report.AppendLine();
        report.AppendLine($"更新：{changed}，删除重复：{deletedDuplicates}，失效项：{stale}");
        Debug.Log(report.ToString());

        if (showDialog)
        {
            EditorUtility.DisplayDialog(
                "模型名称同步完成",
                $"已更新 {changed} 项，删除 {deletedDuplicates} 个重复 Prefab。\n失效 Prefab：{stale}。\n\n人工调整过的版本会优先保留。",
                "确定");
        }

        return changed;
    }

    private static Entry BuildEntry(string prefabPath)
    {
        GameObject prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
        ImportedPropPlacement placement = prefab != null ? prefab.GetComponent<ImportedPropPlacement>() : null;

        string sourcePath = ResolveSourcePath(prefabPath, placement);
        string sourceGuid = !string.IsNullOrWhiteSpace(sourcePath)
            ? AssetDatabase.AssetPathToGUID(sourcePath)
            : null;

        if (string.IsNullOrWhiteSpace(sourceGuid) && placement != null && !string.IsNullOrWhiteSpace(placement.sourceAssetGuid))
        {
            string pathFromGuid = AssetDatabase.GUIDToAssetPath(placement.sourceAssetGuid);
            if (RawModelPreprocessor.IsSupportedRawModelPath(pathFromGuid))
            {
                sourcePath = pathFromGuid;
                sourceGuid = placement.sourceAssetGuid;
            }
        }

        string desired = !string.IsNullOrWhiteSpace(sourcePath)
            ? $"{GeneratedRoot}/{BuildPrefabName(sourcePath)}.prefab"
            : prefabPath;

        return new Entry
        {
            PrefabPath = prefabPath,
            SourcePath = sourcePath,
            SourceGuid = sourceGuid,
            DesiredPrefabPath = desired,
            ManuallyAdjusted = placement != null && placement.manuallyAdjusted
        };
    }

    private static string ResolveSourcePath(string prefabPath, ImportedPropPlacement placement)
    {
        string[] dependencies = AssetDatabase.GetDependencies(prefabPath, true)
            .Where(RawModelPreprocessor.IsSupportedRawModelPath)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        if (dependencies.Length == 1)
        {
            return dependencies[0];
        }

        if (placement != null)
        {
            if (!string.IsNullOrWhiteSpace(placement.sourceAssetGuid))
            {
                string fromGuid = AssetDatabase.GUIDToAssetPath(placement.sourceAssetGuid);
                if (RawModelPreprocessor.IsSupportedRawModelPath(fromGuid))
                {
                    return fromGuid;
                }
            }

            if (RawModelPreprocessor.IsSupportedRawModelPath(placement.sourceAssetPath) &&
                !string.IsNullOrWhiteSpace(AssetDatabase.AssetPathToGUID(placement.sourceAssetPath)))
            {
                return placement.sourceAssetPath;
            }
        }

        return dependencies.FirstOrDefault();
    }

    private static bool UpdateMetadata(string prefabPath, string sourcePath, string sourceGuid)
    {
        if (string.IsNullOrWhiteSpace(prefabPath) || string.IsNullOrWhiteSpace(sourcePath))
        {
            return false;
        }

        GameObject root = null;
        try
        {
            root = PrefabUtility.LoadPrefabContents(prefabPath);
            if (root == null)
            {
                return false;
            }

            ImportedPropPlacement placement = root.GetComponent<ImportedPropPlacement>();
            if (placement == null)
            {
                placement = root.AddComponent<ImportedPropPlacement>();
            }

            bool changed = !string.Equals(placement.sourceAssetPath, sourcePath, StringComparison.OrdinalIgnoreCase) ||
                           !string.Equals(placement.sourceAssetGuid, sourceGuid, StringComparison.OrdinalIgnoreCase);
            placement.sourceAssetPath = sourcePath;
            placement.sourceAssetGuid = sourceGuid;
            placement.preserveAuthoredPlacement = true;

            string newName = Path.GetFileNameWithoutExtension(prefabPath);
            if (!string.Equals(root.name, newName, StringComparison.Ordinal))
            {
                root.name = newName;
                changed = true;
            }

            Transform visual = FindVisual(root.transform);
            if (visual != null)
            {
                string visualName = newName + "_Visual";
                if (!string.Equals(visual.name, visualName, StringComparison.Ordinal))
                {
                    visual.name = visualName;
                    changed = true;
                }
            }

            if (changed)
            {
                PrefabUtility.SaveAsPrefabAsset(root, prefabPath);
            }

            return changed;
        }
        finally
        {
            if (root != null)
            {
                PrefabUtility.UnloadPrefabContents(root);
            }
        }
    }

    private static Transform FindVisual(Transform root)
    {
        for (int i = 0; i < root.childCount; i++)
        {
            Transform child = root.GetChild(i);
            if (child.name.EndsWith("_Visual", StringComparison.OrdinalIgnoreCase))
            {
                return child;
            }
        }

        return root.childCount > 0 ? root.GetChild(0) : null;
    }

    private static string BuildPrefabName(string assetPath)
    {
        string normalized = assetPath.Replace('\\', '/');
        string relative = normalized.StartsWith(RawRoot + "/", StringComparison.OrdinalIgnoreCase)
            ? normalized.Substring(RawRoot.Length + 1)
            : Path.GetFileName(normalized);
        string extension = Path.GetExtension(relative);
        string withoutExtension = relative.Substring(0, relative.Length - extension.Length);
        return Sanitize(withoutExtension.Replace("/", "__"));
    }

    private static string Sanitize(string value)
    {
        var builder = new StringBuilder(value.Length);
        foreach (char c in value.Trim())
        {
            builder.Append(char.IsLetterOrDigit(c) || c == '_' || c == '-' ? c : '_');
        }

        string result = builder.ToString().Trim('_');
        return string.IsNullOrWhiteSpace(result) ? "ImportedModel" : result;
    }

    private static bool PathsEqual(string a, string b)
    {
        return string.Equals(a?.Replace('\\', '/'), b?.Replace('\\', '/'), StringComparison.OrdinalIgnoreCase);
    }

    private sealed class Entry
    {
        public string PrefabPath;
        public string SourcePath;
        public string SourceGuid;
        public string DesiredPrefabPath;
        public bool ManuallyAdjusted;
    }
}
#endif
