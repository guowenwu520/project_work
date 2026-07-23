#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class ModelBundleBuilder
{
    public const string SourceRoot = "Assets/ModelPacks/Generated";
    public const string LegacyRoot = "Assets/Resources/ImportedProps/Generated";
    public const string RuntimeFolderName = "ModelBundles";
    private const int ModelsPerPack = 10;
    private const string BundleVersion = "unity-assetbundle-v2-full-rebuild";

    [MenuItem("Tools/Change Blindness/Model Packs/1. Migrate Existing Generated Prefabs")]
    public static void MigrateExistingGeneratedPrefabs()
    {
        EnsureAssetFolder(SourceRoot);
        if (!AssetDatabase.IsValidFolder(LegacyRoot))
        {
            EditorUtility.DisplayDialog("迁移模型", "没有找到旧目录：" + LegacyRoot, "确定");
            return;
        }

        string[] guids = AssetDatabase.FindAssets("t:Prefab", new[] { LegacyRoot });
        int moved = 0;
        int skipped = 0;
        foreach (string guid in guids)
        {
            string sourcePath = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrWhiteSpace(sourcePath))
            {
                continue;
            }

            string fileName = Path.GetFileName(sourcePath);
            string destinationPath = SourceRoot + "/" + fileName;
            if (AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(destinationPath) != null)
            {
                skipped++;
                continue;
            }

            string error = AssetDatabase.MoveAsset(sourcePath, destinationPath);
            if (string.IsNullOrEmpty(error))
            {
                moved++;
            }
            else
            {
                Debug.LogWarning("模型 Prefab 迁移失败：" + sourcePath + " -> " + destinationPath + "，" + error);
            }
        }

        string legacyManifest = "Assets/Resources/ImportedProps/prop_manifest.json";
        if (AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(legacyManifest) != null)
        {
            AssetDatabase.DeleteAsset(legacyManifest);
        }

        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
        EditorUtility.DisplayDialog(
            "迁移完成",
            "已移动 " + moved + " 个 Prefab 到：\n" + SourceRoot +
            (skipped > 0 ? "\n跳过已存在的 " + skipped + " 个同名 Prefab。" : string.Empty) +
            "\n\n下一步执行：Rebuild Linux Model Packs。",
            "确定");
    }

    [MenuItem("Tools/Change Blindness/Model Packs/2. Rebuild Linux Model Packs")]
    public static void RebuildLinuxModelPacksFromMenu()
    {
        try
        {
            BuildCurrentPrefabs(showDialog: true);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            EditorUtility.DisplayDialog("模型包构建失败", exception.Message, "确定");
        }
    }

    // Kept for compatibility with older buttons and scripts.
    public static void BuildOrUpdateLinuxModelPacks()
    {
        RebuildLinuxModelPacksFromMenu();
    }

    /// <summary>
    /// Rebuilds ModelBundles entirely from the current prefabs under Assets/ModelPacks/Generated.
    /// Stale manifest entries and deleted models are therefore removed automatically.
    /// </summary>
    public static int BuildCurrentPrefabs(bool showDialog)
    {
        EnsureAssetFolder(SourceRoot);
        string[] prefabPaths = AssetDatabase.FindAssets("t:Prefab", new[] { SourceRoot })
            .Select(AssetDatabase.GUIDToAssetPath)
            .Where(path => !string.IsNullOrWhiteSpace(path) && path.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        var validPaths = new List<string>();
        foreach (string path in prefabPaths)
        {
            GameObject prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (prefab == null)
            {
                Debug.LogWarning("跳过无法加载的 Prefab：" + path);
                continue;
            }

            if (!ImportedPropLibrary.IsUsablePrefab(prefab, out string reason))
            {
                Debug.LogWarning("跳过无效 Prefab：" + path + "，" + reason);
                continue;
            }

            validPaths.Add(path);
        }

        string projectRoot = Directory.GetParent(Application.dataPath).FullName;
        string outputRoot = Path.Combine(projectRoot, RuntimeFolderName);
        string tempRoot = Path.Combine(projectRoot, "Build", "ModelBundleTemp");
        Directory.CreateDirectory(outputRoot);
        RecreateDirectory(tempRoot);

        var manifest = new ImportedPropManifestData
        {
            version = BundleVersion,
            props = new List<ImportedPropManifestEntry>()
        };

        if (validPaths.Count == 0)
        {
            DeleteOldRuntimeFiles(outputRoot);
            WriteManifest(outputRoot, manifest);
            ImportedPropLibrary.ClearCache();

            Debug.Log("当前没有可用 Prefab，已清空运行时模型包和模型清单：" + outputRoot);
            if (showDialog)
            {
                EditorUtility.DisplayDialog(
                    "模型包已清空",
                    "没有找到有效 Prefab。\n\n已清空：\n" + outputRoot +
                    "\n\n程序生成的内置简单物体仍然可以正常使用。",
                    "确定");
            }
            return 0;
        }

        var builds = new List<AssetBundleBuild>();
        var assetToBundle = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (int offset = 0, packIndex = 0; offset < validPaths.Count; offset += ModelsPerPack, packIndex++)
        {
            string[] group = validPaths.Skip(offset).Take(ModelsPerPack).ToArray();
            string bundleName = "modelpack_" + packIndex.ToString("D4") + ".bundle";
            builds.Add(new AssetBundleBuild
            {
                assetBundleName = bundleName,
                assetNames = group
            });

            foreach (string assetPath in group)
            {
                assetToBundle[assetPath] = bundleName;
            }
        }

        BuildAssetBundleOptions options =
            BuildAssetBundleOptions.ChunkBasedCompression |
            BuildAssetBundleOptions.DeterministicAssetBundle;
        AssetBundleManifest buildManifest = BuildPipeline.BuildAssetBundles(
            tempRoot,
            builds.ToArray(),
            options,
            BuildTarget.StandaloneLinux64);

        if (buildManifest == null)
        {
            throw new InvalidOperationException("BuildPipeline.BuildAssetBundles 返回空结果，请查看 Console。");
        }

        foreach (string assetPath in validPaths)
        {
            GameObject prefab = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            string propName = prefab != null ? prefab.name.Trim() : Path.GetFileNameWithoutExtension(assetPath);
            manifest.props.Add(new ImportedPropManifestEntry
            {
                name = propName,
                displayName = propName,
                bundleFile = assetToBundle[assetPath],
                assetName = assetPath.ToLowerInvariant()
            });
        }

        manifest.props = manifest.props
            .OrderBy(item => item.name, StringComparer.OrdinalIgnoreCase)
            .ToList();

        // Replace the runtime model library only after all new bundles were built successfully.
        DeleteOldRuntimeFiles(outputRoot);
        foreach (AssetBundleBuild build in builds)
        {
            string sourceBundle = Path.Combine(tempRoot, build.assetBundleName);
            if (!File.Exists(sourceBundle))
            {
                throw new FileNotFoundException("没有生成模型包：" + sourceBundle);
            }
            File.Copy(sourceBundle, Path.Combine(outputRoot, build.assetBundleName), true);
        }
        WriteManifest(outputRoot, manifest);
        ImportedPropLibrary.ClearCache();

        Debug.Log(
            "Linux 模型包全量重建完成。Prefab：" + validPaths.Count +
            "，模型包：" + builds.Count +
            "，输出：" + outputRoot);

        if (showDialog)
        {
            EditorUtility.DisplayDialog(
                "模型包重建完成",
                "当前模型：" + validPaths.Count + " 个\n" +
                "每个模型包最多：" + ModelsPerPack + " 个\n" +
                "模型包数量：" + builds.Count + " 个\n\n" +
                "原始模型固定保留在：\n" + RawModelPreprocessor.RawModelRoot + "\n\n" +
                "调整后的 Prefab 固定保留在：\n" + SourceRoot + "\n\n" +
                "运行时模型包输出到：\n" + outputRoot + "\n\n" +
                "轻量 Player 构建会自动排除原始模型和 Prefab，不需要再移动或归档它们。",
                "确定");
        }

        return validPaths.Count;
    }

    [MenuItem("Tools/Change Blindness/Model Packs/Open ModelBundles Folder")]
    public static void OpenOutputFolder()
    {
        string outputRoot = GetOutputRoot();
        Directory.CreateDirectory(outputRoot);
        EditorUtility.RevealInFinder(outputRoot);
    }

    public static string GetOutputRoot()
    {
        string projectRoot = Directory.GetParent(Application.dataPath).FullName;
        return Path.Combine(projectRoot, RuntimeFolderName);
    }

    private static void DeleteOldRuntimeFiles(string outputRoot)
    {
        Directory.CreateDirectory(outputRoot);
        foreach (string file in Directory.GetFiles(outputRoot, "modelpack_*.bundle", SearchOption.TopDirectoryOnly))
        {
            File.Delete(file);
        }

        string manifestPath = Path.Combine(outputRoot, "prop_manifest.json");
        if (File.Exists(manifestPath))
        {
            File.Delete(manifestPath);
        }
    }

    private static void WriteManifest(string outputRoot, ImportedPropManifestData manifest)
    {
        Directory.CreateDirectory(outputRoot);
        string manifestPath = Path.Combine(outputRoot, "prop_manifest.json");
        File.WriteAllText(manifestPath, JsonUtility.ToJson(manifest, true) + "\n");
    }

    private static void RecreateDirectory(string path)
    {
        if (Directory.Exists(path))
        {
            Directory.Delete(path, true);
        }
        Directory.CreateDirectory(path);
    }

    private static void EnsureAssetFolder(string folder)
    {
        string normalized = folder.Replace('\\', '/').TrimEnd('/');
        string[] parts = normalized.Split('/');
        string current = parts[0];
        for (int i = 1; i < parts.Length; i++)
        {
            string next = current + "/" + parts[i];
            if (!AssetDatabase.IsValidFolder(next))
            {
                AssetDatabase.CreateFolder(current, parts[i]);
            }
            current = next;
        }
    }
}
#endif
