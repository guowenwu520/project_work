using System.Collections.Generic;
using UnityEngine;

public sealed class ProceduralPropFactory
{
    private readonly MaterialLibrary materials;
    private readonly System.Random random;

    public ProceduralPropFactory(MaterialLibrary materialLibrary, int seed)
    {
        materials = materialLibrary;
        random = new System.Random(seed);
    }

    /// <summary>
    /// Runtime entry point. Prefer the manually adjusted Resources prefab when it exists,
    /// then fall back to direct procedural construction.
    /// </summary>
    public GameObject Create(string propName, Transform parent, Vector3 localPosition, float yawDegrees)
    {
        GameObject adjusted = BuiltInPropLibrary.Create(propName, parent, localPosition, yawDegrees);
        return adjusted != null
            ? adjusted
            : CreateRaw(propName, parent, localPosition, yawDegrees);
    }

    /// <summary>
    /// Builds the raw procedural geometry without consulting Resources.
    /// Used by the Editor prefab generator and as the runtime fallback.
    /// </summary>
    public GameObject CreateRaw(string propName, Transform parent, Vector3 localPosition, float yawDegrees)
    {
        GameObject root = new GameObject(string.IsNullOrWhiteSpace(propName) ? "UnknownProp" : propName);
        root.transform.SetParent(parent, false);
        root.transform.localPosition = localPosition;
        root.transform.localRotation = Quaternion.Euler(0f, yawDegrees, 0f);

        switch ((propName ?? string.Empty).Trim().ToLowerInvariant())
        {
            case "cubeblock": BuildCubeBlock(root.transform); break;
            case "rectangularblock": BuildRectangularBlock(root.transform); break;
            case "tallblock": BuildTallBlock(root.transform); break;
            case "flattile": BuildFlatTile(root.transform); break;
            case "cylindercolumn": BuildCylinderColumn(root.transform); break;
            case "shortcylinder": BuildShortCylinder(root.transform); break;
            case "widedrum": BuildWideDrum(root.transform); break;
            case "conetower": BuildConeTower(root.transform); break;
            case "truncatedcone": BuildTruncatedCone(root.transform); break;
            case "pyramid": BuildPyramid(root.transform); break;
            case "sphereorb": BuildSphereOrb(root.transform); break;
            case "smallsphere": BuildSmallSphere(root.transform); break;
            case "capsulepill": BuildCapsulePill(root.transform); break;
            case "horizontalcapsule": BuildHorizontalCapsule(root.transform); break;
            case "donutring": BuildDonutRing(root.transform); break;
            case "flatring": BuildFlatRing(root.transform); break;
            case "hexprism": BuildHexPrism(root.transform); break;
            case "octprism": BuildOctPrism(root.transform); break;
            case "triprism": BuildTriPrism(root.transform); break;
            case "diamond": BuildDiamond(root.transform); break;
            case "doublecone": BuildDoubleCone(root.transform); break;
            case "hourglass": BuildHourglass(root.transform); break;
            case "mushroom": BuildMushroom(root.transform); break;
            case "dumbbell": BuildDumbbell(root.transform); break;
            case "crossblock": BuildCrossBlock(root.transform); break;
            case "tblock": BuildTBlock(root.transform); break;
            case "lblock": BuildLBlock(root.transform); break;
            case "stepblock": BuildStepBlock(root.transform); break;
            case "arch": BuildArch(root.transform); break;
            case "uframe": BuildUFrame(root.transform); break;
            case "tripodstand": BuildTripodStand(root.transform); break;
            case "snowman": BuildSnowman(root.transform); break;
            case "stackcylinders": BuildStackCylinders(root.transform); break;
            case "stackcubes": BuildStackCubes(root.transform); break;
            case "starcolumn": BuildStarColumn(root.transform); break;
            case "gearwheel": BuildGearWheel(root.transform); break;
            case "bowl": BuildBowl(root.transform); break;
            case "vase": BuildVase(root.transform); break;
            case "bottle": BuildBottle(root.transform); break;
            case "goblet": BuildGoblet(root.transform); break;
            default: BuildCubeBlock(root.transform); break;
        }

        AddContactShadowDisc(root.transform);
        return root;
    }

    private Material MakeBodyMaterial(string key, Color baseColor)
    {
        return materials.Get(key, Jitter(baseColor, 0.015f), 0.08f, 0.48f);
    }

    private Material Gray(string key, float value = 0.72f)
    {
        return MakeBodyMaterial(key, new Color(value, value, value));
    }

    private void Primitive(
        Transform root,
        PrimitiveType type,
        string name,
        Vector3 position,
        Vector3 scale,
        Material material,
        Vector3? rotation = null)
    {
        ScenePrimitives.CreatePrimitive(type, name, root, position, scale, material, rotation);
    }

    private void BuildCubeBlock(Transform root)
    {
        Primitive(root, PrimitiveType.Cube, "Body", new Vector3(0f, 0.11f, 0f), new Vector3(0.22f, 0.22f, 0.22f), Gray("CubeBlockMat"));
    }

    private void BuildRectangularBlock(Transform root)
    {
        Primitive(root, PrimitiveType.Cube, "Body", new Vector3(0f, 0.08f, 0f), new Vector3(0.30f, 0.16f, 0.18f), Gray("RectangularBlockMat", 0.70f));
    }

    private void BuildTallBlock(Transform root)
    {
        Primitive(root, PrimitiveType.Cube, "Body", new Vector3(0f, 0.17f, 0f), new Vector3(0.16f, 0.34f, 0.16f), Gray("TallBlockMat", 0.69f));
    }

    private void BuildFlatTile(Transform root)
    {
        Primitive(root, PrimitiveType.Cube, "Body", new Vector3(0f, 0.035f, 0f), new Vector3(0.30f, 0.07f, 0.23f), Gray("FlatTileMat", 0.76f));
    }

    private void BuildCylinderColumn(Transform root)
    {
        Primitive(root, PrimitiveType.Cylinder, "Body", new Vector3(0f, 0.13f, 0f), new Vector3(0.12f, 0.13f, 0.12f), Gray("CylinderColumnMat", 0.70f));
    }

    private void BuildShortCylinder(Transform root)
    {
        Primitive(root, PrimitiveType.Cylinder, "Body", new Vector3(0f, 0.075f, 0f), new Vector3(0.15f, 0.075f, 0.15f), Gray("ShortCylinderMat", 0.74f));
    }

    private void BuildWideDrum(Transform root)
    {
        Primitive(root, PrimitiveType.Cylinder, "Body", new Vector3(0f, 0.10f, 0f), new Vector3(0.18f, 0.10f, 0.18f), Gray("WideDrumMat", 0.67f));
    }

    private void BuildConeTower(Transform root)
    {
        CreateLathe(root, "Cone", "ConeTowerMat", new List<Vector2>
        {
            new Vector2(0.14f, 0f),
            new Vector2(0.12f, 0.02f),
            new Vector2(0.04f, 0.23f),
            new Vector2(0f, 0.29f)
        }, 0.68f);
    }

    private void BuildTruncatedCone(Transform root)
    {
        CreateLathe(root, "TruncatedCone", "TruncatedConeMat", new List<Vector2>
        {
            new Vector2(0.15f, 0f),
            new Vector2(0.15f, 0.02f),
            new Vector2(0.08f, 0.25f),
            new Vector2(0.08f, 0.27f)
        }, 0.71f);
    }

    private void BuildPyramid(Transform root)
    {
        Mesh pyramid = CreatePyramidMesh(0.26f, 0.30f);
        ScenePrimitives.CreateMeshObject("Pyramid", root, pyramid, Gray("PyramidMat", 0.74f), Vector3.zero, Vector3.one, Vector3.zero);
    }

    private void BuildSphereOrb(Transform root)
    {
        Primitive(root, PrimitiveType.Sphere, "Orb", new Vector3(0f, 0.14f, 0f), new Vector3(0.26f, 0.26f, 0.26f), Gray("SphereOrbMat", 0.73f));
    }

    private void BuildSmallSphere(Transform root)
    {
        Primitive(root, PrimitiveType.Sphere, "Orb", new Vector3(0f, 0.095f, 0f), new Vector3(0.18f, 0.18f, 0.18f), Gray("SmallSphereMat", 0.77f));
    }

    private void BuildCapsulePill(Transform root)
    {
        Primitive(root, PrimitiveType.Capsule, "Body", new Vector3(0f, 0.15f, 0f), new Vector3(0.18f, 0.28f, 0.18f), Gray("CapsulePillMat", 0.70f));
    }

    private void BuildHorizontalCapsule(Transform root)
    {
        Primitive(root, PrimitiveType.Capsule, "Body", new Vector3(0f, 0.12f, 0f), new Vector3(0.12f, 0.13f, 0.12f), Gray("HorizontalCapsuleMat", 0.72f), new Vector3(0f, 0f, 90f));
    }

    private void BuildDonutRing(Transform root)
    {
        Mesh torus = ScenePrimitives.CreateTorus(0.13f, 0.045f, 28, 12);
        ScenePrimitives.CreateMeshObject("Ring", root, torus, Gray("DonutRingMat", 0.72f), new Vector3(0f, 0.14f, 0f), Vector3.one, new Vector3(90f, 0f, 0f));
    }

    private void BuildFlatRing(Transform root)
    {
        Mesh torus = ScenePrimitives.CreateTorus(0.13f, 0.045f, 28, 12);
        ScenePrimitives.CreateMeshObject("Ring", root, torus, Gray("FlatRingMat", 0.74f), new Vector3(0f, 0.05f, 0f), Vector3.one, Vector3.zero);
    }

    private void BuildHexPrism(Transform root)
    {
        ScenePrimitives.CreateMeshObject("HexPrism", root, CreatePrismMesh(6, 0.13f, 0.28f), Gray("HexPrismMat", 0.71f), Vector3.zero, Vector3.one, Vector3.zero);
    }

    private void BuildOctPrism(Transform root)
    {
        ScenePrimitives.CreateMeshObject("OctPrism", root, CreatePrismMesh(8, 0.13f, 0.26f), Gray("OctPrismMat", 0.73f), Vector3.zero, Vector3.one, Vector3.zero);
    }

    private void BuildTriPrism(Transform root)
    {
        ScenePrimitives.CreateMeshObject("TriPrism", root, CreateTriPrismMesh(0.26f, 0.22f, 0.22f), Gray("TriPrismMat", 0.71f), Vector3.zero, Vector3.one, Vector3.zero);
    }

    private void BuildDiamond(Transform root)
    {
        ScenePrimitives.CreateMeshObject("Diamond", root, CreateDiamondMesh(0.18f, 0.32f), Gray("DiamondMat", 0.76f), Vector3.zero, Vector3.one, Vector3.zero);
    }

    private void BuildDoubleCone(Transform root)
    {
        CreateLathe(root, "DoubleCone", "DoubleConeMat", new List<Vector2>
        {
            new Vector2(0f, 0f),
            new Vector2(0.15f, 0.14f),
            new Vector2(0f, 0.30f)
        }, 0.69f);
    }

    private void BuildHourglass(Transform root)
    {
        CreateLathe(root, "Hourglass", "HourglassMat", new List<Vector2>
        {
            new Vector2(0.13f, 0f),
            new Vector2(0.13f, 0.025f),
            new Vector2(0.05f, 0.13f),
            new Vector2(0.13f, 0.245f),
            new Vector2(0.13f, 0.27f)
        }, 0.72f);
    }

    private void BuildMushroom(Transform root)
    {
        Material mat = Gray("MushroomMat", 0.72f);
        Primitive(root, PrimitiveType.Cylinder, "Stem", new Vector3(0f, 0.09f, 0f), new Vector3(0.055f, 0.09f, 0.055f), mat);
        Primitive(root, PrimitiveType.Sphere, "Cap", new Vector3(0f, 0.21f, 0f), new Vector3(0.25f, 0.13f, 0.25f), mat);
    }

    private void BuildDumbbell(Transform root)
    {
        Material mat = Gray("DumbbellMat", 0.68f);
        Primitive(root, PrimitiveType.Cylinder, "Handle", new Vector3(0f, 0.11f, 0f), new Vector3(0.045f, 0.13f, 0.045f), mat, new Vector3(0f, 0f, 90f));
        Primitive(root, PrimitiveType.Sphere, "LeftWeight", new Vector3(-0.16f, 0.11f, 0f), new Vector3(0.11f, 0.11f, 0.11f), mat);
        Primitive(root, PrimitiveType.Sphere, "RightWeight", new Vector3(0.16f, 0.11f, 0f), new Vector3(0.11f, 0.11f, 0.11f), mat);
    }

    private void BuildCrossBlock(Transform root)
    {
        Material mat = Gray("CrossBlockMat", 0.72f);
        Primitive(root, PrimitiveType.Cube, "Horizontal", new Vector3(0f, 0.10f, 0f), new Vector3(0.30f, 0.09f, 0.10f), mat);
        Primitive(root, PrimitiveType.Cube, "Vertical", new Vector3(0f, 0.10f, 0f), new Vector3(0.10f, 0.09f, 0.30f), mat);
    }

    private void BuildTBlock(Transform root)
    {
        Material mat = Gray("TBlockMat", 0.70f);
        Primitive(root, PrimitiveType.Cube, "Stem", new Vector3(0f, 0.12f, 0f), new Vector3(0.09f, 0.24f, 0.10f), mat);
        Primitive(root, PrimitiveType.Cube, "Top", new Vector3(0f, 0.27f, 0f), new Vector3(0.29f, 0.08f, 0.10f), mat);
    }

    private void BuildLBlock(Transform root)
    {
        Material mat = Gray("LBlockMat", 0.74f);
        Primitive(root, PrimitiveType.Cube, "Vertical", new Vector3(-0.09f, 0.14f, 0f), new Vector3(0.10f, 0.28f, 0.12f), mat);
        Primitive(root, PrimitiveType.Cube, "Foot", new Vector3(0.03f, 0.05f, 0f), new Vector3(0.24f, 0.10f, 0.12f), mat);
    }

    private void BuildStepBlock(Transform root)
    {
        Material mat = Gray("StepBlockMat", 0.69f);
        Primitive(root, PrimitiveType.Cube, "Step1", new Vector3(-0.10f, 0.05f, 0f), new Vector3(0.10f, 0.10f, 0.20f), mat);
        Primitive(root, PrimitiveType.Cube, "Step2", new Vector3(0f, 0.10f, 0f), new Vector3(0.10f, 0.20f, 0.20f), mat);
        Primitive(root, PrimitiveType.Cube, "Step3", new Vector3(0.10f, 0.15f, 0f), new Vector3(0.10f, 0.30f, 0.20f), mat);
    }

    private void BuildArch(Transform root)
    {
        Material mat = Gray("ArchMat", 0.73f);
        Primitive(root, PrimitiveType.Cube, "LeftPillar", new Vector3(-0.105f, 0.12f, 0f), new Vector3(0.07f, 0.24f, 0.10f), mat);
        Primitive(root, PrimitiveType.Cube, "RightPillar", new Vector3(0.105f, 0.12f, 0f), new Vector3(0.07f, 0.24f, 0.10f), mat);
        Primitive(root, PrimitiveType.Cube, "Top", new Vector3(0f, 0.27f, 0f), new Vector3(0.28f, 0.07f, 0.10f), mat);
    }

    private void BuildUFrame(Transform root)
    {
        Material mat = Gray("UFrameMat", 0.71f);
        Primitive(root, PrimitiveType.Cube, "LeftPillar", new Vector3(-0.105f, 0.15f, 0f), new Vector3(0.07f, 0.30f, 0.10f), mat);
        Primitive(root, PrimitiveType.Cube, "RightPillar", new Vector3(0.105f, 0.15f, 0f), new Vector3(0.07f, 0.30f, 0.10f), mat);
        Primitive(root, PrimitiveType.Cube, "Bottom", new Vector3(0f, 0.035f, 0f), new Vector3(0.28f, 0.07f, 0.10f), mat);
    }

    private void BuildTripodStand(Transform root)
    {
        Material mat = Gray("TripodStandMat", 0.67f);
        for (int i = 0; i < 3; i++)
        {
            float angle = i * 120f;
            float radians = angle * Mathf.Deg2Rad;
            Vector3 position = new Vector3(Mathf.Cos(radians) * 0.08f, 0.12f, Mathf.Sin(radians) * 0.08f);
            Primitive(root, PrimitiveType.Cylinder, "Leg" + i, position, new Vector3(0.025f, 0.13f, 0.025f), mat, new Vector3(18f * Mathf.Sin(radians), angle, -18f * Mathf.Cos(radians)));
        }
        Primitive(root, PrimitiveType.Sphere, "Top", new Vector3(0f, 0.25f, 0f), new Vector3(0.10f, 0.10f, 0.10f), mat);
    }

    private void BuildSnowman(Transform root)
    {
        Material mat = Gray("SnowmanMat", 0.78f);
        Primitive(root, PrimitiveType.Sphere, "Lower", new Vector3(0f, 0.10f, 0f), new Vector3(0.20f, 0.20f, 0.20f), mat);
        Primitive(root, PrimitiveType.Sphere, "Upper", new Vector3(0f, 0.235f, 0f), new Vector3(0.14f, 0.14f, 0.14f), mat);
    }

    private void BuildStackCylinders(Transform root)
    {
        Material mat = Gray("StackCylindersMat", 0.70f);
        Primitive(root, PrimitiveType.Cylinder, "Lower", new Vector3(0f, 0.055f, 0f), new Vector3(0.16f, 0.055f, 0.16f), mat);
        Primitive(root, PrimitiveType.Cylinder, "Middle", new Vector3(0f, 0.14f, 0f), new Vector3(0.12f, 0.04f, 0.12f), mat);
        Primitive(root, PrimitiveType.Cylinder, "Upper", new Vector3(0f, 0.205f, 0f), new Vector3(0.08f, 0.025f, 0.08f), mat);
    }

    private void BuildStackCubes(Transform root)
    {
        Material mat = Gray("StackCubesMat", 0.73f);
        Primitive(root, PrimitiveType.Cube, "Lower", new Vector3(0f, 0.065f, 0f), new Vector3(0.22f, 0.13f, 0.22f), mat);
        Primitive(root, PrimitiveType.Cube, "Middle", new Vector3(0f, 0.165f, 0f), new Vector3(0.16f, 0.07f, 0.16f), mat);
        Primitive(root, PrimitiveType.Cube, "Upper", new Vector3(0f, 0.235f, 0f), new Vector3(0.10f, 0.07f, 0.10f), mat);
    }

    private void BuildStarColumn(Transform root)
    {
        Mesh mesh = CreateStarPrismMesh(5, 0.14f, 0.065f, 0.28f);
        ScenePrimitives.CreateMeshObject("StarColumn", root, mesh, Gray("StarColumnMat", 0.75f), Vector3.zero, Vector3.one, Vector3.zero);
    }

    private void BuildGearWheel(Transform root)
    {
        Material mat = Gray("GearWheelMat", 0.68f);
        Mesh torus = ScenePrimitives.CreateTorus(0.10f, 0.035f, 28, 10);
        ScenePrimitives.CreateMeshObject("Wheel", root, torus, mat, new Vector3(0f, 0.15f, 0f), Vector3.one, new Vector3(90f, 0f, 0f));
        for (int i = 0; i < 8; i++)
        {
            float angle = i * 45f;
            float radians = angle * Mathf.Deg2Rad;
            Vector3 position = new Vector3(Mathf.Cos(radians) * 0.145f, 0.15f + Mathf.Sin(radians) * 0.145f, 0f);
            Primitive(root, PrimitiveType.Cube, "Tooth" + i, position, new Vector3(0.055f, 0.035f, 0.07f), mat, new Vector3(0f, 0f, angle));
        }
    }

    private void BuildBowl(Transform root)
    {
        CreateLathe(root, "Bowl", "BowlMat", new List<Vector2>
        {
            new Vector2(0.05f, 0f),
            new Vector2(0.13f, 0.015f),
            new Vector2(0.16f, 0.06f),
            new Vector2(0.15f, 0.11f),
            new Vector2(0.11f, 0.145f),
            new Vector2(0.04f, 0.155f)
        }, 0.76f);
    }

    private void BuildVase(Transform root)
    {
        CreateLathe(root, "Vase", "VaseMat", new List<Vector2>
        {
            new Vector2(0.07f, 0f),
            new Vector2(0.12f, 0.035f),
            new Vector2(0.14f, 0.12f),
            new Vector2(0.10f, 0.22f),
            new Vector2(0.055f, 0.27f),
            new Vector2(0.06f, 0.30f)
        }, 0.72f);
    }

    private void BuildBottle(Transform root)
    {
        CreateLathe(root, "Bottle", "BottleMat", new List<Vector2>
        {
            new Vector2(0.08f, 0f),
            new Vector2(0.11f, 0.025f),
            new Vector2(0.11f, 0.18f),
            new Vector2(0.065f, 0.235f),
            new Vector2(0.045f, 0.25f),
            new Vector2(0.045f, 0.31f),
            new Vector2(0.055f, 0.32f)
        }, 0.70f);
    }

    private void BuildGoblet(Transform root)
    {
        CreateLathe(root, "Goblet", "GobletMat", new List<Vector2>
        {
            new Vector2(0.09f, 0f),
            new Vector2(0.10f, 0.018f),
            new Vector2(0.025f, 0.035f),
            new Vector2(0.025f, 0.14f),
            new Vector2(0.08f, 0.155f),
            new Vector2(0.13f, 0.21f),
            new Vector2(0.12f, 0.29f),
            new Vector2(0.08f, 0.31f)
        }, 0.75f);
    }

    private void CreateLathe(Transform root, string objectName, string materialName, List<Vector2> profile, float gray)
    {
        Mesh mesh = ScenePrimitives.CreateLathe(profile, 32);
        ScenePrimitives.CreateMeshObject(objectName, root, mesh, Gray(materialName, gray), Vector3.zero, Vector3.one, Vector3.zero);
    }

    private Mesh CreatePyramidMesh(float baseSize, float height)
    {
        float half = baseSize * 0.5f;
        Vector3[] vertices =
        {
            new Vector3(-half, 0f, -half), new Vector3(half, 0f, -half),
            new Vector3(half, 0f, half), new Vector3(-half, 0f, half),
            new Vector3(0f, height, 0f)
        };
        int[] triangles =
        {
            0, 2, 1, 0, 3, 2,
            0, 1, 4, 1, 2, 4,
            2, 3, 4, 3, 0, 4
        };
        return BuildMesh("PyramidMesh", vertices, triangles);
    }

    private Mesh CreatePrismMesh(int sides, float radius, float height)
    {
        List<Vector3> vertices = new List<Vector3>();
        List<int> triangles = new List<int>();
        for (int i = 0; i < sides; i++)
        {
            float angle = i / (float)sides * Mathf.PI * 2f;
            vertices.Add(new Vector3(Mathf.Cos(angle) * radius, 0f, Mathf.Sin(angle) * radius));
        }
        for (int i = 0; i < sides; i++)
        {
            float angle = i / (float)sides * Mathf.PI * 2f;
            vertices.Add(new Vector3(Mathf.Cos(angle) * radius, height, Mathf.Sin(angle) * radius));
        }

        int bottomCenter = vertices.Count;
        vertices.Add(Vector3.zero);
        int topCenter = vertices.Count;
        vertices.Add(new Vector3(0f, height, 0f));

        for (int i = 0; i < sides; i++)
        {
            int next = (i + 1) % sides;
            triangles.Add(i); triangles.Add(next + sides); triangles.Add(i + sides);
            triangles.Add(i); triangles.Add(next); triangles.Add(next + sides);
            triangles.Add(bottomCenter); triangles.Add(next); triangles.Add(i);
            triangles.Add(topCenter); triangles.Add(i + sides); triangles.Add(next + sides);
        }
        return BuildMesh("PrismMesh", vertices.ToArray(), triangles.ToArray());
    }

    private Mesh CreateTriPrismMesh(float width, float depth, float height)
    {
        float halfWidth = width * 0.5f;
        float halfDepth = depth * 0.5f;
        Vector3[] vertices =
        {
            new Vector3(-halfWidth, 0f, -halfDepth), new Vector3(halfWidth, 0f, -halfDepth), new Vector3(0f, height, -halfDepth),
            new Vector3(-halfWidth, 0f, halfDepth), new Vector3(halfWidth, 0f, halfDepth), new Vector3(0f, height, halfDepth)
        };
        int[] triangles =
        {
            0, 1, 2, 5, 4, 3,
            0, 3, 4, 0, 4, 1,
            1, 4, 5, 1, 5, 2,
            2, 5, 3, 2, 3, 0
        };
        return BuildMesh("TriPrismMesh", vertices, triangles);
    }

    private Mesh CreateDiamondMesh(float radius, float height)
    {
        float middleY = height * 0.5f;
        Vector3[] vertices =
        {
            new Vector3(0f, height, 0f),
            new Vector3(radius, middleY, 0f),
            new Vector3(0f, middleY, radius),
            new Vector3(-radius, middleY, 0f),
            new Vector3(0f, middleY, -radius),
            Vector3.zero
        };
        int[] triangles =
        {
            0, 2, 1, 0, 3, 2, 0, 4, 3, 0, 1, 4,
            5, 1, 2, 5, 2, 3, 5, 3, 4, 5, 4, 1
        };
        return BuildMesh("DiamondMesh", vertices, triangles);
    }

    private Mesh CreateStarPrismMesh(int points, float outerRadius, float innerRadius, float height)
    {
        int ringCount = points * 2;
        List<Vector3> vertices = new List<Vector3>();
        List<int> triangles = new List<int>();

        for (int layer = 0; layer < 2; layer++)
        {
            float y = layer == 0 ? 0f : height;
            for (int i = 0; i < ringCount; i++)
            {
                float radius = i % 2 == 0 ? outerRadius : innerRadius;
                float angle = i / (float)ringCount * Mathf.PI * 2f;
                vertices.Add(new Vector3(Mathf.Cos(angle) * radius, y, Mathf.Sin(angle) * radius));
            }
        }

        int bottomCenter = vertices.Count;
        vertices.Add(Vector3.zero);
        int topCenter = vertices.Count;
        vertices.Add(new Vector3(0f, height, 0f));

        for (int i = 0; i < ringCount; i++)
        {
            int next = (i + 1) % ringCount;
            int bottomA = i;
            int bottomB = next;
            int topA = i + ringCount;
            int topB = next + ringCount;

            triangles.Add(bottomA); triangles.Add(topB); triangles.Add(topA);
            triangles.Add(bottomA); triangles.Add(bottomB); triangles.Add(topB);
            triangles.Add(bottomCenter); triangles.Add(bottomB); triangles.Add(bottomA);
            triangles.Add(topCenter); triangles.Add(topA); triangles.Add(topB);
        }

        return BuildMesh("StarPrismMesh", vertices.ToArray(), triangles.ToArray());
    }

    private Mesh BuildMesh(string name, Vector3[] vertices, int[] triangles)
    {
        Mesh mesh = new Mesh { name = name };
        mesh.vertices = vertices;
        mesh.triangles = triangles;
        mesh.RecalculateNormals();
        mesh.RecalculateTangents();
        mesh.RecalculateBounds();
        return mesh;
    }

    private void AddContactShadowDisc(Transform root)
    {
        Material shadow = materials.Get("ContactShadow", new Color(0.028f, 0.023f, 0.018f, 1f), 0f, 0f);
        GameObject disc = ScenePrimitives.CreatePrimitive(
            PrimitiveType.Cylinder,
            "ContactShadow",
            root,
            new Vector3(0f, 0.002f, 0f),
            new Vector3(0.18f, 0.001f, 0.18f),
            shadow);
        Renderer renderer = disc.GetComponent<Renderer>();
        if (renderer != null)
        {
            renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            renderer.receiveShadows = false;
        }
    }

    private Color Jitter(Color source, float amount)
    {
        float red = ((float)random.NextDouble() * 2f - 1f) * amount;
        float green = ((float)random.NextDouble() * 2f - 1f) * amount;
        float blue = ((float)random.NextDouble() * 2f - 1f) * amount;
        return new Color(
            Mathf.Clamp01(source.r + red),
            Mathf.Clamp01(source.g + green),
            Mathf.Clamp01(source.b + blue),
            source.a);
    }
}
