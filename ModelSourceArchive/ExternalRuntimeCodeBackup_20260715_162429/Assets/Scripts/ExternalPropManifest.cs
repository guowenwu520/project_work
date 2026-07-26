using System;
using System.Collections.Generic;
using UnityEngine;

[Serializable]
public sealed class ExternalPropManifestEntry
{
    // Stable runtime key used by dataset generation and QA.
    public string name;

    // Human-readable label. When empty, name is used.
    public string displayName;

    // Path relative to ExternalModels, for example models/Apple.glb.
    public string file;

    // Transform previously authored in the Unity placement editor.
    public Vector3 localPosition = Vector3.zero;
    public Vector3 localEulerAngles = Vector3.zero;
    public Vector3 localScale = Vector3.one;

    // False means the runtime loader auto-fits the object to a tabletop size.
    public bool manuallyAdjusted;
}

[Serializable]
public sealed class ExternalPropManifestData
{
    public string version = "external-glb-v1";
    public List<ExternalPropManifestEntry> props = new List<ExternalPropManifestEntry>();
}
