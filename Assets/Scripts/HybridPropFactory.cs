using UnityEngine;

public sealed class HybridPropFactory
{
    private readonly ProceduralPropFactory procedural;

    public HybridPropFactory(MaterialLibrary materialLibrary, int seed)
    {
        procedural = new ProceduralPropFactory(materialLibrary, seed);
    }

    public GameObject Create(string propName, Transform parent, Vector3 localPosition, float yawDegrees)
    {
        GameObject imported = ImportedPropLibrary.Create(propName, parent, localPosition, yawDegrees);
        if (imported != null)
        {
            return imported;
        }

        return procedural.Create(propName, parent, localPosition, yawDegrees);
    }
}
