using System;
using System.Collections.Generic;

[Serializable]
public sealed class ImportedPropManifestEntry
{
    public string name;
    public string displayName;
    public string bundleFile;
    public string assetName;
}

[Serializable]
public sealed class ImportedPropManifestData
{
    public string version = "unity-assetbundle-v1";
    public List<ImportedPropManifestEntry> props = new List<ImportedPropManifestEntry>();
}
