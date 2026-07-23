using UnityEngine;

[ExecuteAlways]
[RequireComponent(typeof(Camera))]
public sealed class CinematicPostFX : MonoBehaviour
{
    [Range(0.5f, 2f)] public float exposure = 1.16f;
    [Range(0.5f, 1.5f)] public float contrast = 1.06f;
    [Range(0f, 2f)] public float saturation = 1.0f;
    [Range(0f, 1f)] public float vignette = 0.08f;
    [Range(0f, 0.08f)] public float grain = 0.008f;

    private Material material;

    private void OnEnable()
    {
        Shader shader = Resources.Load<Shader>("CinematicPostFX");
        if (shader == null)
        {
            shader = Shader.Find("Hidden/ChangeBlindness/CinematicPostFX");
        }

        if (shader != null)
        {
            material = new Material(shader) { hideFlags = HideFlags.HideAndDontSave };
        }
        else
        {
            Debug.LogWarning("Cinematic post-processing shader is unavailable; rendering without post FX.");
        }
    }

    private void OnDisable()
    {
        if (material != null)
        {
            if (Application.isPlaying)
            {
                Destroy(material);
            }
            else
            {
                DestroyImmediate(material);
            }
        }
    }

    private void OnRenderImage(RenderTexture source, RenderTexture destination)
    {
        if (material == null)
        {
            Graphics.Blit(source, destination);
            return;
        }

        material.SetFloat("_Exposure", exposure);
        material.SetFloat("_Contrast", contrast);
        material.SetFloat("_Saturation", saturation);
        material.SetFloat("_Vignette", vignette);
        material.SetFloat("_Grain", grain);
        material.SetFloat("_TimeSeed", Application.isPlaying ? Time.time * 11.731f : 0f);
        Graphics.Blit(source, destination, material);
    }
}
