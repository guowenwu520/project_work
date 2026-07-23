#if UNITY_EDITOR
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEngine;

public static class ImportedPropValidator
{
    [MenuItem("Tools/Change Blindness/Validate Imported Props")]
    public static void Validate()
    {
        string root = ModelBundleBuilder.SourceRoot;
        string[] paths = AssetDatabase.IsValidFolder(root)
            ? AssetDatabase.FindAssets("t:Prefab", new[] { root })
                .Select(AssetDatabase.GUIDToAssetPath)
                .OrderBy(path => path)
                .ToArray()
            : new string[0];

        int validCount = 0;
        StringBuilder report = new StringBuilder();
        report.AppendLine("ModelPacks 检查：共找到 " + paths.Length + " 个 Prefab。\n");

        foreach (string path in paths)
        {
            GameObject prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (ImportedPropLibrary.IsUsablePrefab(prefab, out string reason))
            {
                validCount++;
                report.AppendLine("[有效] " + (prefab != null ? prefab.name : path));
            }
            else
            {
                report.AppendLine("[无效] " + path + "：" + reason);
            }
        }

        report.AppendLine("\n有效数量：" + validCount + "。构建模型包后，运行时会按需加载。 ");
        Debug.Log(report.ToString());
        EditorUtility.DisplayDialog(
            "Model Packs 检查",
            "找到 " + paths.Length + " 个 Prefab，其中 " + validCount + " 个有效。\n详细结果请查看 Console。",
            "确定");
    }
}
#endif
