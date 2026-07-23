#if UNITY_EDITOR
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

public sealed class ModelLibraryManagerWindow : EditorWindow
{
    private Vector2 scroll;
    private string statusMessage = string.Empty;

    [MenuItem("Tools/Change Blindness/Model Manager")]
    public static void OpenWindow()
    {
        ModelLibraryManagerWindow window = GetWindow<ModelLibraryManagerWindow>("模型管理");
        window.minSize = new Vector2(620f, 500f);
        window.Show();
        window.Focus();
    }

    [MenuItem("Tools/Change Blindness/Restore Latest Model Source Backup")]
    public static void RestoreLatestArchiveFromMenu()
    {
        RestoreLatestArchive(showDialog: true);
    }

    private void OnGUI()
    {
        scroll = EditorGUILayout.BeginScrollView(scroll);
        EditorGUILayout.LabelField("Change Blindness 模型管理", EditorStyles.boldLabel);
        EditorGUILayout.Space(4f);

        DrawPathBox("① 原始模型", RawModelPreprocessor.RawModelRoot,
            "把 FBX、OBJ、GLB 或 glTF 放在这里。模型会一直保留，不再因为轻量构建而移走。");
        DrawPathBox("② 调整后的 Prefab", ModelBundleBuilder.SourceRoot,
            "预处理后自动生成。摆放窗口直接读取并保存这里的 Prefab。");
        DrawPathBox("③ 运行时模型包", ModelBundleBuilder.GetOutputRoot(),
            "完成调整后全量重建到这里。视频生成程序只读取这里的 AssetBundle。");

        EditorGUILayout.Space(8f);
        DrawCounts();
        EditorGUILayout.Space(8f);

        using (new EditorGUILayout.HorizontalScope())
        {
            if (GUILayout.Button("打开原始模型目录", GUILayout.Height(34f)))
            {
                RawModelPreprocessor.OpenRawModelFolder();
            }
            if (GUILayout.Button("预处理新增模型", GUILayout.Height(34f)))
            {
                RawModelPreprocessor.PreprocessAll(showDialog: false, throwOnFailure: false);
                statusMessage = "预处理完成。现在可以打开摆放调整页面。";
            }
        }

        using (new EditorGUILayout.HorizontalScope())
        {
            if (GUILayout.Button("打开摆放调整页面", GUILayout.Height(38f)))
            {
                PropPlacementWindow.OpenWindow();
            }
            if (GUILayout.Button("全量重建 ModelBundles", GUILayout.Height(38f)))
            {
                try
                {
                    int count = ModelBundleBuilder.BuildCurrentPrefabs(showDialog: false);
                    statusMessage = "ModelBundles 已全量重建，当前包含 " + count + " 个导入模型。";
                }
                catch (Exception exception)
                {
                    Debug.LogException(exception);
                    statusMessage = "模型包重建失败：" + exception.Message;
                }
            }
        }

        using (new EditorGUILayout.HorizontalScope())
        {
            if (GUILayout.Button("打开调整后 Prefab 目录", GUILayout.Height(30f)))
            {
                EnsureAssetFolder(ModelBundleBuilder.SourceRoot);
                EditorUtility.RevealInFinder(Path.GetFullPath(ModelBundleBuilder.SourceRoot));
            }
            if (GUILayout.Button("打开运行时模型包目录", GUILayout.Height(30f)))
            {
                ModelBundleBuilder.OpenOutputFolder();
            }
        }

        EditorGUILayout.Space(10f);
        string latestArchive = FindLatestArchive();
        if (!string.IsNullOrWhiteSpace(latestArchive))
        {
            EditorGUILayout.HelpBox(
                "检测到旧版归档目录：\n" + latestArchive +
                "\n\n如果摆放页面没有模型，通常是旧脚本把 Assets/RawModels 和 Assets/ModelPacks 移到了这里。",
                MessageType.Warning);
            if (GUILayout.Button("恢复最近一次归档到固定目录", GUILayout.Height(34f)))
            {
                int copied = RestoreLatestArchive(showDialog: false);
                statusMessage = copied >= 0
                    ? "已恢复最近归档，共复制 " + copied + " 个文件。"
                    : "没有可恢复的归档。";
            }
        }

        EditorGUILayout.Space(8f);
        EditorGUILayout.HelpBox(
            "推荐固定流程：\n" +
            "1. 模型放入 Assets/RawModels。\n" +
            "2. 点击“预处理新增模型”。\n" +
            "3. 点击“打开摆放调整页面”，保存姿态。\n" +
            "4. 点击“全量重建 ModelBundles”。\n" +
            "5. 正常运行轻量 build_linux.sh。\n\n" +
            "不要再把 RawModels 或 ModelPacks 移出 Assets。轻量构建脚本本身已经排除这两个目录。",
            MessageType.Info);

        if (!string.IsNullOrWhiteSpace(statusMessage))
        {
            EditorGUILayout.HelpBox(statusMessage, MessageType.None);
        }

        EditorGUILayout.EndScrollView();
    }

    private static void DrawPathBox(string title, string path, string description)
    {
        using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
        {
            EditorGUILayout.LabelField(title, EditorStyles.boldLabel);
            EditorGUILayout.SelectableLabel(path, EditorStyles.textField, GUILayout.Height(20f));
            EditorGUILayout.LabelField(description, EditorStyles.wordWrappedMiniLabel);
        }
    }

    private static void DrawCounts()
    {
        int rawCount = AssetDatabase.GetAllAssetPaths().Count(RawModelPreprocessor.IsSupportedRawModelPath);
        int prefabCount = AssetDatabase.IsValidFolder(ModelBundleBuilder.SourceRoot)
            ? AssetDatabase.FindAssets("t:Prefab", new[] { ModelBundleBuilder.SourceRoot }).Length
            : 0;
        int runtimeCount = ReadRuntimeManifestCount();

        using (new EditorGUILayout.HorizontalScope(EditorStyles.helpBox))
        {
            GUILayout.Label("原始模型：" + rawCount, GUILayout.MinWidth(150f));
            GUILayout.Label("可调整 Prefab：" + prefabCount, GUILayout.MinWidth(170f));
            GUILayout.Label("运行时模型：" + runtimeCount, GUILayout.MinWidth(160f));
        }
    }

    private static int ReadRuntimeManifestCount()
    {
        string manifestPath = Path.Combine(ModelBundleBuilder.GetOutputRoot(), "prop_manifest.json");
        if (!File.Exists(manifestPath))
        {
            return 0;
        }

        try
        {
            ImportedPropManifestData data = JsonUtility.FromJson<ImportedPropManifestData>(File.ReadAllText(manifestPath));
            return data != null && data.props != null ? data.props.Count : 0;
        }
        catch
        {
            return 0;
        }
    }

    private static int RestoreLatestArchive(bool showDialog)
    {
        string latest = FindLatestArchive();
        if (string.IsNullOrWhiteSpace(latest))
        {
            if (showDialog)
            {
                EditorUtility.DisplayDialog("恢复模型", "没有找到 ModelSourceArchive 归档目录。", "确定");
            }
            return -1;
        }

        string projectRoot = Directory.GetParent(Application.dataPath).FullName;
        string archivedAssets = Path.Combine(latest, "Assets");
        int copied = 0;
        copied += CopyDirectoryMerge(
            Path.Combine(archivedAssets, "RawModels"),
            Path.Combine(projectRoot, "Assets", "RawModels"));
        copied += CopyDirectoryMerge(
            Path.Combine(archivedAssets, "ModelPacks"),
            Path.Combine(projectRoot, "Assets", "ModelPacks"));

        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        ImportedPropLibrary.ClearCache();

        if (showDialog)
        {
            EditorUtility.DisplayDialog(
                "模型恢复完成",
                "来源：\n" + latest +
                "\n\n已复制 " + copied + " 个文件到固定目录。\n" +
                "现在打开 Tools > Change Blindness > Adjust Imported Prop Placement。",
                "确定");
        }

        return copied;
    }

    private static string FindLatestArchive()
    {
        string projectRoot = Directory.GetParent(Application.dataPath).FullName;
        string archiveRoot = Path.Combine(projectRoot, "ModelSourceArchive");
        if (!Directory.Exists(archiveRoot))
        {
            return null;
        }

        return Directory.GetDirectories(archiveRoot, "*", SearchOption.TopDirectoryOnly)
            .OrderByDescending(path => Path.GetFileName(path), StringComparer.OrdinalIgnoreCase)
            .FirstOrDefault(path => Directory.Exists(Path.Combine(path, "Assets", "RawModels")) ||
                                    Directory.Exists(Path.Combine(path, "Assets", "ModelPacks")));
    }

    private static int CopyDirectoryMerge(string sourceRoot, string destinationRoot)
    {
        if (!Directory.Exists(sourceRoot))
        {
            return 0;
        }

        int copied = 0;
        foreach (string sourceFile in Directory.GetFiles(sourceRoot, "*", SearchOption.AllDirectories))
        {
            string relative = sourceFile.Substring(sourceRoot.Length)
                .TrimStart(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string destinationFile = Path.Combine(destinationRoot, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(destinationFile));
            File.Copy(sourceFile, destinationFile, true);
            copied++;
        }
        return copied;
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
