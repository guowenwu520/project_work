using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

/// <summary>
/// Loads Unity-authored prefabs from Linux AssetBundle model packs.
/// Imported source models are never loaded from Resources and are not part of the Player build.
/// </summary>
public static class ImportedPropLibrary
{
    private const string ManifestFileName = "prop_manifest.json";

    private static string configuredBundleDirectory;
    private static ImportedPropManifestData cachedManifest;
    private static Dictionary<string, ImportedPropManifestEntry> cachedEntries;
    private static readonly Dictionary<string, AssetBundle> LoadedBundles =
        new Dictionary<string, AssetBundle>(StringComparer.OrdinalIgnoreCase);
    private static readonly Dictionary<string, GameObject> LoadedPrefabs =
        new Dictionary<string, GameObject>(StringComparer.OrdinalIgnoreCase);
    private static readonly HashSet<string> ReportedInvalidPrefabs =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    private static bool manifestWarningReported;

    public static void Configure(string bundleDirectory)
    {
        string resolved = ResolveBundleDirectory(bundleDirectory);
        if (string.Equals(configuredBundleDirectory, resolved, StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        ClearCache();
        configuredBundleDirectory = resolved;
        Debug.Log("Model bundle directory: " + configuredBundleDirectory);
    }

    public static bool HasImportedProps()
    {
        return GetEntries().Count >= 1;
    }

    public static string[] GetAvailablePropNames()
    {
        List<ImportedPropManifestEntry> entries = GetEntries();
        string[] names = new string[entries.Count];
        for (int i = 0; i < entries.Count; i++)
        {
            names[i] = entries[i].name;
        }
        return names;
    }

    public static bool HasPrefab(string propName)
    {
        return !string.IsNullOrWhiteSpace(propName) &&
               GetEntryLookup().ContainsKey(propName.Trim());
    }

    public static string GetDisplayName(string propName)
    {
        if (string.IsNullOrWhiteSpace(propName))
        {
            return "item";
        }

        if (GetEntryLookup().TryGetValue(propName.Trim(), out ImportedPropManifestEntry entry) &&
            entry != null && !string.IsNullOrWhiteSpace(entry.displayName))
        {
            return entry.displayName.Trim();
        }

        return propName.Trim();
    }

    public static GameObject Create(string propName, Transform parent, Vector3 localPosition, float yawDegrees)
    {
        if (string.IsNullOrWhiteSpace(propName))
        {
            return null;
        }

        if (!GetEntryLookup().TryGetValue(propName.Trim(), out ImportedPropManifestEntry entry) || entry == null)
        {
            return null;
        }

        GameObject prefab = LoadPrefab(entry);
        if (prefab == null)
        {
            ReportInvalid(propName, "无法从模型包加载 Prefab。bundle=" + entry.bundleFile + ", asset=" + entry.assetName);
            return null;
        }

        if (!IsUsablePrefab(prefab, out string prefabReason))
        {
            ReportInvalid(prefab.name, prefabReason);
            return null;
        }

        GameObject root = new GameObject(prefab.name);
        root.transform.SetParent(parent, false);
        root.transform.localPosition = localPosition;
        root.transform.localRotation = Quaternion.Euler(0f, yawDegrees, 0f);

        GameObject instance = UnityEngine.Object.Instantiate(prefab, root.transform, false);
        instance.name = prefab.name + "_Visual";
        instance.transform.localPosition = Vector3.zero;
        instance.transform.localRotation = Quaternion.identity;
        instance.transform.localScale = Vector3.one;

        ImportedPropPlacement placement = instance.GetComponent<ImportedPropPlacement>();
        bool placed = placement != null && placement.preserveAuthoredPlacement
            ? AlignAuthoredToTabletop(root.transform, instance.transform)
            : NormalizeToTabletop(root.transform, instance.transform);

        if (!placed)
        {
            ReportInvalid(prefab.name, "实例化后没有有效 Mesh，请检查模型及其依赖资源。");
            DestroyObject(root);
            return null;
        }

        EnableShadows(instance);
        if (TryCalculateBounds(instance.transform, out Bounds finalBounds))
        {
            ContactShadowUtility.EnsureContactShadow(root.transform, finalBounds, root.transform.position.y);
        }

        return root;
    }

    public static bool IsUsablePrefab(GameObject prefab, out string reason)
    {
        if (prefab == null)
        {
            reason = "Prefab 为空。";
            return false;
        }

        foreach (MeshFilter filter in prefab.GetComponentsInChildren<MeshFilter>(true))
        {
            if (filter != null && filter.sharedMesh != null)
            {
                reason = null;
                return true;
            }
        }

        foreach (SkinnedMeshRenderer renderer in prefab.GetComponentsInChildren<SkinnedMeshRenderer>(true))
        {
            if (renderer != null && renderer.sharedMesh != null)
            {
                reason = null;
                return true;
            }
        }

        reason = "没有可用 Mesh。请检查模型依赖是否完整。";
        return false;
    }

    public static void ClearCache()
    {
        cachedManifest = null;
        cachedEntries = null;
        LoadedPrefabs.Clear();
        ReportedInvalidPrefabs.Clear();
        manifestWarningReported = false;

        foreach (AssetBundle bundle in LoadedBundles.Values)
        {
            if (bundle != null)
            {
                bundle.Unload(false);
            }
        }
        LoadedBundles.Clear();
    }

    private static GameObject LoadPrefab(ImportedPropManifestEntry entry)
    {
        if (LoadedPrefabs.TryGetValue(entry.name, out GameObject cached) && cached != null)
        {
            return cached;
        }

        string bundlePath = Path.Combine(GetBundleDirectory(), entry.bundleFile ?? string.Empty);
        bundlePath = Path.GetFullPath(bundlePath);
        if (!File.Exists(bundlePath))
        {
            ReportInvalid(entry.name, "模型包文件不存在：" + bundlePath);
            return null;
        }

        if (!LoadedBundles.TryGetValue(bundlePath, out AssetBundle bundle) || bundle == null)
        {
            bundle = AssetBundle.LoadFromFile(bundlePath);
            if (bundle == null)
            {
                ReportInvalid(entry.name, "AssetBundle.LoadFromFile 失败：" + bundlePath);
                return null;
            }
            LoadedBundles[bundlePath] = bundle;
        }

        GameObject prefab = null;
        if (!string.IsNullOrWhiteSpace(entry.assetName))
        {
            prefab = bundle.LoadAsset<GameObject>(entry.assetName);
            if (prefab == null)
            {
                prefab = bundle.LoadAsset<GameObject>(Path.GetFileNameWithoutExtension(entry.assetName));
            }
        }

        if (prefab != null)
        {
            LoadedPrefabs[entry.name] = prefab;
        }
        return prefab;
    }

    private static List<ImportedPropManifestEntry> GetEntries()
    {
        if (cachedManifest != null && cachedManifest.props != null)
        {
            return cachedManifest.props;
        }

        string manifestPath = Path.Combine(GetBundleDirectory(), ManifestFileName);
        if (!File.Exists(manifestPath))
        {
            cachedManifest = new ImportedPropManifestData();
            ReportManifestWarning(
                "没有找到模型包清单：" + manifestPath +
                "。请执行 Tools > Change Blindness > Model Packs > Build or Update Linux Model Packs。");
            return cachedManifest.props;
        }

        try
        {
            cachedManifest = JsonUtility.FromJson<ImportedPropManifestData>(File.ReadAllText(manifestPath));
        }
        catch (Exception exception)
        {
            cachedManifest = new ImportedPropManifestData();
            ReportManifestWarning("模型包清单 JSON 解析失败：" + exception.Message);
        }

        if (cachedManifest == null)
        {
            cachedManifest = new ImportedPropManifestData();
        }
        if (cachedManifest.props == null)
        {
            cachedManifest.props = new List<ImportedPropManifestEntry>();
        }

        cachedManifest.props.RemoveAll(item =>
            item == null ||
            string.IsNullOrWhiteSpace(item.name) ||
            string.IsNullOrWhiteSpace(item.bundleFile) ||
            string.IsNullOrWhiteSpace(item.assetName));
        cachedManifest.props.Sort((a, b) => string.Compare(a.name, b.name, StringComparison.OrdinalIgnoreCase));
        return cachedManifest.props;
    }

    private static Dictionary<string, ImportedPropManifestEntry> GetEntryLookup()
    {
        if (cachedEntries != null)
        {
            return cachedEntries;
        }

        cachedEntries = new Dictionary<string, ImportedPropManifestEntry>(StringComparer.OrdinalIgnoreCase);
        foreach (ImportedPropManifestEntry entry in GetEntries())
        {
            if (!cachedEntries.ContainsKey(entry.name.Trim()))
            {
                cachedEntries[entry.name.Trim()] = entry;
            }
        }
        return cachedEntries;
    }

    private static string GetBundleDirectory()
    {
        if (string.IsNullOrWhiteSpace(configuredBundleDirectory))
        {
            configuredBundleDirectory = ResolveBundleDirectory(null);
        }
        return configuredBundleDirectory;
    }

    private static string ResolveBundleDirectory(string requested)
    {
        if (!string.IsNullOrWhiteSpace(requested))
        {
            return Path.GetFullPath(requested);
        }

        string current = Directory.GetCurrentDirectory();
        return Path.GetFullPath(Path.Combine(current, "ModelBundles"));
    }

    private static bool AlignAuthoredToTabletop(Transform root, Transform instance)
    {
        if (!TryCalculateBounds(instance, out Bounds bounds))
        {
            return false;
        }
        instance.position += Vector3.up * (root.position.y - bounds.min.y);
        return true;
    }

    private static bool NormalizeToTabletop(Transform root, Transform instance)
    {
        if (!TryCalculateBounds(instance, out Bounds bounds))
        {
            return false;
        }

        const float targetMaxWidth = 0.30f;
        const float targetMaxDepth = 0.30f;
        const float targetMaxHeight = 0.38f;
        float scaleX = targetMaxWidth / Mathf.Max(0.0001f, bounds.size.x);
        float scaleZ = targetMaxDepth / Mathf.Max(0.0001f, bounds.size.z);
        float scaleY = targetMaxHeight / Mathf.Max(0.0001f, bounds.size.y);
        float uniformScale = Mathf.Clamp(Mathf.Min(scaleX, scaleZ, scaleY), 0.0001f, 100f);
        instance.localScale *= uniformScale;

        if (!TryCalculateBounds(instance, out bounds))
        {
            return false;
        }

        instance.position += new Vector3(
            root.position.x - bounds.center.x,
            root.position.y - bounds.min.y,
            root.position.z - bounds.center.z);
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
        return initialized && bounds.size.sqrMagnitude > 0.00000001f;
    }

    private static void EnableShadows(GameObject instance)
    {
        foreach (Renderer renderer in instance.GetComponentsInChildren<Renderer>(true))
        {
            renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On;
            renderer.receiveShadows = true;
        }
    }

    private static void ReportInvalid(string prefabName, string reason)
    {
        string key = prefabName + "|" + reason;
        if (ReportedInvalidPrefabs.Add(key))
        {
            Debug.LogWarning("忽略无效模型包物体 '" + prefabName + "'：" + reason);
        }
    }

    private static void ReportManifestWarning(string message)
    {
        if (!manifestWarningReported)
        {
            manifestWarningReported = true;
            Debug.LogWarning(message);
        }
    }

    private static void DestroyObject(GameObject target)
    {
        if (target == null)
        {
            return;
        }
        if (Application.isPlaying)
        {
            UnityEngine.Object.Destroy(target);
        }
        else
        {
            UnityEngine.Object.DestroyImmediate(target);
        }
    }
}
