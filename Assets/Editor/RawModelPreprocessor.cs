#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

public static class RawModelPreprocessor
{
    public const string RawModelRoot = "Assets/RawModels";
    public const string OutputPrefabRoot = "Assets/ModelPacks/Generated";

    private static readonly HashSet<string> SupportedExtensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
    {
        ".fbx", ".obj", ".glb", ".gltf"
    };

    private static bool isProcessing;

    public static bool IsProcessing => isProcessing;

    [MenuItem("Tools/Change Blindness/Preprocess Raw Models")]
    public static void ProcessAllFromMenu()
    {
        PreprocessAll(showDialog: true, throwOnFailure: false);
    }

    [MenuItem("Tools/Change Blindness/Open Raw Model Folder")]
    public static void OpenRawModelFolder()
    {
        EnsureFolders();
        EditorUtility.RevealInFinder(Path.GetFullPath(RawModelRoot));
    }

    public static void ProcessAllFromCommandLine()
    {
        try
        {
            PreprocessAll(showDialog: false, throwOnFailure: true);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            EditorApplication.Exit(1);
            return;
        }

        EditorApplication.Exit(0);
    }

    public static void PreprocessAll(bool showDialog, bool throwOnFailure)
    {
        if (isProcessing)
        {
            return;
        }

        isProcessing = true;
        try
        {
            EnsureFolders();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            RawModelRenameSynchronizer.Sync(showDialog: false);

            List<string> modelPaths = FindRawModelPaths();
            var results = new List<PreprocessResult>();

            for (int i = 0; i < modelPaths.Count; i++)
            {
                string assetPath = modelPaths[i];
                EditorUtility.DisplayProgressBar(
                    "预处理桌面小物体",
                    $"正在处理 {Path.GetFileName(assetPath)} ({i + 1}/{modelPaths.Count})",
                    modelPaths.Count == 0 ? 1f : (float)i / modelPaths.Count);

                results.Add(ProcessOne(assetPath));
            }

            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            ImportedPropLibrary.ClearCache();

            string report = BuildReport(results);
            Debug.Log(report);

            int succeeded = results.Count(item => item.Success);
            int failed = results.Count - succeeded;
            if (showDialog)
            {
                EditorUtility.DisplayDialog(
                    "模型预处理完成",
                    $"发现 {modelPaths.Count} 个模型。\n成功生成 {succeeded} 个 Prefab，失败 {failed} 个。\n\n接下来可逐个调整模型姿态。",
                    "打开摆放调整器");
                PropPlacementWindow.OpenWindow();
            }

            if (throwOnFailure && (modelPaths.Count == 0 || failed > 0))
            {
                throw new InvalidOperationException(report);
            }
        }
        finally
        {
            EditorUtility.ClearProgressBar();
            isProcessing = false;
        }
    }

    public static bool IsSupportedRawModelPath(string assetPath)
    {
        if (string.IsNullOrWhiteSpace(assetPath))
        {
            return false;
        }

        string normalized = assetPath.Replace('\\', '/');
        if (!normalized.StartsWith(RawModelRoot + "/", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        return SupportedExtensions.Contains(Path.GetExtension(normalized));
    }

    private static PreprocessResult ProcessOne(string assetPath)
    {
        string prefabName = BuildPrefabName(assetPath);
        string outputPath = $"{OutputPrefabRoot}/{prefabName}.prefab";
        try
        {
            GameObject existingPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(outputPath);
            ImportedPropPlacement existingPlacement = existingPrefab != null
                ? existingPrefab.GetComponent<ImportedPropPlacement>()
                : null;
            if (existingPlacement != null && existingPlacement.manuallyAdjusted)
            {
                return PreprocessResult.Ok(assetPath, outputPath + "（已保留人工调整）");
            }

            AssetDatabase.ImportAsset(
                assetPath,
                ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);

            GameObject source = FindSourceGameObject(assetPath);
            if (source == null)
            {
                string extension = Path.GetExtension(assetPath);
                string hint = extension.Equals(".glb", StringComparison.OrdinalIgnoreCase) ||
                              extension.Equals(".gltf", StringComparison.OrdinalIgnoreCase)
                    ? "请确认 Packages/manifest.json 已安装 com.unity.cloud.gltfast，并检查模型是否依赖缺失。"
                    : "请检查模型文件是否损坏，或 OBJ 的 MTL/贴图是否缺失。";
                DeleteAssetIfExists(outputPath);
                return PreprocessResult.Fail(assetPath, "Unity 没有从该文件导入出可实例化的 GameObject。" + hint);
            }

            GameObject wrapper = null;
            try
            {
                wrapper = new GameObject(prefabName);
                ImportedPropPlacement placement = wrapper.AddComponent<ImportedPropPlacement>();
                placement.preserveAuthoredPlacement = true;
                placement.manuallyAdjusted = false;
                placement.sourceAssetPath = assetPath;
                placement.sourceAssetGuid = AssetDatabase.AssetPathToGUID(assetPath);

                GameObject visual = InstantiateSource(source);
                if (visual == null)
                {
                    DeleteAssetIfExists(outputPath);
                    return PreprocessResult.Fail(assetPath, "模型实例化失败。");
                }

                visual.name = prefabName + "_Visual";
                visual.transform.SetParent(wrapper.transform, true);
                RemoveUnsupportedComponents(visual);
                ConfigureRenderers(visual);

                if (!NormalizeModel(wrapper.transform, visual.transform, out string boundsError))
                {
                    DeleteAssetIfExists(outputPath);
                    return PreprocessResult.Fail(assetPath, boundsError);
                }

                GameObject saved = PrefabUtility.SaveAsPrefabAsset(wrapper, outputPath, out bool success);
                if (!success || saved == null)
                {
                    DeleteAssetIfExists(outputPath);
                    return PreprocessResult.Fail(assetPath, $"无法写入 Prefab：{outputPath}");
                }

                return PreprocessResult.Ok(assetPath, outputPath);
            }
            finally
            {
                if (wrapper != null)
                {
                    UnityEngine.Object.DestroyImmediate(wrapper);
                }
            }
        }
        catch (Exception exception)
        {
            DeleteAssetIfExists(outputPath);
            return PreprocessResult.Fail(assetPath, exception.GetType().Name + ": " + exception.Message);
        }
    }

    private static GameObject FindSourceGameObject(string assetPath)
    {
        GameObject main = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
        if (main != null && ContainsUsableMesh(main))
        {
            return main;
        }

        UnityEngine.Object[] allAssets = AssetDatabase.LoadAllAssetsAtPath(assetPath);
        return allAssets
            .OfType<GameObject>()
            .Where(ContainsUsableMesh)
            .OrderByDescending(CountUsableRenderers)
            .FirstOrDefault();
    }

    private static GameObject InstantiateSource(GameObject source)
    {
        GameObject instance = PrefabUtility.InstantiatePrefab(source) as GameObject;
        if (instance == null)
        {
            instance = UnityEngine.Object.Instantiate(source);
        }

        if (instance == null)
        {
            return null;
        }

        instance.transform.position = Vector3.zero;
        instance.transform.rotation = Quaternion.identity;
        instance.transform.localScale = Vector3.one;
        return instance;
    }

    private static void RemoveUnsupportedComponents(GameObject root)
    {
        foreach (Camera camera in root.GetComponentsInChildren<Camera>(true))
        {
            UnityEngine.Object.DestroyImmediate(camera);
        }

        foreach (Light light in root.GetComponentsInChildren<Light>(true))
        {
            UnityEngine.Object.DestroyImmediate(light);
        }

        foreach (AudioListener listener in root.GetComponentsInChildren<AudioListener>(true))
        {
            UnityEngine.Object.DestroyImmediate(listener);
        }

        foreach (AudioSource source in root.GetComponentsInChildren<AudioSource>(true))
        {
            UnityEngine.Object.DestroyImmediate(source);
        }

        foreach (Rigidbody rigidbody in root.GetComponentsInChildren<Rigidbody>(true))
        {
            UnityEngine.Object.DestroyImmediate(rigidbody);
        }

        foreach (Collider collider in root.GetComponentsInChildren<Collider>(true))
        {
            UnityEngine.Object.DestroyImmediate(collider);
        }

        foreach (Animator animator in root.GetComponentsInChildren<Animator>(true))
        {
            animator.enabled = false;
        }

        foreach (Animation animation in root.GetComponentsInChildren<Animation>(true))
        {
            animation.enabled = false;
        }
    }

    private static void ConfigureRenderers(GameObject root)
    {
        foreach (Renderer renderer in root.GetComponentsInChildren<Renderer>(true))
        {
            renderer.enabled = true;
            renderer.shadowCastingMode = ShadowCastingMode.On;
            renderer.receiveShadows = true;
            renderer.lightProbeUsage = LightProbeUsage.BlendProbes;
            renderer.reflectionProbeUsage = ReflectionProbeUsage.BlendProbes;
        }
    }

    private static bool NormalizeModel(Transform wrapper, Transform visual, out string error)
    {
        if (!TryCalculateBounds(visual, out Bounds bounds))
        {
            error = "模型没有可用 Mesh，无法计算包围盒。";
            return false;
        }

        // Normalize directly to the final tabletop size. The placement editor can then
        // fine-tune rotation, position and scale, and runtime preserves those values.
        const float targetMaxWidth = 0.30f;
        const float targetMaxDepth = 0.30f;
        const float targetMaxHeight = 0.38f;

        float scaleX = targetMaxWidth / Mathf.Max(0.000001f, bounds.size.x);
        float scaleZ = targetMaxDepth / Mathf.Max(0.000001f, bounds.size.z);
        float scaleY = targetMaxHeight / Mathf.Max(0.000001f, bounds.size.y);
        float scale = Mathf.Clamp(Mathf.Min(scaleX, scaleZ, scaleY), 0.000001f, 100000f);
        visual.localScale *= scale;

        if (!TryCalculateBounds(visual, out bounds))
        {
            error = "缩放后无法重新计算包围盒。";
            return false;
        }

        visual.position += new Vector3(
            wrapper.position.x - bounds.center.x,
            wrapper.position.y - bounds.min.y,
            wrapper.position.z - bounds.center.z);

        error = null;
        return true;
    }

    private static bool TryCalculateBounds(Transform root, out Bounds bounds)
    {
        bool initialized = false;
        bounds = new Bounds(root.position, Vector3.zero);

        foreach (MeshRenderer renderer in root.GetComponentsInChildren<MeshRenderer>(true))
        {
            MeshFilter filter = renderer.GetComponent<MeshFilter>();
            if (filter == null || filter.sharedMesh == null)
            {
                continue;
            }

            if (!initialized)
            {
                bounds = renderer.bounds;
                initialized = true;
            }
            else
            {
                bounds.Encapsulate(renderer.bounds);
            }
        }

        foreach (SkinnedMeshRenderer renderer in root.GetComponentsInChildren<SkinnedMeshRenderer>(true))
        {
            if (renderer.sharedMesh == null)
            {
                continue;
            }

            if (!initialized)
            {
                bounds = renderer.bounds;
                initialized = true;
            }
            else
            {
                bounds.Encapsulate(renderer.bounds);
            }
        }

        return initialized && bounds.size.sqrMagnitude > 0.0000000001f;
    }

    private static bool ContainsUsableMesh(GameObject root)
    {
        return CountUsableRenderers(root) > 0;
    }

    private static int CountUsableRenderers(GameObject root)
    {
        if (root == null)
        {
            return 0;
        }

        int count = root.GetComponentsInChildren<MeshFilter>(true).Count(item => item != null && item.sharedMesh != null);
        count += root.GetComponentsInChildren<SkinnedMeshRenderer>(true).Count(item => item != null && item.sharedMesh != null);
        return count;
    }

    private static List<string> FindRawModelPaths()
    {
        string absoluteRoot = Path.GetFullPath(RawModelRoot);
        if (!Directory.Exists(absoluteRoot))
        {
            return new List<string>();
        }

        return Directory
            .EnumerateFiles(absoluteRoot, "*", SearchOption.AllDirectories)
            .Where(path => SupportedExtensions.Contains(Path.GetExtension(path)))
            .Select(ToAssetPath)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    private static string ToAssetPath(string absolutePath)
    {
        string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."))
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        string fullPath = Path.GetFullPath(absolutePath);
        if (!fullPath.StartsWith(projectRoot, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("模型路径不在当前 Unity 项目中：" + fullPath);
        }

        return fullPath.Substring(projectRoot.Length).Replace('\\', '/');
    }

    private static void EnsureFolders()
    {
        EnsureAssetFolder("Assets", "RawModels");
        EnsureAssetFolder("Assets", "ModelPacks");
        EnsureAssetFolder("Assets/ModelPacks", "Generated");
    }

    private static void EnsureAssetFolder(string parent, string child)
    {
        string fullPath = parent + "/" + child;
        if (!AssetDatabase.IsValidFolder(fullPath))
        {
            AssetDatabase.CreateFolder(parent, child);
        }
    }



    private static void DeleteAssetIfExists(string assetPath)
    {
        if (!string.IsNullOrWhiteSpace(AssetDatabase.AssetPathToGUID(assetPath)))
        {
            AssetDatabase.DeleteAsset(assetPath);
        }
    }

    private static string BuildPrefabName(string assetPath)
    {
        string normalized = assetPath.Replace('\\', '/');
        string relative = normalized.StartsWith(RawModelRoot + "/", StringComparison.OrdinalIgnoreCase)
            ? normalized.Substring(RawModelRoot.Length + 1)
            : Path.GetFileName(normalized);
        string withoutExtension = relative.Substring(0, relative.Length - Path.GetExtension(relative).Length);
        return SanitizeName(withoutExtension.Replace("/", "__"));
    }

    private static string SanitizeName(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "ImportedModel";
        }

        var builder = new StringBuilder(value.Length);
        foreach (char character in value.Trim())
        {
            builder.Append(char.IsLetterOrDigit(character) || character == '_' || character == '-' ? character : '_');
        }

        string result = builder.ToString().Trim('_');
        return string.IsNullOrWhiteSpace(result) ? "ImportedModel" : result;
    }

    private static string BuildReport(List<PreprocessResult> results)
    {
        var builder = new StringBuilder();
        builder.AppendLine("原始模型预处理报告");
        builder.AppendLine($"输入目录：{RawModelRoot}");
        builder.AppendLine($"输出目录：{OutputPrefabRoot}");
        builder.AppendLine();

        if (results.Count == 0)
        {
            builder.AppendLine("没有找到 FBX、OBJ、GLB 或 glTF 文件。");
            return builder.ToString();
        }

        foreach (PreprocessResult result in results)
        {
            if (result.Success)
            {
                builder.AppendLine($"[成功] {result.SourcePath} -> {result.OutputPath}");
            }
            else
            {
                builder.AppendLine($"[失败] {result.SourcePath}：{result.Error}");
            }
        }

        return builder.ToString();
    }

    private readonly struct PreprocessResult
    {
        public bool Success { get; }
        public string SourcePath { get; }
        public string OutputPath { get; }
        public string Error { get; }

        private PreprocessResult(bool success, string sourcePath, string outputPath, string error)
        {
            Success = success;
            SourcePath = sourcePath;
            OutputPath = outputPath;
            Error = error;
        }

        public static PreprocessResult Ok(string sourcePath, string outputPath)
        {
            return new PreprocessResult(true, sourcePath, outputPath, null);
        }

        public static PreprocessResult Fail(string sourcePath, string error)
        {
            return new PreprocessResult(false, sourcePath, null, error);
        }
    }
}

public sealed class RawModelAutoPostprocessor : AssetPostprocessor
{
    private static bool queued;

    private static void OnPostprocessAllAssets(
        string[] importedAssets,
        string[] deletedAssets,
        string[] movedAssets,
        string[] movedFromAssetPaths)
    {
        if (RawModelPreprocessor.IsProcessing || queued)
        {
            return;
        }

        bool containsRawModel = importedAssets.Any(RawModelPreprocessor.IsSupportedRawModelPath) ||
                                movedAssets.Any(RawModelPreprocessor.IsSupportedRawModelPath);
        if (!containsRawModel)
        {
            return;
        }

        queued = true;
        EditorApplication.delayCall += () =>
        {
            queued = false;
            if (!EditorApplication.isCompiling && !RawModelPreprocessor.IsProcessing)
            {
                RawModelPreprocessor.PreprocessAll(showDialog: false, throwOnFailure: false);
                PropPlacementWindow.OpenWindow();
            }
        };
    }
}
#endif
