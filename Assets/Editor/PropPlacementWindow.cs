#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Linq;
using System.IO;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

public sealed class PropPlacementWindow : EditorWindow
{
    private const string GeneratedRoot = "Assets/ModelPacks/Generated";
    private const string BuiltInGeneratedRoot = BuiltInPropPrefabBuilder.OutputRoot;
    private const float TargetMaxWidth = 0.30f;
    private const float TargetMaxDepth = 0.30f;
    private const float TargetMaxHeight = 0.38f;
    private const int PageSize = 10;
    private const string AdjustedMarker = "ChangeBlindness.ManuallyAdjusted";
    private static readonly Vector3 SelectedPreviewAnchor = new Vector3(0.34f, 0f, 0.02f);
    private static readonly Vector3 ReferencePreviewAnchor = new Vector3(-0.40f, 0f, 0.03f);

    private readonly List<string> prefabPaths = new List<string>();
    private PreviewRenderUtility previewUtility;
    private GameObject previewRoot;
    private Transform previewVisual;
    private GameObject previewTable;
    private GameObject previewEnvironmentRoot;
    private GameObject previewReferenceRoot;
    private MaterialLibrary previewMaterials;
    private bool showReferenceObject = true;
    private bool showAdjustedModels = true;
    private int pageIndex;
    private int totalPrefabCount;
    private int adjustedPrefabCount;
    private Vector2 listScroll;
    private int selectedIndex = -1;
    private Vector3 editedPosition;
    private Vector3 editedRotation;
    private Vector3 editedScale = Vector3.one;
    private bool autoSnapToTable = true;
    private string statusMessage = "";

    [MenuItem("Tools/Change Blindness/Adjust Imported Prop Placement")]
    public static void OpenWindow()
    {
        TrySyncRenamedModels();
        PropPlacementWindow window = GetWindow<PropPlacementWindow>("小物体摆放调整");
        window.minSize = new Vector2(900f, 620f);
        window.Show();
        window.Focus();
        window.RefreshPrefabList(keepSelection: true);
    }

    private void OnEnable()
    {
        TrySyncRenamedModels();
        CreatePreviewUtility();
        RefreshPrefabList(keepSelection: true);
    }

    private void OnDisable()
    {
        if (previewUtility != null)
        {
            previewUtility.Cleanup();
            previewUtility = null;
        }

        previewRoot = null;
        previewVisual = null;
        previewTable = null;
        previewEnvironmentRoot = null;
        previewReferenceRoot = null;
        previewMaterials = null;
    }

    private void OnGUI()
    {
        DrawTopToolbar();
        EditorGUILayout.Space(4f);

        using (new EditorGUILayout.HorizontalScope())
        {
            DrawPrefabList();
            DrawPlacementEditor();
        }
    }

    private void DrawTopToolbar()
    {
        using (new EditorGUILayout.HorizontalScope(EditorStyles.toolbar))
        {
            if (GUILayout.Button("刷新列表", EditorStyles.toolbarButton, GUILayout.Width(80f)))
            {
                TrySyncRenamedModels();
                RefreshPrefabList(keepSelection: true);
            }

            if (GUILayout.Button("同步重命名", EditorStyles.toolbarButton, GUILayout.Width(95f)))
            {
                TrySyncRenamedModels(showDialog: true);
                RefreshPrefabList(keepSelection: true);
            }

            if (GUILayout.Button("预处理 RawModels", EditorStyles.toolbarButton, GUILayout.Width(125f)))
            {
                RawModelPreprocessor.PreprocessAll(showDialog: false, throwOnFailure: false);
                RefreshPrefabList(keepSelection: true);
            }

            if (GUILayout.Button("补全40个内置物体", EditorStyles.toolbarButton, GUILayout.Width(135f)))
            {
                BuiltInPropPrefabBuilder.EnsureBuiltInPrefabs(showDialog: false, overwriteExisting: false);
                RefreshPrefabList(keepSelection: true);
                statusMessage = "已补全 40 个内置物体。现有手工姿态不会被覆盖。";
            }

            if (GUILayout.Button("打开原始模型目录", EditorStyles.toolbarButton, GUILayout.Width(130f)))
            {
                RawModelPreprocessor.OpenRawModelFolder();
            }

            if (GUILayout.Button("模型管理", EditorStyles.toolbarButton, GUILayout.Width(80f)))
            {
                ModelLibraryManagerWindow.OpenWindow();
            }

            bool newShowAdjusted = GUILayout.Toggle(
                showAdjustedModels,
                "显示已调整模型",
                EditorStyles.toolbarButton,
                GUILayout.Width(120f));
            if (newShowAdjusted != showAdjustedModels)
            {
                showAdjustedModels = newShowAdjusted;
                pageIndex = 0;
                RefreshPrefabList(keepSelection: false);
            }

            bool newShowReference = GUILayout.Toggle(
                showReferenceObject,
                "显示 10cm 参考杯",
                EditorStyles.toolbarButton,
                GUILayout.Width(125f));
            if (newShowReference != showReferenceObject)
            {
                showReferenceObject = newShowReference;
                if (previewReferenceRoot != null)
                {
                    previewReferenceRoot.SetActive(showReferenceObject);
                }
                Repaint();
            }

            GUILayout.FlexibleSpace();
            GUILayout.Label("每页 10 个｜只实例化当前选中模型", EditorStyles.miniLabel);
        }
    }

    private void DrawPrefabList()
    {
        using (new EditorGUILayout.VerticalScope(GUILayout.Width(255f)))
        {
            EditorGUILayout.LabelField("待调整的桌面物体", EditorStyles.boldLabel);
            int pendingCount = Mathf.Max(0, totalPrefabCount - adjustedPrefabCount);
            EditorGUILayout.LabelField(
                showAdjustedModels
                    ? $"全部：{totalPrefabCount}｜已调整：{adjustedPrefabCount}"
                    : $"未调整：{pendingCount}｜已调整已隐藏：{adjustedPrefabCount}",
                EditorStyles.miniLabel);

            int pageCount = GetPageCount();
            pageIndex = Mathf.Clamp(pageIndex, 0, pageCount - 1);
            int startIndex = pageIndex * PageSize;
            int endIndex = Mathf.Min(startIndex + PageSize, prefabPaths.Count);

            listScroll = EditorGUILayout.BeginScrollView(listScroll);
            for (int i = startIndex; i < endIndex; i++)
            {
                string path = prefabPaths[i];
                bool adjusted = IsManuallyAdjustedPath(path);
                string mark = adjusted ? "✓ " : "○ ";
                string sourceMark = IsBuiltInPrefabPath(path) ? "[内置] " : "[导入] ";
                string label = mark + sourceMark + Path.GetFileNameWithoutExtension(path);

                GUIStyle style = i == selectedIndex ? EditorStyles.toolbarButton : EditorStyles.miniButton;
                if (GUILayout.Button(label, style, GUILayout.Height(28f)))
                {
                    SelectPrefab(i);
                }
            }
            EditorGUILayout.EndScrollView();

            using (new EditorGUILayout.HorizontalScope())
            {
                using (new EditorGUI.DisabledScope(pageIndex <= 0))
                {
                    if (GUILayout.Button("上一页", GUILayout.Height(26f)))
                    {
                        ChangePage(pageIndex - 1);
                    }
                }

                GUILayout.Label($"第 {pageIndex + 1}/{pageCount} 页", EditorStyles.centeredGreyMiniLabel, GUILayout.Width(82f));

                using (new EditorGUI.DisabledScope(pageIndex >= pageCount - 1))
                {
                    if (GUILayout.Button("下一页", GUILayout.Height(26f)))
                    {
                        ChangePage(pageIndex + 1);
                    }
                }
            }

            if (prefabPaths.Count == 0)
            {
                EditorGUILayout.HelpBox(
                    showAdjustedModels
                        ? "没有找到已生成的 Prefab。请打开顶部“模型管理”；如果以前运行过归档脚本，可恢复最近一次归档。"
                        : "当前没有未调整模型。需要检查旧模型时，打开顶部的“显示已调整模型”。",
                    MessageType.Info);
            }
            else
            {
                EditorGUILayout.HelpBox(
                    "窗口每页只显示 10 个模型，并且只实例化当前选中的一个。默认过滤已经人工保存的模型。",
                    MessageType.Info);
            }
        }
    }

    private void DrawPlacementEditor()
    {
        using (new EditorGUILayout.VerticalScope())
        {
            if (selectedIndex < 0 || selectedIndex >= prefabPaths.Count || previewVisual == null)
            {
                EditorGUILayout.HelpBox(
                    "左侧包含已生成的内置物体和你导入的模型。首次使用请点击顶部“补全40个内置物体”，然后选择物体进行调整。",
                    MessageType.Info);
                return;
            }

            EditorGUILayout.LabelField(
                Path.GetFileNameWithoutExtension(prefabPaths[selectedIndex]),
                EditorStyles.boldLabel);

            Rect previewRect = GUILayoutUtility.GetRect(300f, 10000f, 300f, Mathf.Max(310f, position.height * 0.53f));
            DrawPreview(previewRect);
            HandlePreviewInput(previewRect);
            DrawCurrentSizeInfo();

            EditorGUILayout.Space(5f);
            DrawTransformControls();
            DrawActionButtons();

            if (!string.IsNullOrWhiteSpace(statusMessage))
            {
                EditorGUILayout.HelpBox(statusMessage, MessageType.None);
            }
        }
    }

    private void DrawTransformControls()
    {
        EditorGUI.BeginChangeCheck();
        Vector3 newPosition = EditorGUILayout.Vector3Field("局部位置", editedPosition);
        Vector3 newRotation = EditorGUILayout.Vector3Field("旋转角度", editedRotation);
        Vector3 newScale = EditorGUILayout.Vector3Field("缩放", editedScale);
        bool newAutoSnap = EditorGUILayout.ToggleLeft("旋转或缩放后自动把最低点贴到桌面", autoSnapToTable);
        if (EditorGUI.EndChangeCheck())
        {
            editedPosition = newPosition;
            editedRotation = NormalizeEuler(newRotation);
            editedScale = ClampScale(newScale);
            autoSnapToTable = newAutoSnap;
            ApplyEditedTransform(snapToTable: autoSnapToTable);
        }

        using (new EditorGUILayout.HorizontalScope())
        {
            GUILayout.Label("快速旋转", GUILayout.Width(65f));
            if (GUILayout.Button("X +90°")) RotateBy(new Vector3(90f, 0f, 0f));
            if (GUILayout.Button("Y +90°")) RotateBy(new Vector3(0f, 90f, 0f));
            if (GUILayout.Button("Z +90°")) RotateBy(new Vector3(0f, 0f, 90f));
            if (GUILayout.Button("反面 X 180°")) RotateBy(new Vector3(180f, 0f, 0f));
        }
    }

    private void DrawActionButtons()
    {
        using (new EditorGUILayout.HorizontalScope())
        {
            if (GUILayout.Button("自动适配桌面尺寸", GUILayout.Height(30f)))
            {
                AutoFitCurrentOrientation();
            }

            if (GUILayout.Button("居中并贴到桌面", GUILayout.Height(30f)))
            {
                CenterAndSnapPreview();
            }

            if (GUILayout.Button("恢复上次保存", GUILayout.Height(30f)))
            {
                LoadSelectedPrefabIntoPreview();
            }
        }

        using (new EditorGUILayout.HorizontalScope())
        {
            GUI.backgroundColor = new Color(0.63f, 0.90f, 0.66f);
            if (GUILayout.Button("保存当前姿态", GUILayout.Height(36f)))
            {
                SaveCurrentPrefab(moveToNext: false);
            }

            if (GUILayout.Button("保存并调整下一个", GUILayout.Height(36f)))
            {
                SaveCurrentPrefab(moveToNext: true);
            }
            GUI.backgroundColor = Color.white;
        }

        using (new EditorGUILayout.HorizontalScope())
        {
            GUILayout.FlexibleSpace();
            bool selectedBuiltIn = IsSelectedBuiltInPrefab();
            GUI.backgroundColor = selectedBuiltIn
                ? new Color(0.95f, 0.76f, 0.42f)
                : new Color(1f, 0.58f, 0.55f);
            string actionLabel = selectedBuiltIn ? "重置当前内置物体" : "删除当前模型";
            if (GUILayout.Button(actionLabel, GUILayout.Width(180f), GUILayout.Height(32f)))
            {
                if (selectedBuiltIn)
                {
                    ResetCurrentBuiltInModel();
                }
                else
                {
                    DeleteCurrentModel();
                }
            }
            GUI.backgroundColor = Color.white;
        }
    }

    private void CreatePreviewUtility()
    {
        if (previewUtility != null)
        {
            return;
        }

        previewUtility = new PreviewRenderUtility();
        previewUtility.camera.clearFlags = CameraClearFlags.Color;
        previewUtility.camera.backgroundColor = new Color(0.67f, 0.64f, 0.58f, 1f);
        previewUtility.camera.fieldOfView = 37f;
        previewUtility.camera.nearClipPlane = 0.03f;
        previewUtility.camera.farClipPlane = 30f;
        previewUtility.camera.transform.position = new Vector3(0f, 1.24f, -3.37f);
        previewUtility.camera.transform.LookAt(new Vector3(0f, 0.08f, -0.02f));
        previewUtility.ambientColor = new Color(0.54f, 0.52f, 0.48f);

        previewUtility.lights[0].intensity = 1.28f;
        previewUtility.lights[0].color = new Color(1f, 0.95f, 0.88f);
        previewUtility.lights[0].transform.rotation = Quaternion.Euler(46f, -138f, 0f);
        previewUtility.lights[0].shadows = LightShadows.Soft;
        previewUtility.lights[1].intensity = 0f;

        previewMaterials = new MaterialLibrary(20260713);
        BuildPreviewEnvironment();
        BuildReferenceObject();
    }

    private void BuildPreviewEnvironment()
    {
        previewEnvironmentRoot = new GameObject("PreviewEnvironment");
        previewEnvironmentRoot.hideFlags = HideFlags.HideAndDontSave;

        Material floor = previewMaterials.Wood(
            "PreviewFloorWood",
            new Color(0.12f, 0.065f, 0.035f),
            new Color(0.43f, 0.23f, 0.10f),
            0.28f);
        Material wall = previewMaterials.Plaster("PreviewWarmPlaster", new Color(0.74f, 0.70f, 0.64f));
        Material trim = previewMaterials.Get("PreviewTrim", new Color(0.68f, 0.66f, 0.61f), 0f, 0.25f);
        Material tableWood = previewMaterials.Wood(
            "PreviewTableWalnut",
            new Color(0.075f, 0.035f, 0.018f),
            new Color(0.38f, 0.16f, 0.055f),
            0.48f);
        Material tableMetal = previewMaterials.Get("PreviewTableMetal", new Color(0.055f, 0.058f, 0.062f), 0.72f, 0.38f);

        // Same room proportions as the runtime scene, translated so the tabletop surface is Y=0.
        ScenePrimitives.CreatePrimitive(
            PrimitiveType.Cube,
            "PreviewFloor",
            previewEnvironmentRoot.transform,
            new Vector3(0f, -1.00f, 0f),
            new Vector3(8f, 0.12f, 8f),
            floor);
        ScenePrimitives.CreatePrimitive(
            PrimitiveType.Cube,
            "PreviewBackWall",
            previewEnvironmentRoot.transform,
            new Vector3(0f, 0.66f, 3.65f),
            new Vector3(8f, 3.2f, 0.12f),
            wall);
        ScenePrimitives.CreatePrimitive(
            PrimitiveType.Cube,
            "PreviewBaseboard",
            previewEnvironmentRoot.transform,
            new Vector3(0f, -0.85f, 3.56f),
            new Vector3(7.8f, 0.18f, 0.06f),
            trim);

        Transform table = new GameObject("FixedTable").transform;
        table.SetParent(previewEnvironmentRoot.transform, false);
        table.localPosition = new Vector3(0f, -RoomEnvironment.TableTopSurfaceY, 0f);

        previewTable = ScenePrimitives.CreatePrimitive(
            PrimitiveType.Cube,
            "TableTop",
            table,
            new Vector3(0f, 0.89f, 0f),
            new Vector3(2.25f, 0.10f, 1.02f),
            tableWood);
        ScenePrimitives.CreatePrimitive(
            PrimitiveType.Cube,
            "ApronFront",
            table,
            new Vector3(0f, 0.79f, -0.43f),
            new Vector3(1.94f, 0.18f, 0.075f),
            tableWood);
        ScenePrimitives.CreatePrimitive(
            PrimitiveType.Cube,
            "ApronBack",
            table,
            new Vector3(0f, 0.79f, 0.43f),
            new Vector3(1.94f, 0.18f, 0.075f),
            tableWood);

        Vector3 legScale = new Vector3(0.095f, 0.82f, 0.095f);
        float x = 0.94f;
        float z = 0.36f;
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "LegFL", table, new Vector3(-x, 0.41f, -z), legScale, tableMetal);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "LegFR", table, new Vector3(x, 0.41f, -z), legScale, tableMetal);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "LegBL", table, new Vector3(-x, 0.41f, z), legScale, tableMetal);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "LegBR", table, new Vector3(x, 0.41f, z), legScale, tableMetal);

        GameObject ceilingLamp = new GameObject("PreviewCeilingLamp");
        ceilingLamp.transform.SetParent(previewEnvironmentRoot.transform, false);
        ceilingLamp.transform.localPosition = new Vector3(-0.55f, 1.51f, 0.75f);
        Light lamp = ceilingLamp.AddComponent<Light>();
        lamp.type = LightType.Point;
        lamp.color = new Color(1f, 0.86f, 0.70f);
        lamp.intensity = 2.8f;
        lamp.range = 7.5f;
        lamp.shadows = LightShadows.Soft;
        lamp.shadowStrength = 0.5f;
        lamp.shadowBias = 0.05f;
        lamp.shadowNormalBias = 0.35f;

        EnablePreviewShadows(previewEnvironmentRoot);
        previewUtility.AddSingleGO(previewEnvironmentRoot);
    }

    private void BuildReferenceObject()
    {
        previewReferenceRoot = new GameObject("ReferenceMug_10cm");
        previewReferenceRoot.hideFlags = HideFlags.HideAndDontSave;
        previewReferenceRoot.transform.position = ReferencePreviewAnchor;

        Material ceramic = previewMaterials.Get("ReferenceMugCeramic", new Color(0.72f, 0.18f, 0.11f), 0f, 0.62f);
        Material inside = previewMaterials.Get("ReferenceMugInside", new Color(0.08f, 0.035f, 0.018f), 0f, 0.22f);

        ScenePrimitives.CreatePrimitive(
            PrimitiveType.Cylinder,
            "ReferenceCup",
            previewReferenceRoot.transform,
            new Vector3(0f, 0.055f, 0f),
            new Vector3(0.05f, 0.055f, 0.05f),
            ceramic);
        ScenePrimitives.CreatePrimitive(
            PrimitiveType.Cylinder,
            "ReferenceInside",
            previewReferenceRoot.transform,
            new Vector3(0f, 0.111f, 0f),
            new Vector3(0.041f, 0.002f, 0.041f),
            inside);
        Mesh handleMesh = ScenePrimitives.CreateTorus(0.037f, 0.007f, 28, 8);
        ScenePrimitives.CreateMeshObject(
            "ReferenceHandle",
            previewReferenceRoot.transform,
            handleMesh,
            ceramic,
            new Vector3(0.052f, 0.058f, 0f),
            Vector3.one,
            new Vector3(90f, 0f, 0f));

        EnablePreviewShadows(previewReferenceRoot);
        previewReferenceRoot.SetActive(showReferenceObject);
        previewUtility.AddSingleGO(previewReferenceRoot);
    }

    private static void EnablePreviewShadows(GameObject root)
    {
        foreach (Renderer renderer in root.GetComponentsInChildren<Renderer>(true))
        {
            renderer.shadowCastingMode = ShadowCastingMode.On;
            renderer.receiveShadows = true;
        }
    }

    private void DrawCurrentSizeInfo()
    {
        if (previewVisual == null || !TryCalculateBounds(previewVisual, out Bounds bounds))
        {
            return;
        }

        Vector3 cm = bounds.size * 100f;
        EditorGUILayout.HelpBox(
            $"当前模型尺寸：宽 {cm.x:0.0} cm × 高 {cm.y:0.0} cm × 深 {cm.z:0.0} cm。" +
            "左侧参考杯约宽 10 cm、高 11 cm；预览桌与正式场景桌尺寸一致（225 × 102 cm）。",
            MessageType.Info);
    }

    private void RefreshPrefabList(bool keepSelection)
    {
        RefreshPrefabList(keepSelection, preferredIndex: -1);
    }

    private void RefreshPrefabList(bool keepSelection, int preferredIndex)
    {
        string previousPath = keepSelection && selectedIndex >= 0 && selectedIndex < prefabPaths.Count
            ? prefabPaths[selectedIndex]
            : null;

        List<string> allPaths = new List<string>();
        if (AssetDatabase.IsValidFolder(BuiltInGeneratedRoot))
        {
            allPaths.AddRange(
                AssetDatabase.FindAssets("t:Prefab", new[] { BuiltInGeneratedRoot })
                    .Select(AssetDatabase.GUIDToAssetPath));
        }
        if (AssetDatabase.IsValidFolder(GeneratedRoot))
        {
            allPaths.AddRange(
                AssetDatabase.FindAssets("t:Prefab", new[] { GeneratedRoot })
                    .Select(AssetDatabase.GUIDToAssetPath));
        }
        allPaths = allPaths
            .Where(path => !string.IsNullOrWhiteSpace(path))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(path => IsBuiltInPrefabPath(path) ? 0 : 1)
            .ThenBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ToList();

        totalPrefabCount = allPaths.Count;
        adjustedPrefabCount = 0;
        prefabPaths.Clear();
        foreach (string path in allPaths)
        {
            bool adjusted = IsManuallyAdjustedPath(path);
            if (adjusted)
            {
                adjustedPrefabCount++;
            }

            if (showAdjustedModels || !adjusted)
            {
                prefabPaths.Add(path);
            }
        }

        int newIndex = !string.IsNullOrEmpty(previousPath) ? prefabPaths.IndexOf(previousPath) : -1;
        if (newIndex < 0 && preferredIndex >= 0 && prefabPaths.Count > 0)
        {
            newIndex = Mathf.Clamp(preferredIndex, 0, prefabPaths.Count - 1);
        }
        if (newIndex < 0 && prefabPaths.Count > 0)
        {
            newIndex = Mathf.Clamp(pageIndex * PageSize, 0, prefabPaths.Count - 1);
        }

        if (newIndex >= 0)
        {
            pageIndex = newIndex / PageSize;
            SelectPrefab(newIndex);
        }
        else
        {
            pageIndex = 0;
            selectedIndex = -1;
            ClearPreviewModel();
        }

        listScroll = Vector2.zero;
        Repaint();
    }

    private int GetPageCount()
    {
        return Mathf.Max(1, Mathf.CeilToInt(prefabPaths.Count / (float)PageSize));
    }

    private void ChangePage(int newPage)
    {
        pageIndex = Mathf.Clamp(newPage, 0, GetPageCount() - 1);
        int firstIndex = pageIndex * PageSize;
        listScroll = Vector2.zero;
        if (firstIndex < prefabPaths.Count)
        {
            SelectPrefab(firstIndex);
        }
        else
        {
            selectedIndex = -1;
            ClearPreviewModel();
        }
    }

    private void SelectPrefab(int index)
    {
        if (index < 0 || index >= prefabPaths.Count)
        {
            return;
        }

        selectedIndex = index;
        statusMessage = "";
        LoadSelectedPrefabIntoPreview();
    }

    private void LoadSelectedPrefabIntoPreview()
    {
        ClearPreviewModel();
        if (selectedIndex < 0 || selectedIndex >= prefabPaths.Count)
        {
            return;
        }

        GameObject prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPaths[selectedIndex]);
        if (prefab == null)
        {
            return;
        }

        bool needsInitialTableFit = prefab.GetComponent<ImportedPropPlacement>() == null;

        previewRoot = Instantiate(prefab);
        previewRoot.name = prefab.name + "_Preview";
        previewRoot.hideFlags = HideFlags.HideAndDontSave;
        previewRoot.transform.position = SelectedPreviewAnchor;
        previewRoot.transform.rotation = Quaternion.identity;
        previewRoot.transform.localScale = Vector3.one;
        previewVisual = FindEditableVisual(previewRoot.transform);
        previewUtility.AddSingleGO(previewRoot);

        if (previewVisual != null)
        {
            editedPosition = previewVisual.localPosition;
            editedRotation = NormalizeEuler(previewVisual.localEulerAngles);
            editedScale = previewVisual.localScale;
            if (needsInitialTableFit)
            {
                AutoFitCurrentOrientation();
            }
            else
            {
                ApplyEditedTransform(snapToTable: true);
            }
        }

        Repaint();
    }

    private void ClearPreviewModel()
    {
        if (previewRoot != null)
        {
            DestroyImmediate(previewRoot);
        }

        previewRoot = null;
        previewVisual = null;
    }

    private void DrawPreview(Rect rect)
    {
        if (previewUtility == null)
        {
            CreatePreviewUtility();
        }

        EditorGUI.DrawRect(rect, new Color(0.10f, 0.11f, 0.12f));
        previewUtility.BeginPreview(rect, GUIStyle.none);
        previewUtility.camera.Render();
        Texture previewTexture = previewUtility.EndPreview();
        GUI.DrawTexture(rect, previewTexture, ScaleMode.StretchToFill, false);

        GUI.Label(
            new Rect(rect.x + 8f, rect.y + 7f, rect.width - 16f, 22f),
            "正式桌面预览｜右侧：当前模型｜左侧：10×11 cm 参考杯",
            EditorStyles.whiteMiniLabel);
    }

    private void HandlePreviewInput(Rect rect)
    {
        Event current = Event.current;
        if (!rect.Contains(current.mousePosition) || previewVisual == null)
        {
            return;
        }

        bool changed = false;
        if (current.type == EventType.MouseDrag && current.button == 0)
        {
            editedRotation.x += current.delta.y * 0.55f;
            editedRotation.y -= current.delta.x * 0.75f;
            editedRotation = NormalizeEuler(editedRotation);
            changed = true;
        }
        else if (current.type == EventType.MouseDrag && (current.button == 1 || current.button == 2))
        {
            editedPosition.x += current.delta.x * 0.0015f;
            editedPosition.z -= current.delta.y * 0.0015f;
            changed = true;
        }
        else if (current.type == EventType.ScrollWheel)
        {
            float factor = Mathf.Exp(-current.delta.y * 0.055f);
            editedScale = ClampScale(editedScale * factor);
            changed = true;
        }

        if (!changed)
        {
            return;
        }

        ApplyEditedTransform(snapToTable: autoSnapToTable);
        current.Use();
        Repaint();
    }

    private void RotateBy(Vector3 delta)
    {
        editedRotation = NormalizeEuler(editedRotation + delta);
        ApplyEditedTransform(snapToTable: true);
    }

    private void ApplyEditedTransform(bool snapToTable)
    {
        if (previewVisual == null)
        {
            return;
        }

        previewVisual.localPosition = editedPosition;
        previewVisual.localEulerAngles = editedRotation;
        previewVisual.localScale = ClampScale(editedScale);

        if (snapToTable && TryCalculateBounds(previewVisual, out Bounds bounds))
        {
            previewVisual.position += Vector3.up * -bounds.min.y;
            editedPosition = previewVisual.localPosition;
        }
    }

    private void AutoFitCurrentOrientation()
    {
        ApplyEditedTransform(snapToTable: false);
        if (!TryCalculateBounds(previewVisual, out Bounds bounds))
        {
            statusMessage = "模型没有有效的 Renderer/Mesh，无法自动适配。";
            return;
        }

        float factor = Mathf.Min(
            TargetMaxWidth / Mathf.Max(0.000001f, bounds.size.x),
            TargetMaxDepth / Mathf.Max(0.000001f, bounds.size.z),
            TargetMaxHeight / Mathf.Max(0.000001f, bounds.size.y));
        editedScale = ClampScale(editedScale * factor);
        ApplyEditedTransform(snapToTable: false);
        CenterAndSnapPreview();
        statusMessage = "已按当前朝向适配到桌面物体尺寸。";
    }

    private void CenterAndSnapPreview()
    {
        ApplyEditedTransform(snapToTable: false);
        if (!TryCalculateBounds(previewVisual, out Bounds bounds))
        {
            return;
        }

        Vector3 anchor = previewRoot != null ? previewRoot.transform.position : SelectedPreviewAnchor;
        previewVisual.position += new Vector3(anchor.x - bounds.center.x, -bounds.min.y, anchor.z - bounds.center.z);
        editedPosition = previewVisual.localPosition;
        statusMessage = "已居中，并把模型最低点贴到桌面。";
    }

    private void SaveCurrentPrefab(bool moveToNext)
    {
        if (selectedIndex < 0 || selectedIndex >= prefabPaths.Count)
        {
            return;
        }

        string prefabPath = prefabPaths[selectedIndex];
        GameObject root = null;
        try
        {
            root = PrefabUtility.LoadPrefabContents(prefabPath);
            Transform visual = FindEditableVisual(root.transform);
            if (visual == null)
            {
                statusMessage = "保存失败：Prefab 中没有找到可调整的 Visual 节点。";
                return;
            }

            visual.localPosition = editedPosition;
            visual.localEulerAngles = editedRotation;
            visual.localScale = ClampScale(editedScale);

            if (TryCalculateBounds(visual, out Bounds bounds))
            {
                visual.position += Vector3.up * (root.transform.position.y - bounds.min.y);
                editedPosition = visual.localPosition;
            }

            ImportedPropPlacement placement = root.GetComponent<ImportedPropPlacement>();
            if (placement == null)
            {
                placement = root.AddComponent<ImportedPropPlacement>();
            }
            placement.preserveAuthoredPlacement = true;
            placement.manuallyAdjusted = true;

            PrefabUtility.SaveAsPrefabAsset(root, prefabPath, out bool success);
            if (!success)
            {
                statusMessage = "保存失败：Unity 无法写入该 Prefab。";
                return;
            }
        }
        finally
        {
            if (root != null)
            {
                PrefabUtility.UnloadPrefabContents(root);
            }
        }

        MarkAdjustedMetadata(prefabPath);
        AssetDatabase.SaveAssets();
        ImportedPropLibrary.ClearCache();
        statusMessage = "已保存。默认列表会隐藏这个模型，下一项会按当前分页位置继续加载。";

        int oldIndex = selectedIndex;
        if (!showAdjustedModels)
        {
            RefreshPrefabList(keepSelection: false, preferredIndex: oldIndex);
        }
        else if (moveToNext && prefabPaths.Count > 0)
        {
            SelectPrefab((selectedIndex + 1) % prefabPaths.Count);
        }
        else
        {
            LoadSelectedPrefabIntoPreview();
        }
    }

    private bool IsSelectedBuiltInPrefab()
    {
        return selectedIndex >= 0 && selectedIndex < prefabPaths.Count &&
               IsBuiltInPrefabPath(prefabPaths[selectedIndex]);
    }

    private static bool IsBuiltInPrefabPath(string prefabPath)
    {
        return BuiltInPropPrefabBuilder.IsBuiltInPrefabPath(prefabPath);
    }

    private void ResetCurrentBuiltInModel()
    {
        if (!IsSelectedBuiltInPrefab())
        {
            return;
        }

        string prefabPath = prefabPaths[selectedIndex];
        string propName = Path.GetFileNameWithoutExtension(prefabPath);
        bool confirmed = EditorUtility.DisplayDialog(
            "重置内置物体：" + propName,
            "这会恢复该内置物体的默认形状、大小和姿态。当前手工调整会被覆盖。",
            "重置",
            "取消");
        if (!confirmed)
        {
            return;
        }

        int oldIndex = selectedIndex;
        ClearPreviewModel();
        BuiltInPropPrefabBuilder.RebuildOne(propName);
        statusMessage = "已重置内置物体：" + propName;
        selectedIndex = -1;
        RefreshPrefabList(keepSelection: false, preferredIndex: oldIndex);
    }

    private void DeleteCurrentModel()
    {
        if (selectedIndex < 0 || selectedIndex >= prefabPaths.Count)
        {
            return;
        }

        string prefabPath = prefabPaths[selectedIndex];
        GameObject prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
        string modelName = prefab != null ? prefab.name : Path.GetFileNameWithoutExtension(prefabPath);
        ImportedPropPlacement placement = prefab != null ? prefab.GetComponent<ImportedPropPlacement>() : null;
        string sourcePath = ResolveSourceAssetPath(placement);

        string sourceDescription = string.IsNullOrWhiteSpace(sourcePath)
            ? "没有找到对应的 RawModels 源文件。"
            : sourcePath;
        int choice = EditorUtility.DisplayDialogComplex(
            "删除模型：" + modelName,
            "将删除调整后的 Prefab，并全量重建 ModelBundles，使视频生成不再使用该模型。\n\n" +
            "Prefab：\n" + prefabPath + "\n\n" +
            "源模型：\n" + sourceDescription + "\n\n" +
            "选择“仅删除生成模型”时，源文件仍保留；以后再次预处理会重新生成该模型。",
            "删除模型和源文件",
            "取消",
            "仅删除生成模型");

        if (choice == 1)
        {
            return;
        }

        ClearPreviewModel();
        bool deletedPrefab = AssetDatabase.DeleteAsset(prefabPath);
        bool deletedSource = false;
        if (choice == 0 && RawModelPreprocessor.IsSupportedRawModelPath(sourcePath))
        {
            deletedSource = AssetDatabase.DeleteAsset(sourcePath);
        }

        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        ImportedPropLibrary.ClearCache();

        string bundleResult;
        try
        {
            int remaining = ModelBundleBuilder.BuildCurrentPrefabs(showDialog: false);
            bundleResult = "ModelBundles 已重建，剩余 " + remaining + " 个导入模型。";
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            bundleResult = "本地模型已删除，但 ModelBundles 重建失败：" + exception.Message;
        }

        statusMessage =
            (deletedPrefab ? "已删除 Prefab。" : "Prefab 删除失败。") +
            (choice == 0
                ? (deletedSource ? " 已删除源模型。" : " 没有删除源模型。")
                : " 源模型已保留。") +
            " " + bundleResult;

        int oldIndex = selectedIndex;
        selectedIndex = -1;
        RefreshPrefabList(keepSelection: false, preferredIndex: oldIndex);
    }

    private static string ResolveSourceAssetPath(ImportedPropPlacement placement)
    {
        if (placement == null)
        {
            return null;
        }

        if (!string.IsNullOrWhiteSpace(placement.sourceAssetGuid))
        {
            string pathFromGuid = AssetDatabase.GUIDToAssetPath(placement.sourceAssetGuid);
            if (RawModelPreprocessor.IsSupportedRawModelPath(pathFromGuid))
            {
                return pathFromGuid;
            }
        }

        return RawModelPreprocessor.IsSupportedRawModelPath(placement.sourceAssetPath)
            ? placement.sourceAssetPath
            : null;
    }

    private static bool IsManuallyAdjustedPath(string prefabPath)
    {
        AssetImporter importer = AssetImporter.GetAtPath(prefabPath);
        if (importer != null && !string.IsNullOrEmpty(importer.userData) &&
            importer.userData.IndexOf(AdjustedMarker, StringComparison.Ordinal) >= 0)
        {
            return true;
        }

        try
        {
            string absolutePath = Path.GetFullPath(prefabPath);
            if (File.Exists(absolutePath))
            {
                foreach (string line in File.ReadLines(absolutePath))
                {
                    if (line.IndexOf("manuallyAdjusted: 1", StringComparison.Ordinal) >= 0)
                    {
                        MarkAdjustedMetadata(prefabPath);
                        return true;
                    }
                }
            }
        }
        catch (Exception exception)
        {
            Debug.LogWarning($"读取摆放状态失败：{prefabPath}，{exception.Message}");
        }

        return false;
    }

    private static void MarkAdjustedMetadata(string prefabPath)
    {
        AssetImporter importer = AssetImporter.GetAtPath(prefabPath);
        if (importer == null)
        {
            return;
        }

        string data = importer.userData ?? string.Empty;
        if (data.IndexOf(AdjustedMarker, StringComparison.Ordinal) >= 0)
        {
            return;
        }

        importer.userData = string.IsNullOrWhiteSpace(data)
            ? AdjustedMarker
            : data.TrimEnd() + "\n" + AdjustedMarker;
        AssetDatabase.WriteImportSettingsIfDirty(prefabPath);
    }

    private static void TrySyncRenamedModels(bool showDialog = false)
    {
        Type synchronizerType = Type.GetType("RawModelRenameSynchronizer, Assembly-CSharp-Editor");
        if (synchronizerType == null)
        {
            return;
        }

        var method = synchronizerType.GetMethod(
            "Sync",
            System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);
        if (method == null)
        {
            return;
        }

        try
        {
            method.Invoke(null, new object[] { showDialog });
        }
        catch (Exception exception)
        {
            Debug.LogWarning($"同步模型重命名失败：{exception.GetBaseException().Message}");
        }
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

        return root.childCount > 0 ? root.GetChild(0) : root;
    }

    private static bool TryCalculateBounds(Transform root, out Bounds bounds)
    {
        bool initialized = false;
        bounds = new Bounds(root != null ? root.position : Vector3.zero, Vector3.zero);
        if (root == null)
        {
            return false;
        }

        foreach (Renderer renderer in root.GetComponentsInChildren<Renderer>(true))
        {
            if (renderer is MeshRenderer)
            {
                MeshFilter filter = renderer.GetComponent<MeshFilter>();
                if (filter == null || filter.sharedMesh == null)
                {
                    continue;
                }
            }
            else if (renderer is SkinnedMeshRenderer skinned && skinned.sharedMesh == null)
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

    private static Vector3 ClampScale(Vector3 scale)
    {
        return new Vector3(
            Mathf.Clamp(Mathf.Abs(scale.x), 0.000001f, 100000f),
            Mathf.Clamp(Mathf.Abs(scale.y), 0.000001f, 100000f),
            Mathf.Clamp(Mathf.Abs(scale.z), 0.000001f, 100000f));
    }

    private static Vector3 NormalizeEuler(Vector3 value)
    {
        return new Vector3(NormalizeAngle(value.x), NormalizeAngle(value.y), NormalizeAngle(value.z));
    }

    private static float NormalizeAngle(float value)
    {
        value %= 360f;
        return value < 0f ? value + 360f : value;
    }
}
#endif
