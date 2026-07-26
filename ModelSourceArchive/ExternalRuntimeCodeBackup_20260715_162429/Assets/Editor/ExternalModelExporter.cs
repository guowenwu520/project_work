#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class ExternalModelExporter
{
    private const string GeneratedRoot = "Assets/Resources/ImportedProps/Generated";

    [MenuItem("Tools/Change Blindness/External Models/Export Current GLB Models + Placement")]
    public static void ExportCurrentModels()
    {
        string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
        string externalRoot = Path.Combine(projectRoot, "ExternalModels");
        string modelRoot = Path.Combine(externalRoot, "models");
        string manifestPath = Path.Combine(externalRoot, "prop_manifest.json");
        Directory.CreateDirectory(modelRoot);

        ExternalPropManifestData existing = LoadExistingManifest(manifestPath);
        var existingLookup = new Dictionary<string, ExternalPropManifestEntry>(StringComparer.OrdinalIgnoreCase);
        if (existing.props != null)
        {
            foreach (ExternalPropManifestEntry item in existing.props)
            {
                if (item != null && !string.IsNullOrWhiteSpace(item.name))
                {
                    existingLookup[item.name.Trim()] = item;
                }
            }
        }

        string[] prefabGuids = AssetDatabase.FindAssets("t:Prefab", new[] { GeneratedRoot });
        var exported = new List<ExternalPropManifestEntry>();
        var skipped = new List<string>();

        try
        {
            for (int i = 0; i < prefabGuids.Length; i++)
            {
                string prefabPath = AssetDatabase.GUIDToAssetPath(prefabGuids[i]);
                GameObject prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                if (prefab == null)
                {
                    continue;
                }

                EditorUtility.DisplayProgressBar(
                    "导出外部 GLB 模型",
                    prefab.name + " (" + (i + 1) + "/" + prefabGuids.Length + ")",
                    prefabGuids.Length == 0 ? 1f : i / (float)prefabGuids.Length);

                ImportedPropPlacement placement = prefab.GetComponent<ImportedPropPlacement>();
                string sourceAssetPath = ResolveSourceAssetPath(placement);
                if (string.IsNullOrWhiteSpace(sourceAssetPath))
                {
                    skipped.Add(prefab.name + "：找不到原始模型路径");
                    continue;
                }

                string extension = Path.GetExtension(sourceAssetPath);
                if (!string.Equals(extension, ".glb", StringComparison.OrdinalIgnoreCase))
                {
                    skipped.Add(prefab.name + "：源文件是 " + extension + "，请先转换为 GLB");
                    continue;
                }

                string sourceFullPath = Path.GetFullPath(Path.Combine(projectRoot, sourceAssetPath));
                if (!File.Exists(sourceFullPath))
                {
                    skipped.Add(prefab.name + "：源文件不存在 " + sourceAssetPath);
                    continue;
                }

                string safeName = MakeSafeFileName(prefab.name);
                string destinationFileName = safeName + ".glb";
                string destinationFullPath = Path.Combine(modelRoot, destinationFileName);
                File.Copy(sourceFullPath, destinationFullPath, true);

                Transform visual = FindEditableVisual(prefab.transform);
                ExternalPropManifestEntry entry = existingLookup.TryGetValue(prefab.name, out ExternalPropManifestEntry old)
                    ? old
                    : new ExternalPropManifestEntry();
                entry.name = prefab.name.Trim();
                entry.displayName = prefab.name.Trim();
                entry.file = "models/" + destinationFileName;
                entry.manuallyAdjusted = placement != null && placement.manuallyAdjusted;

                if (visual != null)
                {
                    entry.localPosition = visual.localPosition;
                    entry.localEulerAngles = visual.localEulerAngles;
                    entry.localScale = visual.localScale;
                }
                else
                {
                    entry.localPosition = Vector3.zero;
                    entry.localEulerAngles = Vector3.zero;
                    entry.localScale = Vector3.one;
                    entry.manuallyAdjusted = false;
                }

                exported.Add(entry);
            }
        }
        finally
        {
            EditorUtility.ClearProgressBar();
        }

        exported.Sort((a, b) => string.Compare(a.name, b.name, StringComparison.OrdinalIgnoreCase));
        ExternalPropManifestData result = new ExternalPropManifestData
        {
            version = "external-glb-v1",
            props = exported
        };
        File.WriteAllText(manifestPath, JsonUtility.ToJson(result, true) + "\n");

        ExternalPropLibrary.ClearCache();
        string skippedMessage = skipped.Count == 0
            ? "无"
            : string.Join("\n", skipped.Take(12).ToArray()) + (skipped.Count > 12 ? "\n……" : string.Empty);
        EditorUtility.DisplayDialog(
            "外部模型导出完成",
            "已导出：" + exported.Count + " 个 GLB\n" +
            "跳过：" + skipped.Count + " 个\n\n" +
            "目录：" + externalRoot + "\n\n" +
            "跳过详情：\n" + skippedMessage + "\n\n" +
            "确认 ExternalModels 后，关闭 Unity，再运行 Tools/detach_embedded_models.sh。",
            "确定");
    }

    [MenuItem("Tools/Change Blindness/External Models/Open ExternalModels Folder")]
    public static void OpenExternalFolder()
    {
        string root = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "ExternalModels"));
        Directory.CreateDirectory(Path.Combine(root, "models"));
        EditorUtility.RevealInFinder(root);
    }

    private static ExternalPropManifestData LoadExistingManifest(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                ExternalPropManifestData data = JsonUtility.FromJson<ExternalPropManifestData>(File.ReadAllText(path));
                if (data != null)
                {
                    return data;
                }
            }
        }
        catch (Exception exception)
        {
            Debug.LogWarning("读取旧 ExternalModels 清单失败，将重新创建：" + exception.Message);
        }
        return new ExternalPropManifestData();
    }

    private static string ResolveSourceAssetPath(ImportedPropPlacement placement)
    {
        if (placement == null)
        {
            return null;
        }

        if (!string.IsNullOrWhiteSpace(placement.sourceAssetGuid))
        {
            string path = AssetDatabase.GUIDToAssetPath(placement.sourceAssetGuid);
            if (!string.IsNullOrWhiteSpace(path))
            {
                return path;
            }
        }
        return placement.sourceAssetPath;
    }

    private static Transform FindEditableVisual(Transform root)
    {
        if (root == null)
        {
            return null;
        }

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

    private static string MakeSafeFileName(string value)
    {
        string result = value ?? "model";
        foreach (char invalid in Path.GetInvalidFileNameChars())
        {
            result = result.Replace(invalid, '_');
        }
        result = result.Trim();
        return string.IsNullOrWhiteSpace(result) ? "model" : result;
    }
}
#endif
