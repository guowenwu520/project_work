using System;
using System.Collections.Generic;
using UnityEngine;

public static class ScenePrimitives
{
    public static GameObject CreatePrimitive(
        PrimitiveType type,
        string name,
        Transform parent,
        Vector3 localPosition,
        Vector3 localScale,
        Material material,
        Vector3? localEulerAngles = null)
    {
        GameObject instance = GameObject.CreatePrimitive(type);
        instance.name = name;
        instance.transform.SetParent(parent, false);
        instance.transform.localPosition = localPosition;
        instance.transform.localScale = localScale;
        instance.transform.localEulerAngles = localEulerAngles ?? Vector3.zero;

        Renderer renderer = instance.GetComponent<Renderer>();
        if (renderer != null && material != null)
        {
            renderer.sharedMaterial = material;
        }

        Collider collider = instance.GetComponent<Collider>();
        if (collider != null)
        {
            collider.enabled = false;
        }

        return instance;
    }

    public static GameObject CreateMeshObject(
        string name,
        Transform parent,
        Mesh mesh,
        Material material,
        Vector3 localPosition,
        Vector3 localScale,
        Vector3 localEulerAngles)
    {
        GameObject instance = new GameObject(name);
        instance.transform.SetParent(parent, false);
        instance.transform.localPosition = localPosition;
        instance.transform.localScale = localScale;
        instance.transform.localEulerAngles = localEulerAngles;

        MeshFilter filter = instance.AddComponent<MeshFilter>();
        filter.sharedMesh = mesh;
        MeshRenderer renderer = instance.AddComponent<MeshRenderer>();
        renderer.sharedMaterial = material;
        return instance;
    }

    public static Mesh CreateTorus(float majorRadius, float minorRadius, int majorSegments = 32, int minorSegments = 12)
    {
        var vertices = new List<Vector3>((majorSegments + 1) * (minorSegments + 1));
        var normals = new List<Vector3>(vertices.Capacity);
        var uvs = new List<Vector2>(vertices.Capacity);
        var triangles = new List<int>(majorSegments * minorSegments * 6);

        for (int i = 0; i <= majorSegments; i++)
        {
            float u = i / (float)majorSegments;
            float theta = u * Mathf.PI * 2f;
            Vector3 ringCenter = new Vector3(Mathf.Cos(theta) * majorRadius, 0f, Mathf.Sin(theta) * majorRadius);

            for (int j = 0; j <= minorSegments; j++)
            {
                float v = j / (float)minorSegments;
                float phi = v * Mathf.PI * 2f;
                Vector3 normal = new Vector3(
                    Mathf.Cos(theta) * Mathf.Cos(phi),
                    Mathf.Sin(phi),
                    Mathf.Sin(theta) * Mathf.Cos(phi));
                vertices.Add(ringCenter + normal * minorRadius);
                normals.Add(normal.normalized);
                uvs.Add(new Vector2(u, v));
            }
        }

        int stride = minorSegments + 1;
        for (int i = 0; i < majorSegments; i++)
        {
            for (int j = 0; j < minorSegments; j++)
            {
                int a = i * stride + j;
                int b = (i + 1) * stride + j;
                int c = (i + 1) * stride + j + 1;
                int d = i * stride + j + 1;
                triangles.Add(a); triangles.Add(b); triangles.Add(c);
                triangles.Add(a); triangles.Add(c); triangles.Add(d);
            }
        }

        Mesh mesh = new Mesh { name = "ProceduralTorus" };
        mesh.SetVertices(vertices);
        mesh.SetNormals(normals);
        mesh.SetUVs(0, uvs);
        mesh.SetTriangles(triangles, 0);
        mesh.RecalculateBounds();
        return mesh;
    }

    public static Mesh CreateLathe(IReadOnlyList<Vector2> profile, int radialSegments = 32, bool closeEnds = true)
    {
        if (profile == null || profile.Count < 2)
        {
            throw new ArgumentException("Lathe profile needs at least two points.");
        }

        var vertices = new List<Vector3>((radialSegments + 1) * profile.Count);
        var uvs = new List<Vector2>(vertices.Capacity);
        var triangles = new List<int>(radialSegments * (profile.Count - 1) * 6);

        for (int r = 0; r <= radialSegments; r++)
        {
            float u = r / (float)radialSegments;
            float angle = u * Mathf.PI * 2f;
            float cos = Mathf.Cos(angle);
            float sin = Mathf.Sin(angle);

            for (int p = 0; p < profile.Count; p++)
            {
                Vector2 point = profile[p];
                vertices.Add(new Vector3(point.x * cos, point.y, point.x * sin));
                uvs.Add(new Vector2(u, p / (float)(profile.Count - 1)));
            }
        }

        int stride = profile.Count;
        for (int r = 0; r < radialSegments; r++)
        {
            for (int p = 0; p < profile.Count - 1; p++)
            {
                int a = r * stride + p;
                int b = (r + 1) * stride + p;
                int c = (r + 1) * stride + p + 1;
                int d = r * stride + p + 1;
                triangles.Add(a); triangles.Add(c); triangles.Add(b);
                triangles.Add(a); triangles.Add(d); triangles.Add(c);
            }
        }

        Mesh mesh = new Mesh { name = "ProceduralLathe" };
        mesh.SetVertices(vertices);
        mesh.SetUVs(0, uvs);
        mesh.SetTriangles(triangles, 0);
        mesh.RecalculateNormals();
        mesh.RecalculateTangents();
        mesh.RecalculateBounds();
        return mesh;
    }
}
