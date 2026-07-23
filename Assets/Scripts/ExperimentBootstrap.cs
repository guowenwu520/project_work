using System;
using System.Collections.Generic;
using UnityEngine;

public sealed class ExperimentBootstrap : MonoBehaviour
{
    private enum DeskPropCategory
    {
        Generic,
        Mouse,
        Keyboard,
        Laptop,
        Flat,
        Tall
    }

    private struct DeskPropSlot
    {
        public Vector3 localPosition;
        public float yaw;

        public DeskPropSlot(Vector3 pos, float angle)
        {
            localPosition = pos;
            yaw = angle;
        }
    }

    private void Awake()
    {
        DontDestroyOnLoad(gameObject);
        Application.runInBackground = true;

        CommandLineOptions options = CommandLineOptions.Parse(Environment.GetCommandLineArgs());
        ImportedPropLibrary.Configure(options.ModelBundleDirectory);
        BatchJob job = BatchConfiguration.ResolveJob(options.BatchIndex, options.SeedOverride, options.ForcedChangeType);
        UnityEngine.Random.InitState(job.seed);

        MaterialLibrary materialLibrary = new MaterialLibrary(job.seed);
        RoomEnvironment room = new RoomEnvironment(materialLibrary);
        room.Build();

        Transform propRoot = new GameObject("BatchProps").transform;
        propRoot.position = new Vector3(0f, RoomEnvironment.TableTopSurfaceY, 0.35f);

        DeskPropSlot leftSlot;
        DeskPropSlot rightSlot;
        ResolveSlots(job.leftBefore.propClass, job.rightBefore.propClass, out leftSlot, out rightSlot);

        HybridPropFactory propFactory = new HybridPropFactory(materialLibrary, job.seed);
        GameObject leftBefore = CreateProp(propFactory, job.leftBefore, propRoot, leftSlot, "LeftBefore");
        GameObject rightBefore = CreateProp(propFactory, job.rightBefore, propRoot, rightSlot, "RightBefore");

        List<GameObject> originalsToHide = new List<GameObject>();
        List<GameObject> replacementsToShow = new List<GameObject>();
        Transform[] repositionTargets = null;
        Vector3[] afterLocalPositions = null;
        Quaternion[] afterLocalRotations = null;

        if (job.changeType == DatasetChangeTypes.OneObjectReplacement ||
            job.changeType == DatasetChangeTypes.ColorChange)
        {
            if (string.Equals(job.changedSlot, "left", StringComparison.OrdinalIgnoreCase))
            {
                GameObject leftAfter = CreateProp(propFactory, job.leftAfter, propRoot, leftSlot, "LeftAfter");
                leftAfter.SetActive(false);
                originalsToHide.Add(leftBefore);
                replacementsToShow.Add(leftAfter);
            }
            else
            {
                GameObject rightAfter = CreateProp(propFactory, job.rightAfter, propRoot, rightSlot, "RightAfter");
                rightAfter.SetActive(false);
                originalsToHide.Add(rightBefore);
                replacementsToShow.Add(rightAfter);
            }
        }
        else if (job.changeType == DatasetChangeTypes.TwoObjectsReplacement)
        {
            GameObject leftAfter = CreateProp(propFactory, job.leftAfter, propRoot, leftSlot, "LeftAfter");
            GameObject rightAfter = CreateProp(propFactory, job.rightAfter, propRoot, rightSlot, "RightAfter");
            leftAfter.SetActive(false);
            rightAfter.SetActive(false);
            originalsToHide.Add(leftBefore);
            originalsToHide.Add(rightBefore);
            replacementsToShow.Add(leftAfter);
            replacementsToShow.Add(rightAfter);
        }
        else if (job.changeType == DatasetChangeTypes.DistanceIncrease)
        {
            DeskPropSlot leftAfterSlot = leftSlot;
            DeskPropSlot rightAfterSlot = rightSlot;
            leftAfterSlot.localPosition.x -= 0.13f;
            rightAfterSlot.localPosition.x += 0.13f;
            repositionTargets = new[] { leftBefore.transform, rightBefore.transform };
            afterLocalPositions = new[] { leftAfterSlot.localPosition, rightAfterSlot.localPosition };
            afterLocalRotations = new[]
            {
                Quaternion.Euler(0f, leftAfterSlot.yaw, 0f),
                Quaternion.Euler(0f, rightAfterSlot.yaw, 0f)
            };
        }
        else if (job.changeType == DatasetChangeTypes.SwapPositions)
        {
            repositionTargets = new[] { leftBefore.transform, rightBefore.transform };
            afterLocalPositions = new[] { rightSlot.localPosition, leftSlot.localPosition };
            afterLocalRotations = new[]
            {
                Quaternion.Euler(0f, rightSlot.yaw, 0f),
                Quaternion.Euler(0f, leftSlot.yaw, 0f)
            };
        }

        Camera camera = BuildCamera(room.TableCenter);
        FrameSequenceCapture capture = camera.gameObject.AddComponent<FrameSequenceCapture>();
        capture.Configure(options, job);

        ChangeBlindnessSequence sequence = gameObject.AddComponent<ChangeBlindnessSequence>();
        sequence.Initialize(
            camera.transform,
            originalsToHide.ToArray(),
            replacementsToShow.ToArray(),
            job.HasVisualChange,
            options.Loop,
            capture,
            repositionTargets,
            afterLocalPositions,
            afterLocalRotations);

        Debug.Log("Dataset schema version: " + DatasetBuildInfo.SchemaVersion);
        Debug.Log(
            "Dataset batch " + job.id +
            ": before=[" + job.leftBefore.Description + ", " + job.rightBefore.Description + "]" +
            ", after=[" + job.leftAfter.Description + ", " + job.rightAfter.Description + "]" +
            ", change=" + job.changeType +
            ", slot=" + job.changedSlot);
    }

    private static GameObject CreateProp(
        HybridPropFactory factory,
        DatasetObjectState state,
        Transform parent,
        DeskPropSlot slot,
        string prefix)
    {
        GameObject prop = factory.Create(state.propClass, parent, slot.localPosition, slot.yaw);
        prop.name = prefix + "_" + state.propClass;
        if (state.supportsColor && !string.IsNullOrWhiteSpace(state.color))
        {
            PropColorApplicator.Apply(prop, state.color);
        }
        return prop;
    }

    private static void ResolveSlots(string stableName, string originalName, out DeskPropSlot stableSlot, out DeskPropSlot originalSlot)
    {
        DeskPropCategory stableCategory = Categorize(stableName);
        DeskPropCategory originalCategory = Categorize(originalName);

        if ((stableCategory == DeskPropCategory.Keyboard && originalCategory == DeskPropCategory.Mouse) ||
            (stableCategory == DeskPropCategory.Mouse && originalCategory == DeskPropCategory.Keyboard))
        {
            DeskPropSlot keyboard = new DeskPropSlot(new Vector3(0.24f, 0f, 0.08f), -7f);
            DeskPropSlot mouse = new DeskPropSlot(new Vector3(-0.24f, 0f, -0.02f), 18f);
            if (stableCategory == DeskPropCategory.Keyboard)
            {
                stableSlot = keyboard;
                originalSlot = mouse;
            }
            else
            {
                stableSlot = mouse;
                originalSlot = keyboard;
            }
            return;
        }

        if ((stableCategory == DeskPropCategory.Laptop && originalCategory == DeskPropCategory.Mouse) ||
            (stableCategory == DeskPropCategory.Mouse && originalCategory == DeskPropCategory.Laptop))
        {
            DeskPropSlot laptop = new DeskPropSlot(new Vector3(0.00f, 0f, 0.10f), -4f);
            DeskPropSlot mouse = new DeskPropSlot(new Vector3(0.42f, 0f, -0.02f), 15f);
            if (stableCategory == DeskPropCategory.Laptop)
            {
                stableSlot = laptop;
                originalSlot = mouse;
            }
            else
            {
                stableSlot = mouse;
                originalSlot = laptop;
            }
            return;
        }

        stableSlot = DefaultSlotFor(stableCategory, true);
        originalSlot = DefaultSlotFor(originalCategory, false);

        if (stableCategory == DeskPropCategory.Flat || stableCategory == DeskPropCategory.Keyboard)
        {
            stableSlot.localPosition.z += 0.05f;
        }
        if (originalCategory == DeskPropCategory.Flat || originalCategory == DeskPropCategory.Keyboard)
        {
            originalSlot.localPosition.z += 0.05f;
        }
        if (stableCategory == DeskPropCategory.Tall)
        {
            stableSlot.localPosition.z += 0.03f;
        }
        if (originalCategory == DeskPropCategory.Tall)
        {
            originalSlot.localPosition.z += 0.03f;
        }
    }

    private static DeskPropSlot DefaultSlotFor(DeskPropCategory category, bool isLeft)
    {
        if (category == DeskPropCategory.Mouse)
        {
            return isLeft
                ? new DeskPropSlot(new Vector3(-0.26f, 0f, -0.02f), 16f)
                : new DeskPropSlot(new Vector3(0.30f, 0f, -0.01f), -12f);
        }
        if (category == DeskPropCategory.Keyboard)
        {
            return isLeft
                ? new DeskPropSlot(new Vector3(-0.16f, 0f, 0.10f), -6f)
                : new DeskPropSlot(new Vector3(0.20f, 0f, 0.09f), -8f);
        }
        if (category == DeskPropCategory.Laptop)
        {
            return isLeft
                ? new DeskPropSlot(new Vector3(-0.18f, 0f, 0.08f), -5f)
                : new DeskPropSlot(new Vector3(0.18f, 0f, 0.08f), -7f);
        }
        if (category == DeskPropCategory.Flat)
        {
            return isLeft
                ? new DeskPropSlot(new Vector3(-0.28f, 0f, 0.08f), -10f)
                : new DeskPropSlot(new Vector3(0.28f, 0f, 0.07f), 6f);
        }
        if (category == DeskPropCategory.Tall)
        {
            return isLeft
                ? new DeskPropSlot(new Vector3(-0.28f, 0f, 0.03f), -8f)
                : new DeskPropSlot(new Vector3(0.28f, 0f, 0.03f), 10f);
        }
        return isLeft
            ? new DeskPropSlot(new Vector3(-0.28f, 0f, 0.01f), -10f)
            : new DeskPropSlot(new Vector3(0.30f, 0f, 0.04f), 8f);
    }

    private static DeskPropCategory Categorize(string propName)
    {
        string key = (propName ?? string.Empty).ToLowerInvariant();
        if (key.Contains("mouse") || key.Contains("mice")) return DeskPropCategory.Mouse;
        if (key.Contains("keyboard") || key.Contains("keypad")) return DeskPropCategory.Keyboard;
        if (key.Contains("laptop") || key.Contains("notebook")) return DeskPropCategory.Laptop;
        if (key.Contains("tablet") || key.Contains("phone") || key.Contains("book") || key.Contains("pad")) return DeskPropCategory.Flat;
        if (key.Contains("bottle") || key.Contains("mug") || key.Contains("cup") || key.Contains("vase") || key.Contains("plant") || key.Contains("lamp") || key.Contains("column") || key.Contains("tower") || key.Contains("pyramid")) return DeskPropCategory.Tall;
        return DeskPropCategory.Generic;
    }

    private static Camera BuildCamera(Vector3 target)
    {
        GameObject cameraObject = new GameObject("Main Camera");
        cameraObject.tag = "MainCamera";
        cameraObject.transform.position = new Vector3(0f, 2.18f, -3.02f);
        cameraObject.transform.rotation = Quaternion.LookRotation((target + new Vector3(0f, 0.08f, -0.02f) - cameraObject.transform.position).normalized, Vector3.up);

        Camera camera = cameraObject.AddComponent<Camera>();
        camera.clearFlags = CameraClearFlags.Skybox;
        camera.fieldOfView = 37f;
        camera.nearClipPlane = 0.03f;
        camera.farClipPlane = 50f;
        camera.allowHDR = true;
        camera.allowMSAA = true;
        camera.depthTextureMode = DepthTextureMode.Depth;
        camera.usePhysicalProperties = true;
        camera.focalLength = 48f;
        camera.sensorSize = new Vector2(36f, 24f);
        camera.gateFit = Camera.GateFitMode.Horizontal;

        cameraObject.AddComponent<AudioListener>();
        cameraObject.AddComponent<FlareLayer>();
        cameraObject.AddComponent<CinematicPostFX>();
        return camera;
    }
}
