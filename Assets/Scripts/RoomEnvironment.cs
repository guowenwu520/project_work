using UnityEngine;
using UnityEngine.Rendering;

public sealed class RoomEnvironment
{
    public const float TableTopSurfaceY = 0.94f;
    public Vector3 TableCenter { get; private set; }
    public Transform Root { get; private set; }

    private readonly MaterialLibrary materials;

    public RoomEnvironment(MaterialLibrary materialLibrary)
    {
        materials = materialLibrary;
    }

    public void Build()
    {
        Root = new GameObject("RoomEnvironment").transform;
        TableCenter = new Vector3(0f, TableTopSurfaceY, 0.35f);

        ConfigureRenderSettings();
        BuildArchitecture();
        BuildTable();
        BuildBackgroundDecor();
        BuildLighting();
    }

    private void ConfigureRenderSettings()
    {
        QualitySettings.shadows = ShadowQuality.All;
        QualitySettings.shadowResolution = ShadowResolution.VeryHigh;
        QualitySettings.shadowDistance = 35f;
        QualitySettings.antiAliasing = 8;
        QualitySettings.anisotropicFiltering = AnisotropicFiltering.ForceEnable;
        QualitySettings.realtimeReflectionProbes = true;
        QualitySettings.vSyncCount = 0;

        RenderSettings.ambientMode = AmbientMode.Trilight;
        RenderSettings.ambientSkyColor = new Color(0.64f, 0.67f, 0.72f);
        RenderSettings.ambientEquatorColor = new Color(0.44f, 0.43f, 0.40f);
        RenderSettings.ambientGroundColor = new Color(0.24f, 0.22f, 0.20f);
        RenderSettings.ambientIntensity = 1.22f;
        RenderSettings.reflectionIntensity = 0.96f;
        RenderSettings.fog = true;
        RenderSettings.fogMode = FogMode.ExponentialSquared;
        RenderSettings.fogColor = new Color(0.68f, 0.69f, 0.70f);
        RenderSettings.fogDensity = 0.0009f;
    }

    private void BuildArchitecture()
    {
        Material floor = materials.Plaster(
            "FloorStone",
            new Color(0.44f, 0.46f, 0.48f));
        floor.mainTextureScale = new Vector2(8f, 8f);
        Material wall = materials.Plaster("WarmPlaster", new Color(0.74f, 0.70f, 0.64f));
        Material trim = materials.Get("WarmWhiteTrim", new Color(0.68f, 0.66f, 0.61f), 0f, 0.25f);
        Material ceiling = materials.Plaster("Ceiling", new Color(0.82f, 0.82f, 0.79f));

        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "Floor", Root, new Vector3(0f, -0.06f, 0f), new Vector3(8f, 0.12f, 8f), floor);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "BackWall", Root, new Vector3(0f, 1.6f, 4f), new Vector3(8f, 3.2f, 0.12f), wall);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "LeftWall", Root, new Vector3(-4f, 1.6f, 0f), new Vector3(0.12f, 3.2f, 8f), wall);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "RightWall", Root, new Vector3(4f, 1.6f, 0f), new Vector3(0.12f, 3.2f, 8f), wall);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "FrontWall", Root, new Vector3(0f, 1.6f, -4f), new Vector3(8f, 3.2f, 0.12f), wall);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "Ceiling", Root, new Vector3(0f, 3.22f, 0f), new Vector3(8f, 0.10f, 8f), ceiling);

        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "BackBaseboard", Root, new Vector3(0f, 0.09f, 3.91f), new Vector3(7.8f, 0.18f, 0.06f), trim);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "LeftBaseboard", Root, new Vector3(-3.91f, 0.09f, 0f), new Vector3(0.06f, 0.18f, 7.8f), trim);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "RightBaseboard", Root, new Vector3(3.91f, 0.09f, 0f), new Vector3(0.06f, 0.18f, 7.8f), trim);

        Material rug = materials.Get("Rug", new Color(0.19f, 0.22f, 0.24f), 0f, 0.08f);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "Rug", Root, new Vector3(0f, 0.016f, 0.55f), new Vector3(3.6f, 0.025f, 2.8f), rug);
    }

    private void BuildTable()
    {
        Transform table = new GameObject("FixedTable").transform;
        table.SetParent(Root, false);
        table.localPosition = new Vector3(0f, 0f, 0.35f);

        Material wood = materials.Wood(
            "TableWalnut",
            new Color(0.075f, 0.035f, 0.018f),
            new Color(0.38f, 0.16f, 0.055f),
            0.48f);
        Material metal = materials.Get("TableMetal", new Color(0.055f, 0.058f, 0.062f), 0.72f, 0.38f);

        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "TableTop", table, new Vector3(0f, 0.89f, 0f), new Vector3(2.25f, 0.10f, 1.02f), wood);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "ApronFront", table, new Vector3(0f, 0.79f, -0.43f), new Vector3(1.94f, 0.18f, 0.075f), wood);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "ApronBack", table, new Vector3(0f, 0.79f, 0.43f), new Vector3(1.94f, 0.18f, 0.075f), wood);

        Vector3 legScale = new Vector3(0.095f, 0.82f, 0.095f);
        float x = 0.94f;
        float z = 0.36f;
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "LegFL", table, new Vector3(-x, 0.41f, -z), legScale, metal);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "LegFR", table, new Vector3(x, 0.41f, -z), legScale, metal);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "LegBL", table, new Vector3(-x, 0.41f, z), legScale, metal);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "LegBR", table, new Vector3(x, 0.41f, z), legScale, metal);

    }

    private void BuildBackgroundDecor()
    {
        Material darkWood = materials.Wood(
            "CabinetWood",
            new Color(0.05f, 0.025f, 0.014f),
            new Color(0.24f, 0.095f, 0.032f),
            0.34f);
        Material black = materials.Get("DecorBlack", new Color(0.025f, 0.028f, 0.032f), 0.55f, 0.5f);
        Material glass = materials.Get("WindowGlass", new Color(0.22f, 0.32f, 0.42f), 0.1f, 0.92f);
        Material frame = materials.Get("FrameBrass", new Color(0.27f, 0.18f, 0.06f), 0.72f, 0.45f);
        Material picture = materials.Get("Picture", new Color(0.18f, 0.25f, 0.29f), 0f, 0.28f);

        Transform cabinet = new GameObject("Sideboard").transform;
        cabinet.SetParent(Root, false);
        cabinet.localPosition = new Vector3(-2.55f, 0f, 2.95f);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "Body", cabinet, new Vector3(0f, 0.55f, 0f), new Vector3(1.55f, 1.08f, 0.45f), darkWood);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "Top", cabinet, new Vector3(0f, 1.12f, 0f), new Vector3(1.68f, 0.08f, 0.54f), darkWood);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "HandleL", cabinet, new Vector3(-0.35f, 0.62f, -0.24f), new Vector3(0.22f, 0.025f, 0.025f), frame);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "HandleR", cabinet, new Vector3(0.35f, 0.62f, -0.24f), new Vector3(0.22f, 0.025f, 0.025f), frame);

        Transform pictureFrame = new GameObject("WallArt").transform;
        pictureFrame.SetParent(Root, false);
        pictureFrame.localPosition = new Vector3(-2.2f, 2.15f, 3.90f);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "Frame", pictureFrame, Vector3.zero, new Vector3(1.3f, 0.9f, 0.07f), frame);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "Art", pictureFrame, new Vector3(0f, 0f, -0.042f), new Vector3(1.12f, 0.72f, 0.02f), picture);

        Transform window = new GameObject("Window").transform;
        window.SetParent(Root, false);
        window.localPosition = new Vector3(3.90f, 1.85f, 1.35f);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "Glass", window, Vector3.zero, new Vector3(0.055f, 1.55f, 1.75f), glass);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "VerticalMullion", window, Vector3.zero, new Vector3(0.075f, 1.68f, 0.055f), black);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "HorizontalMullion", window, Vector3.zero, new Vector3(0.075f, 0.055f, 1.88f), black);

        Material curtain = materials.Get("Curtain", new Color(0.26f, 0.27f, 0.26f), 0f, 0.08f);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "CurtainFront", Root, new Vector3(3.75f, 1.8f, 0.28f), new Vector3(0.18f, 2.35f, 0.48f), curtain);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Cube, "CurtainBack", Root, new Vector3(3.75f, 1.8f, 2.42f), new Vector3(0.18f, 2.35f, 0.48f), curtain);
    }

    private void BuildLighting()
    {
        GameObject sunObject = new GameObject("MainRoomLight");
        sunObject.transform.SetParent(Root, false);
        sunObject.transform.rotation = Quaternion.Euler(46f, -138f, 0f);
        Light sun = sunObject.AddComponent<Light>();
        sun.type = LightType.Directional;
        sun.color = new Color(1f, 0.95f, 0.88f);
        sun.intensity = 1.28f;
        sun.shadows = LightShadows.Soft;
        sun.shadowStrength = 0.58f;
        sun.shadowBias = 0.04f;
        sun.shadowNormalBias = 0.26f;
        sun.cookieSize = 10f;

        GameObject ceilingLamp = new GameObject("CeilingLamp");
        ceilingLamp.transform.SetParent(Root, false);
        ceilingLamp.transform.localPosition = new Vector3(-0.55f, 2.45f, 1.10f);
        Light lamp = ceilingLamp.AddComponent<Light>();
        lamp.type = LightType.Point;
        lamp.color = new Color(1f, 0.86f, 0.70f);
        lamp.intensity = 2.8f;
        lamp.range = 7.5f;
        lamp.shadows = LightShadows.Soft;
        lamp.shadowStrength = 0.5f;
        lamp.shadowBias = 0.05f;
        lamp.shadowNormalBias = 0.35f;

        Material bulb = materials.Emissive("CeilingLampBulb", new Color(1f, 0.78f, 0.52f), 2.2f);
        ScenePrimitives.CreatePrimitive(PrimitiveType.Sphere, "LampBulb", ceilingLamp.transform, Vector3.zero, Vector3.one * 0.12f, bulb);

        GameObject probeObject = new GameObject("RoomReflectionProbe");
        probeObject.transform.SetParent(Root, false);
        probeObject.transform.localPosition = new Vector3(0f, 1.55f, 0.5f);
        ReflectionProbe probe = probeObject.AddComponent<ReflectionProbe>();
        probe.mode = ReflectionProbeMode.Realtime;
        probe.refreshMode = ReflectionProbeRefreshMode.ViaScripting;
        probe.timeSlicingMode = ReflectionProbeTimeSlicingMode.NoTimeSlicing;
        probe.size = new Vector3(7.4f, 3f, 7.4f);
        probe.intensity = 0.92f;
        probe.boxProjection = true;
        probe.resolution = 512;
        probe.RenderProbe();
    }
}
