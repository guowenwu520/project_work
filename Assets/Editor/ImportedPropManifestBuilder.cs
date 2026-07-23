#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;

/// <summary>
/// Compatibility entry point retained for old menu references.
/// Runtime manifests are now produced together with Linux AssetBundle model packs.
/// </summary>
public static class ImportedPropManifestBuilder
{
    [MenuItem("Tools/Change Blindness/Rebuild Runtime Prop Manifest")]
    public static void BuildFromMenu()
    {
        EditorUtility.DisplayDialog(
            "模型包清单",
            "当前版本不再把模型放进 Resources。请执行：\n\nTools > Change Blindness > Model Packs > Build or Update Linux Model Packs\n\n该操作会同时生成 AssetBundle 和外部 prop_manifest.json。",
            "确定");
    }

    public static int BuildManifest(bool silent)
    {
        if (!silent)
        {
            Debug.Log("模型包模式：运行时清单由 ModelBundleBuilder 生成。");
        }
        return 0;
    }
}
#endif
