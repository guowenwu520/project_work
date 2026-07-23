using UnityEngine;

/// <summary>
/// Marks a generated tabletop prop whose authored transform should be preserved at runtime.
/// </summary>
public sealed class ImportedPropPlacement : MonoBehaviour
{
    [Tooltip("Keep the prefab's authored size and orientation instead of applying runtime auto-fit.")]
    public bool preserveAuthoredPlacement = true;

    [Tooltip("Set by the placement editor after the user saves manual adjustments.")]
    public bool manuallyAdjusted;

    [Tooltip("Current source model asset path. This is synchronized after a model is renamed or moved.")]
    public string sourceAssetPath;

    [Tooltip("Stable Unity GUID of the source model. Used to track renames and moves.")]
    public string sourceAssetGuid;
}
