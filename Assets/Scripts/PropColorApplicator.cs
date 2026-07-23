using UnityEngine;

/// <summary>
/// Applies a clearly visible dataset color to imported or procedural props.
///
/// Imported GLB/FBX/OBJ materials use many different shader property names. Setting only
/// _Color or _BaseColor is therefore not reliable. This implementation replaces each
/// visible material instance with a guaranteed recolorable shader while retaining the
/// source albedo texture as grayscale surface detail.
/// </summary>
public static class PropColorApplicator
{
    private static readonly string[] TextureProperties =
    {
        "_BaseMap",
        "_MainTex",
        "_BaseColorTexture",
        "_BaseColorTex",
        "baseColorTexture"
    };

    private static Shader cachedRecolorShader;

    public static void Apply(GameObject root, string colorName)
    {
        if (root == null)
        {
            return;
        }

        DatasetColorDefinition definition = DatasetConfiguration.FindColor(colorName);
        Color target = definition.ToUnityColor();
        Shader recolorShader = GetRecolorShader();
        int recoloredMaterialCount = 0;

        Renderer[] renderers = root.GetComponentsInChildren<Renderer>(true);
        foreach (Renderer renderer in renderers)
        {
            if (renderer == null || IsContactShadow(renderer.transform))
            {
                continue;
            }

            Material[] sourceMaterials = renderer.sharedMaterials;
            if (sourceMaterials == null || sourceMaterials.Length == 0)
            {
                continue;
            }

            Material[] runtimeMaterials = new Material[sourceMaterials.Length];
            for (int i = 0; i < sourceMaterials.Length; i++)
            {
                Material source = sourceMaterials[i];
                runtimeMaterials[i] = CreateRecoloredMaterial(source, recolorShader, target, colorName, i);
                if (runtimeMaterials[i] != null)
                {
                    recoloredMaterialCount++;
                }
            }

            renderer.materials = runtimeMaterials;
        }

        if (recoloredMaterialCount == 0)
        {
            Debug.LogWarning("No render materials were recolored for prop: " + root.name);
        }
    }

    private static Material CreateRecoloredMaterial(
        Material source,
        Shader recolorShader,
        Color target,
        string colorName,
        int materialIndex)
    {
        if (recolorShader == null)
        {
            return ApplyColorPropertiesFallback(source, target);
        }

        Material material = new Material(recolorShader)
        {
            name = (source != null ? source.name : "Material") + "_Dataset_" + colorName + "_" + materialIndex
        };

        Texture texture = FindBaseTexture(source, out Vector2 scale, out Vector2 offset);
        if (texture != null && material.HasProperty("_MainTex"))
        {
            material.SetTexture("_MainTex", texture);
            material.SetTextureScale("_MainTex", scale);
            material.SetTextureOffset("_MainTex", offset);
        }

        material.SetColor("_Color", new Color(target.r, target.g, target.b, 1f));
        material.SetFloat("_Metallic", ReadMetallic(source));
        material.SetFloat("_Glossiness", ReadSmoothness(source));
        return material;
    }

    private static Material ApplyColorPropertiesFallback(Material source, Color target)
    {
        if (source == null)
        {
            return null;
        }

        Material material = new Material(source);
        string[] properties =
        {
            "_Color", "_BaseColor", "_BaseColorFactor", "_TintColor", "_Tint", "_DiffuseColor"
        };
        foreach (string property in properties)
        {
            if (!material.HasProperty(property))
            {
                continue;
            }

            Color original = material.GetColor(property);
            material.SetColor(property, new Color(target.r, target.g, target.b, original.a));
        }
        return material;
    }

    private static Texture FindBaseTexture(Material source, out Vector2 scale, out Vector2 offset)
    {
        scale = Vector2.one;
        offset = Vector2.zero;
        if (source == null)
        {
            return Texture2D.whiteTexture;
        }

        foreach (string property in TextureProperties)
        {
            if (!source.HasProperty(property))
            {
                continue;
            }

            Texture texture = source.GetTexture(property);
            if (texture == null)
            {
                continue;
            }

            scale = source.GetTextureScale(property);
            offset = source.GetTextureOffset(property);
            return texture;
        }

        return Texture2D.whiteTexture;
    }

    private static float ReadMetallic(Material source)
    {
        if (source == null)
        {
            return 0f;
        }
        if (source.HasProperty("_Metallic")) return Mathf.Clamp01(source.GetFloat("_Metallic"));
        if (source.HasProperty("_MetallicFactor")) return Mathf.Clamp01(source.GetFloat("_MetallicFactor"));
        return 0f;
    }

    private static float ReadSmoothness(Material source)
    {
        if (source == null)
        {
            return 0.35f;
        }
        if (source.HasProperty("_Glossiness")) return Mathf.Clamp01(source.GetFloat("_Glossiness"));
        if (source.HasProperty("_Smoothness")) return Mathf.Clamp01(source.GetFloat("_Smoothness"));
        if (source.HasProperty("_RoughnessFactor")) return 1f - Mathf.Clamp01(source.GetFloat("_RoughnessFactor"));
        return 0.35f;
    }

    private static Shader GetRecolorShader()
    {
        if (cachedRecolorShader != null)
        {
            return cachedRecolorShader;
        }

        cachedRecolorShader = Resources.Load<Shader>("PropRecolorLit");
        if (cachedRecolorShader == null)
        {
            cachedRecolorShader = Shader.Find("ChangeBlindness/PropRecolorLit");
        }
        return cachedRecolorShader;
    }

    private static bool IsContactShadow(Transform transform)
    {
        if (transform == null)
        {
            return false;
        }

        return string.Equals(transform.name, "ContactShadow", System.StringComparison.OrdinalIgnoreCase) ||
               (transform.parent != null &&
                string.Equals(transform.parent.name, "ContactShadow", System.StringComparison.OrdinalIgnoreCase));
    }
}
