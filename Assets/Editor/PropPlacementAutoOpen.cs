#if UNITY_EDITOR
using UnityEditor;

/// <summary>
/// Provides a manual menu entry for the prop placement window.
/// It intentionally does not use InitializeOnLoad and does not open any
/// EditorWindow while Unity is importing or compiling the project.
/// </summary>
public static class PropPlacementAutoOpen
{
    [MenuItem("Tools/Change Blindness/Open Prop Placement Window")]
    public static void OpenNow()
    {
        PropPlacementWindow.OpenWindow();
    }
}
#endif
