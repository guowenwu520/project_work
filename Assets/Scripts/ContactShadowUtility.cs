using UnityEngine;
using UnityEngine.Rendering;

public static class ContactShadowUtility
{
    private static Material cachedMaterial;

    public static void EnsureContactShadow(Transform parentRoot, Bounds worldBounds, float tableY)
    {
        if (parentRoot == null)
        {
            return;
        }

        Transform existing = parentRoot.Find("ContactShadow");
        if (existing != null)
        {
            if (Application.isPlaying)
            {
                Object.Destroy(existing.gameObject);
            }
            else
            {
                Object.DestroyImmediate(existing.gameObject);
            }
        }

        float width = Mathf.Clamp(worldBounds.size.x * 0.92f, 0.06f, 0.48f);
        float depth = Mathf.Clamp(worldBounds.size.z * 0.92f, 0.06f, 0.48f);
        float flatness = worldBounds.size.y / Mathf.Max(0.0001f, Mathf.Max(worldBounds.size.x, worldBounds.size.z));
        float alpha = Mathf.Lerp(0.16f, 0.28f, Mathf.Clamp01(0.55f - flatness));

        GameObject quad = GameObject.CreatePrimitive(PrimitiveType.Quad);
        quad.name = "ContactShadow";
        quad.transform.SetParent(parentRoot, false);
        Vector3 worldCenter = new Vector3(worldBounds.center.x, tableY + 0.0025f, worldBounds.center.z);
        quad.transform.localPosition = parentRoot.InverseTransformPoint(worldCenter);
        quad.transform.localRotation = Quaternion.Euler(90f, 0f, 0f);
        quad.transform.localScale = new Vector3(width, depth, 1f);

        Collider collider = quad.GetComponent<Collider>();
        if (collider != null)
        {
            if (Application.isPlaying)
            {
                Object.Destroy(collider);
            }
            else
            {
                Object.DestroyImmediate(collider);
            }
        }

        Renderer renderer = quad.GetComponent<Renderer>();
        renderer.shadowCastingMode = ShadowCastingMode.Off;
        renderer.receiveShadows = false;
        renderer.motionVectorGenerationMode = MotionVectorGenerationMode.ForceNoMotion;
        renderer.lightProbeUsage = LightProbeUsage.Off;
        renderer.reflectionProbeUsage = ReflectionProbeUsage.Off;
        renderer.sharedMaterial = GetMaterial();

        MaterialPropertyBlock block = new MaterialPropertyBlock();
        block.SetColor("_Color", new Color(0f, 0f, 0f, alpha));
        renderer.SetPropertyBlock(block);
    }

    private static Material GetMaterial()
    {
        if (cachedMaterial != null)
        {
            return cachedMaterial;
        }

        Shader shader = Resources.Load<Shader>("ContactShadow");
        if (shader == null)
        {
            shader = Shader.Find("Unlit/Transparent");
        }

        if (shader == null)
        {
            return null;
        }

        cachedMaterial = new Material(shader)
        {
            name = "RuntimeContactShadowMaterial",
            hideFlags = HideFlags.HideAndDontSave
        };
        return cachedMaterial;
    }
}
