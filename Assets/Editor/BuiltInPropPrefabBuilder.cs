#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEngine;

public static class BuiltInPropPrefabBuilder
{
    public const string OutputRoot = "Assets/Resources/BuiltInProps/Generated";
    public const string DataRoot = "Assets/Resources/BuiltInProps/Data";

    [MenuItem("Tools/Change Blindness/Built-in Props/Generate Missing 40 Built-in Prefabs")]
    public static void GenerateMissingFromMenu()
    {
        int generated = EnsureBuiltInPrefabs(showDialog: false, overwriteExisting: false);
        EditorUtility.DisplayDialog(
            "内置物体",
            "已补全内置物体 Prefab。生成完成后会自动出现在摆放调整窗口。\n\n本次新生成：" + generated +
            "\n总目录：" + OutputRoot +
            "\n\n现在可以打开摆放调整窗口逐个修改。",
            "确定");
    }

    [MenuItem("Tools/Change Blindness/Built-in Props/Rebuild All 40 Built-in Prefabs")]
    public static void RebuildAllFromMenu()
    {
        bool confirmed = EditorUtility.DisplayDialog(
            "重建全部内置物体",
            "这会覆盖 40 个内置物体当前保存的手工姿态。\n\n确定继续吗？",
            "重建全部",
            "取消");
        if (!confirmed)
        {
            return;
        }

        int generated = EnsureBuiltInPrefabs(showDialog: false, overwriteExisting: true);
        EditorUtility.DisplayDialog(
            "内置物体",
            "已重建 " + generated + " 个内置物体 Prefab。",
            "确定");
    }

    public static int EnsureBuiltInPrefabs(bool showDialog, bool overwriteExisting)
    {
        EnsureAssetFolder(OutputRoot);
        EnsureAssetFolder(DataRoot);

        int generated = 0;
        try
        {
            for (int index = 0; index < BuiltInPropCatalog.Names.Length; index++)
            {
                string propName = BuiltInPropCatalog.Names[index];
                string prefabPath = GetPrefabPath(propName);
                if (!overwriteExisting && AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath) != null)
                {
                    continue;
                }

                EditorUtility.DisplayProgressBar(
                    "生成内置物体 Prefab",
                    propName + " (" + (index + 1) + "/" + BuiltInPropCatalog.Names.Length + ")",
                    (index + 1f) / BuiltInPropCatalog.Names.Length);

                RebuildOneInternal(propName);
                generated++;
            }
        }
        finally
        {
            EditorUtility.ClearProgressBar();
            AssetDatabase.SaveAssets();
        }

        if (showDialog)
        {
            EditorUtility.DisplayDialog(
                "内置物体",
                "已生成/更新 " + generated + " 个内置物体。\n\n目录：" + OutputRoot,
                "确定");
        }

        return generated;
    }

    public static void RebuildOne(string propName)
    {
        if (!BuiltInPropCatalog.IsBuiltIn(propName))
        {
            throw new ArgumentException("不是内置物体：" + propName, nameof(propName));
        }

        try
        {
            EditorUtility.DisplayProgressBar("重置内置物体", propName, 0.5f);
            RebuildOneInternal(propName);
            AssetDatabase.SaveAssets();
        }
        finally
        {
            EditorUtility.ClearProgressBar();
        }
    }

    public static bool IsBuiltInPrefabPath(string assetPath)
    {
        if (string.IsNullOrWhiteSpace(assetPath))
        {
            return false;
        }

        string normalized = assetPath.Replace('\\', '/');
        return normalized.StartsWith(OutputRoot + "/", StringComparison.OrdinalIgnoreCase);
    }

    public static string GetPrefabPath(string propName)
    {
        return OutputRoot + "/" + propName + ".prefab";
    }

    private static void RebuildOneInternal(string propName)
    {
        EnsureAssetFolder(OutputRoot);
        EnsureAssetFolder(DataRoot);

        string prefabPath = GetPrefabPath(propName);
        string propDataFolder = DataRoot + "/" + propName;

        DeleteAssetIfExists(prefabPath);
        DeleteAssetIfExists(propDataFolder);
        EnsureAssetFolder(propDataFolder);

        GameObject wrapper = null;
        try
        {
            wrapper = new GameObject(propName);

            ImportedPropPlacement placement = wrapper.AddComponent<ImportedPropPlacement>();
            placement.preserveAuthoredPlacement = true;
            placement.manuallyAdjusted = false;
            placement.sourceAssetPath = string.Empty;
            placement.sourceAssetGuid = string.Empty;

            BuiltInPropMarker marker = wrapper.AddComponent<BuiltInPropMarker>();
            marker.propName = propName;

            MaterialLibrary materialLibrary = new MaterialLibrary(StableSeed(propName));
            ProceduralPropFactory factory = new ProceduralPropFactory(materialLibrary, StableSeed(propName));
            GameObject visual = factory.CreateRaw(propName, wrapper.transform, Vector3.zero, 0f);
            visual.name = propName + "_Visual";
            visual.transform.localPosition = Vector3.zero;
            visual.transform.localRotation = Quaternion.identity;
            visual.transform.localScale = Vector3.one;

            RemoveContactShadow(visual.transform);
            RemoveColliders(visual);
            PersistMeshesAndMaterials(visual, propDataFolder, propName);
            ConfigureRenderers(visual);

            GameObject saved = PrefabUtility.SaveAsPrefabAsset(wrapper, prefabPath, out bool success);
            if (!success || saved == null)
            {
                throw new InvalidOperationException("无法保存内置物体 Prefab：" + prefabPath);
            }
        }
        finally
        {
            if (wrapper != null)
            {
                UnityEngine.Object.DestroyImmediate(wrapper);
            }
        }
    }

    private static void PersistMeshesAndMaterials(GameObject root, string dataFolder, string propName)
    {
        int meshIndex = 0;
        foreach (MeshFilter filter in root.GetComponentsInChildren<MeshFilter>(true))
        {
            if (filter == null || filter.sharedMesh == null)
            {
                continue;
            }

            Mesh copy = UnityEngine.Object.Instantiate(filter.sharedMesh);
            copy.name = propName + "_" + SafeName(filter.gameObject.name) + "_Mesh";
            string meshPath = AssetDatabase.GenerateUniqueAssetPath(
                dataFolder + "/mesh_" + meshIndex.ToString("D2") + "_" + SafeName(filter.gameObject.name) + ".asset");
            AssetDatabase.CreateAsset(copy, meshPath);
            filter.sharedMesh = copy;
            meshIndex++;
        }

        Dictionary<Material, Material> materialCopies = new Dictionary<Material, Material>();
        int materialIndex = 0;
        foreach (Renderer renderer in root.GetComponentsInChildren<Renderer>(true))
        {
            if (renderer == null)
            {
                continue;
            }

            Material[] sourceMaterials = renderer.sharedMaterials;
            Material[] savedMaterials = new Material[sourceMaterials.Length];
            for (int index = 0; index < sourceMaterials.Length; index++)
            {
                Material source = sourceMaterials[index];
                if (source == null)
                {
                    savedMaterials[index] = null;
                    continue;
                }

                if (!materialCopies.TryGetValue(source, out Material savedMaterial) || savedMaterial == null)
                {
                    savedMaterial = new Material(source)
                    {
                        name = propName + "_Material_" + materialIndex.ToString("D2")
                    };
                    string materialPath = AssetDatabase.GenerateUniqueAssetPath(
                        dataFolder + "/material_" + materialIndex.ToString("D2") + ".mat");
                    AssetDatabase.CreateAsset(savedMaterial, materialPath);
                    materialCopies[source] = savedMaterial;
                    materialIndex++;
                }

                savedMaterials[index] = savedMaterial;
            }
            renderer.sharedMaterials = savedMaterials;
        }
    }

    private static void RemoveContactShadow(Transform root)
    {
        if (root == null)
        {
            return;
        }

        List<GameObject> toDelete = new List<GameObject>();
        foreach (Transform child in root.GetComponentsInChildren<Transform>(true))
        {
            if (child != root && string.Equals(child.name, "ContactShadow", StringComparison.OrdinalIgnoreCase))
            {
                toDelete.Add(child.gameObject);
            }
        }

        foreach (GameObject item in toDelete)
        {
            UnityEngine.Object.DestroyImmediate(item);
        }
    }

    private static void RemoveColliders(GameObject root)
    {
        foreach (Collider collider in root.GetComponentsInChildren<Collider>(true))
        {
            UnityEngine.Object.DestroyImmediate(collider);
        }
    }

    private static void ConfigureRenderers(GameObject root)
    {
        foreach (Renderer renderer in root.GetComponentsInChildren<Renderer>(true))
        {
            renderer.enabled = true;
            renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On;
            renderer.receiveShadows = true;
            renderer.lightProbeUsage = UnityEngine.Rendering.LightProbeUsage.BlendProbes;
            renderer.reflectionProbeUsage = UnityEngine.Rendering.ReflectionProbeUsage.BlendProbes;
        }
    }

    private static int StableSeed(string value)
    {
        unchecked
        {
            int hash = 17;
            foreach (char character in value ?? string.Empty)
            {
                hash = hash * 31 + character;
            }
            return hash;
        }
    }

    private static string SafeName(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "item";
        }

        foreach (char invalid in Path.GetInvalidFileNameChars())
        {
            value = value.Replace(invalid, '_');
        }
        return value.Replace(' ', '_');
    }

    private static void DeleteAssetIfExists(string assetPath)
    {
        if (AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(assetPath) != null ||
            AssetDatabase.IsValidFolder(assetPath))
        {
            AssetDatabase.DeleteAsset(assetPath);
        }
    }

    private static void EnsureAssetFolder(string folder)
    {
        string normalized = folder.Replace('\\', '/').TrimEnd('/');
        string[] parts = normalized.Split('/');
        string current = parts[0];
        for (int index = 1; index < parts.Length; index++)
        {
            string next = current + "/" + parts[index];
            if (!AssetDatabase.IsValidFolder(next))
            {
                AssetDatabase.CreateFolder(current, parts[index]);
            }
            current = next;
        }
    }
}

public sealed class BuiltInPropBuildPreprocessor : IPreprocessBuildWithReport
{
    public int callbackOrder => -1000;

    public void OnPreprocessBuild(BuildReport report)
    {
        BuiltInPropPrefabBuilder.EnsureBuiltInPrefabs(showDialog: false, overwriteExisting: false);
    }
}
#endif
