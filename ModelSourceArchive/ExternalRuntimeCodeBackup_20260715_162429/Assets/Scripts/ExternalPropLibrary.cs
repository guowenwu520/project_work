using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using GLTFast;
using UnityEngine;
using UnityEngine.Rendering;

/// <summary>
/// Loads user-provided GLB/glTF models from a directory outside Assets.
/// Unity therefore does not import or package those models during Player builds.
/// </summary>
public static class ExternalPropLibrary
{
    private const string ManifestFileName = "prop_manifest.json";
    private const float TargetMaxWidth = 0.30f;
    private const float TargetMaxDepth = 0.30f;
    private const float TargetMaxHeight = 0.38f;

    private static string rootDirectory;
    private static ExternalPropManifestData manifest;
    private static Dictionary<string, ExternalPropManifestEntry> lookup;

    public static string RootDirectory
    {
        get
        {
            EnsureInitialized();
            return rootDirectory;
        }
    }

    public static void Initialize(string requestedDirectory)
    {
        rootDirectory = ResolveRootDirectory(requestedDirectory);
        manifest = LoadManifest(rootDirectory);
        lookup = new Dictionary<string, ExternalPropManifestEntry>(StringComparer.OrdinalIgnoreCase);

        if (manifest.props == null)
        {
            manifest.props = new List<ExternalPropManifestEntry>();
        }

        foreach (ExternalPropManifestEntry entry in manifest.props)
        {
            if (entry == null || string.IsNullOrWhiteSpace(entry.name) || string.IsNullOrWhiteSpace(entry.file))
            {
                continue;
            }

            string key = entry.name.Trim();
            if (!lookup.ContainsKey(key))
            {
                lookup.Add(key, entry);
            }
        }

        Debug.Log("External model directory: " + rootDirectory);
        Debug.Log("External model manifest entries: " + lookup.Count);
    }

    public static string[] GetAvailablePropNames()
    {
        EnsureInitialized();
        var names = new List<string>(lookup.Keys);
        names.Sort(StringComparer.OrdinalIgnoreCase);
        return names.ToArray();
    }

    public static bool HasProp(string propName)
    {
        EnsureInitialized();
        return !string.IsNullOrWhiteSpace(propName) && lookup.ContainsKey(propName.Trim());
    }

    public static string GetDisplayName(string propName)
    {
        EnsureInitialized();
        if (string.IsNullOrWhiteSpace(propName))
        {
            return "item";
        }

        if (lookup.TryGetValue(propName.Trim(), out ExternalPropManifestEntry entry) &&
            entry != null && !string.IsNullOrWhiteSpace(entry.displayName))
        {
            return entry.displayName.Trim();
        }

        return propName.Trim();
    }

    public static async Task<GameObject> CreateAsync(
        string propName,
        Transform parent,
        Vector3 localPosition,
        float yawDegrees)
    {
        EnsureInitialized();
        if (string.IsNullOrWhiteSpace(propName) ||
            !lookup.TryGetValue(propName.Trim(), out ExternalPropManifestEntry entry))
        {
            return null;
        }

        string modelPath = ResolveModelPath(entry.file);
        if (!File.Exists(modelPath))
        {
            Debug.LogError("External model file does not exist: " + modelPath);
            return null;
        }

        GameObject root = new GameObject(entry.name.Trim());
        root.transform.SetParent(parent, false);
        root.transform.localPosition = localPosition;
        root.transform.localRotation = Quaternion.Euler(0f, yawDegrees, 0f);

        GameObject visualRoot = new GameObject(entry.name.Trim() + "_Visual");
        visualRoot.transform.SetParent(root.transform, false);

        GltfImport importer = new GltfImport();
        string uri = new Uri(modelPath).AbsoluteUri;
        bool loaded;
        try
        {
            loaded = await importer.Load(uri);
        }
        catch (Exception exception)
        {
            Debug.LogError("Failed to load external glTF: " + modelPath + "\n" + exception);
            importer.Dispose();
            UnityEngine.Object.Destroy(root);
            return null;
        }

        if (!loaded)
        {
            Debug.LogError("glTFast could not load external model: " + modelPath);
            importer.Dispose();
            UnityEngine.Object.Destroy(root);
            return null;
        }

        bool instantiated;
        try
        {
            instantiated = await importer.InstantiateMainSceneAsync(visualRoot.transform);
        }
        catch (Exception exception)
        {
            Debug.LogError("Failed to instantiate external glTF: " + modelPath + "\n" + exception);
            importer.Dispose();
            UnityEngine.Object.Destroy(root);
            return null;
        }

        if (!instantiated)
        {
            Debug.LogError("glTFast loaded the file but could not instantiate its main scene: " + modelPath);
            importer.Dispose();
            UnityEngine.Object.Destroy(root);
            return null;
        }

        RuntimeGltfLifetime lifetime = root.AddComponent<RuntimeGltfLifetime>();
        lifetime.SetImporter(importer);

        RemoveUnsupportedComponents(visualRoot);
        EnableShadows(visualRoot);

        if (entry.manuallyAdjusted)
        {
            visualRoot.transform.localPosition = entry.localPosition;
            visualRoot.transform.localRotation = Quaternion.Euler(entry.localEulerAngles);
            visualRoot.transform.localScale = SanitizeScale(entry.localScale);
            SnapMinimumToTable(root.transform, visualRoot.transform);
        }
        else
        {
            AutoFitToTable(root.transform, visualRoot.transform);
        }

        if (TryCalculateBounds(visualRoot.transform, out Bounds finalBounds))
        {
            ContactShadowUtility.EnsureContactShadow(root.transform, finalBounds, root.transform.position.y);
        }

        return root;
    }

    public static void ClearCache()
    {
        manifest = null;
        lookup = null;
        rootDirectory = null;
    }

    private static void EnsureInitialized()
    {
        if (manifest == null || lookup == null)
        {
            Initialize(null);
        }
    }

    private static string ResolveRootDirectory(string requestedDirectory)
    {
        if (!string.IsNullOrWhiteSpace(requestedDirectory))
        {
            return Path.GetFullPath(requestedDirectory.Trim());
        }

#if UNITY_EDITOR
        return Path.GetFullPath(Path.Combine(Application.dataPath, "..", "ExternalModels"));
#else
        // Build/Linux/ChangeBlindnessRoom.x86_64 -> project root/ExternalModels.
        string candidate = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "ExternalModels"));
        if (Directory.Exists(candidate))
        {
            return candidate;
        }

        return Path.GetFullPath(Path.Combine(Directory.GetCurrentDirectory(), "ExternalModels"));
#endif
    }

    private static ExternalPropManifestData LoadManifest(string directory)
    {
        string path = Path.Combine(directory, ManifestFileName);
        if (!File.Exists(path))
        {
            Debug.LogWarning("External model manifest was not found: " + path +
                             ". Only built-in procedural objects will be available.");
            return new ExternalPropManifestData();
        }

        try
        {
            ExternalPropManifestData data = JsonUtility.FromJson<ExternalPropManifestData>(File.ReadAllText(path));
            return data ?? new ExternalPropManifestData();
        }
        catch (Exception exception)
        {
            Debug.LogError("Failed to read external model manifest: " + path + "\n" + exception);
            return new ExternalPropManifestData();
        }
    }

    private static string ResolveModelPath(string relativePath)
    {
        string normalized = (relativePath ?? string.Empty).Replace('/', Path.DirectorySeparatorChar);
        return Path.GetFullPath(Path.Combine(rootDirectory, normalized));
    }

    private static void AutoFitToTable(Transform root, Transform visual)
    {
        visual.localPosition = Vector3.zero;
        visual.localRotation = Quaternion.identity;
        visual.localScale = Vector3.one;

        if (!TryCalculateBounds(visual, out Bounds bounds))
        {
            return;
        }

        float factor = Mathf.Min(
            TargetMaxWidth / Mathf.Max(0.000001f, bounds.size.x),
            TargetMaxDepth / Mathf.Max(0.000001f, bounds.size.z),
            TargetMaxHeight / Mathf.Max(0.000001f, bounds.size.y));
        factor = Mathf.Clamp(factor, 0.0001f, 1000f);
        visual.localScale = Vector3.one * factor;

        if (!TryCalculateBounds(visual, out bounds))
        {
            return;
        }

        visual.position += new Vector3(
            root.position.x - bounds.center.x,
            root.position.y - bounds.min.y,
            root.position.z - bounds.center.z);
    }

    private static void SnapMinimumToTable(Transform root, Transform visual)
    {
        if (TryCalculateBounds(visual, out Bounds bounds))
        {
            visual.position += Vector3.up * (root.position.y - bounds.min.y);
        }
    }

    private static Vector3 SanitizeScale(Vector3 scale)
    {
        return new Vector3(
            Mathf.Max(0.0001f, Mathf.Abs(scale.x)),
            Mathf.Max(0.0001f, Mathf.Abs(scale.y)),
            Mathf.Max(0.0001f, Mathf.Abs(scale.z)));
    }

    private static void RemoveUnsupportedComponents(GameObject root)
    {
        foreach (Camera camera in root.GetComponentsInChildren<Camera>(true))
        {
            UnityEngine.Object.Destroy(camera);
        }
        foreach (Light light in root.GetComponentsInChildren<Light>(true))
        {
            UnityEngine.Object.Destroy(light);
        }
        foreach (AudioListener listener in root.GetComponentsInChildren<AudioListener>(true))
        {
            UnityEngine.Object.Destroy(listener);
        }
        foreach (Collider collider in root.GetComponentsInChildren<Collider>(true))
        {
            collider.enabled = false;
        }
    }

    private static void EnableShadows(GameObject root)
    {
        foreach (Renderer renderer in root.GetComponentsInChildren<Renderer>(true))
        {
            renderer.shadowCastingMode = ShadowCastingMode.On;
            renderer.receiveShadows = true;
        }
    }

    private static bool TryCalculateBounds(Transform root, out Bounds bounds)
    {
        bool initialized = false;
        bounds = new Bounds(root.position, Vector3.zero);
        Renderer[] renderers = root.GetComponentsInChildren<Renderer>(true);
        foreach (Renderer renderer in renderers)
        {
            if (renderer == null || renderer.transform.name == "ContactShadow")
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
        return initialized;
    }
}

public sealed class RuntimeGltfLifetime : MonoBehaviour
{
    private GltfImport importer;

    public void SetImporter(GltfImport value)
    {
        importer = value;
    }

    private void OnDestroy()
    {
        if (importer != null)
        {
            importer.Dispose();
            importer = null;
        }
    }
}
