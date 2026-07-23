using System;
using System.Collections.Generic;
using UnityEngine;

public sealed class MaterialLibrary
{
    private readonly Dictionary<string, Material> materials = new Dictionary<string, Material>();
    private readonly int seed;
    private readonly Shader litShader;

    public MaterialLibrary(int seedValue)
    {
        seed = seedValue;
        litShader = ResolveLitShader();
    }

    public Material Get(string key, Color color, float metallic = 0f, float smoothness = 0.35f)
    {
        if (materials.TryGetValue(key, out Material cached))
        {
            return cached;
        }

        Material material = new Material(litShader)
        {
            name = key
        };

        SetColor(material, color);
        SetFloatIfPresent(material, "_Metallic", metallic);
        SetFloatIfPresent(material, "_Glossiness", smoothness);
        SetFloatIfPresent(material, "_Smoothness", smoothness);

        materials[key] = material;
        return material;
    }

    public Material Wood(string key, Color dark, Color light, float smoothness = 0.38f)
    {
        if (materials.TryGetValue(key, out Material cached))
        {
            return cached;
        }

        Material material = Get(key, Color.white, 0f, smoothness);
        Texture2D texture = CreateWoodTexture(512, 512, dark, light, seed + key.GetHashCode());
        texture.name = key + "_Albedo";
        material.mainTexture = texture;
        material.mainTextureScale = new Vector2(2.5f, 1.5f);
        return material;
    }

    public Material Plaster(string key, Color baseColor)
    {
        if (materials.TryGetValue(key, out Material cached))
        {
            return cached;
        }

        Material material = Get(key, Color.white, 0f, 0.18f);
        Texture2D texture = CreateNoiseTexture(256, 256, baseColor, seed + key.GetHashCode(), 0.025f);
        texture.name = key + "_Albedo";
        material.mainTexture = texture;
        material.mainTextureScale = new Vector2(4f, 4f);
        return material;
    }

    public Material Emissive(string key, Color color, float intensity)
    {
        Material material = Get(key, color, 0f, 0.1f);
        Color emission = color * intensity;
        material.EnableKeyword("_EMISSION");
        if (material.HasProperty("_EmissionColor"))
        {
            material.SetColor("_EmissionColor", emission);
        }

        return material;
    }

    private static Shader ResolveLitShader()
    {
        // Resources.Load keeps this shader in standalone builds and avoids
        // Shader.Find("Standard") returning null after shader stripping.
        Shader shader = Resources.Load<Shader>("ChangeBlindnessLit");
        if (shader != null)
        {
            return shader;
        }

        string[] fallbacks =
        {
            "ChangeBlindness/Lit",
            "Standard",
            "Universal Render Pipeline/Lit",
            "Legacy Shaders/Diffuse",
            "Unlit/Texture",
            "Hidden/InternalErrorShader"
        };

        foreach (string shaderName in fallbacks)
        {
            shader = Shader.Find(shaderName);
            if (shader != null)
            {
                Debug.LogWarning($"Using fallback material shader: {shaderName}");
                return shader;
            }
        }

        throw new InvalidOperationException(
            "No compatible material shader was found. Ensure Assets/Resources/ChangeBlindnessLit.shader is included before building.");
    }

    private static void SetColor(Material material, Color color)
    {
        if (material.HasProperty("_Color"))
        {
            material.SetColor("_Color", color);
        }

        if (material.HasProperty("_BaseColor"))
        {
            material.SetColor("_BaseColor", color);
        }
    }

    private static void SetFloatIfPresent(Material material, string propertyName, float value)
    {
        if (material.HasProperty(propertyName))
        {
            material.SetFloat(propertyName, value);
        }
    }

    private static Texture2D CreateNoiseTexture(int width, int height, Color baseColor, int noiseSeed, float amount)
    {
        Texture2D texture = new Texture2D(width, height, TextureFormat.RGB24, true)
        {
            wrapMode = TextureWrapMode.Repeat,
            filterMode = FilterMode.Trilinear,
            anisoLevel = 8
        };

        var random = new System.Random(noiseSeed);
        Color[] pixels = new Color[width * height];
        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                float noise = ((float)random.NextDouble() * 2f - 1f) * amount;
                float broad = Mathf.PerlinNoise(x * 0.025f + noiseSeed * 0.001f, y * 0.025f) * amount;
                pixels[y * width + x] = new Color(
                    Mathf.Clamp01(baseColor.r + noise + broad),
                    Mathf.Clamp01(baseColor.g + noise + broad),
                    Mathf.Clamp01(baseColor.b + noise + broad));
            }
        }

        texture.SetPixels(pixels);
        texture.Apply(true, false);
        return texture;
    }

    private static Texture2D CreateWoodTexture(int width, int height, Color dark, Color light, int noiseSeed)
    {
        Texture2D texture = new Texture2D(width, height, TextureFormat.RGB24, true)
        {
            wrapMode = TextureWrapMode.Repeat,
            filterMode = FilterMode.Trilinear,
            anisoLevel = 16
        };

        var random = new System.Random(noiseSeed);
        float offset = (float)random.NextDouble() * 100f;
        Color[] pixels = new Color[width * height];
        for (int y = 0; y < height; y++)
        {
            float plank = (y % 96) / 96f;
            float seam = Mathf.Min(plank, 1f - plank);
            float seamDarkening = seam < 0.022f ? -0.18f : 0f;

            for (int x = 0; x < width; x++)
            {
                float grain = Mathf.PerlinNoise(x * 0.035f + offset, y * 0.006f + offset) * 0.68f;
                grain += Mathf.PerlinNoise(x * 0.12f + offset * 0.5f, y * 0.018f) * 0.2f;
                float ring = Mathf.Sin((x * 0.055f + Mathf.PerlinNoise(y * 0.02f, offset) * 9f)) * 0.08f;
                float t = Mathf.Clamp01(grain + ring + seamDarkening);
                pixels[y * width + x] = Color.Lerp(dark, light, t);
            }
        }

        texture.SetPixels(pixels);
        texture.Apply(true, false);
        return texture;
    }
}
