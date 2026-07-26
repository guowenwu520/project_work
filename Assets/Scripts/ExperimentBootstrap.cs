using System;
using System.Collections.Generic;
using UnityEngine;

public sealed class ExperimentBootstrap : MonoBehaviour
{
    // 距离增大时，每个物体期望向外移动 0.32m。
    // 两个物体合计最多增加约 0.64m 的间距。
    private const float DistanceIncreaseDesiredMove = 0.32f;

    // 物体最外侧与桌边至少保留 8cm。
    private const float DistanceIncreaseEdgeMargin = 0.08f;

    // 如果某个模型无法获取 Renderer Bounds，
    // 使用 18cm 作为保守的半宽估计。
    private const float DistanceIncreaseFallbackHalfExtentX = 0.18f;


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

        CommandLineOptions options =
            CommandLineOptions.Parse(
                Environment.GetCommandLineArgs());

        ImportedPropLibrary.Configure(
            options.ModelBundleDirectory);

        BatchJob job =
            BatchConfiguration.ResolveJob(
                options.BatchIndex,
                options.SeedOverride,
                options.ForcedChangeType);

        UnityEngine.Random.InitState(
            job.seed);

        MaterialLibrary materialLibrary =
            new MaterialLibrary(
                job.seed);

        RoomEnvironment room =
            new RoomEnvironment(
                materialLibrary);

        room.Build();

        Transform propRoot =
            new GameObject(
                "BatchProps").transform;

        propRoot.position =
            new Vector3(
                0f,
                RoomEnvironment.TableTopSurfaceY,
                0.35f);


        DeskPropSlot leftSlot;
        DeskPropSlot rightSlot;

        ResolveSlots(
            job.leftBefore.propClass,
            job.rightBefore.propClass,
            out leftSlot,
            out rightSlot);


        HybridPropFactory propFactory =
            new HybridPropFactory(
                materialLibrary,
                job.seed);


        GameObject leftBefore =
            CreateProp(
                propFactory,
                job.leftBefore,
                propRoot,
                leftSlot,
                "LeftBefore");

        GameObject rightBefore =
            CreateProp(
                propFactory,
                job.rightBefore,
                propRoot,
                rightSlot,
                "RightBefore");


        List<GameObject> originalsToHide =
            new List<GameObject>();

        List<GameObject> replacementsToShow =
            new List<GameObject>();

        Transform[] repositionTargets = null;

        Vector3[] afterLocalPositions = null;

        Quaternion[] afterLocalRotations = null;


        if (
            job.changeType ==
                DatasetChangeTypes.OneObjectReplacement ||
            job.changeType ==
                DatasetChangeTypes.ColorChange)
        {
            if (
                string.Equals(
                    job.changedSlot,
                    "left",
                    StringComparison.OrdinalIgnoreCase))
            {
                GameObject leftAfter =
                    CreateProp(
                        propFactory,
                        job.leftAfter,
                        propRoot,
                        leftSlot,
                        "LeftAfter");

                leftAfter.SetActive(false);

                originalsToHide.Add(
                    leftBefore);

                replacementsToShow.Add(
                    leftAfter);
            }
            else
            {
                GameObject rightAfter =
                    CreateProp(
                        propFactory,
                        job.rightAfter,
                        propRoot,
                        rightSlot,
                        "RightAfter");

                rightAfter.SetActive(false);

                originalsToHide.Add(
                    rightBefore);

                replacementsToShow.Add(
                    rightAfter);
            }
        }
        else if (
            job.changeType ==
            DatasetChangeTypes.TwoObjectsReplacement)
        {
            GameObject leftAfter =
                CreateProp(
                    propFactory,
                    job.leftAfter,
                    propRoot,
                    leftSlot,
                    "LeftAfter");

            GameObject rightAfter =
                CreateProp(
                    propFactory,
                    job.rightAfter,
                    propRoot,
                    rightSlot,
                    "RightAfter");

            leftAfter.SetActive(false);
            rightAfter.SetActive(false);

            originalsToHide.Add(
                leftBefore);

            originalsToHide.Add(
                rightBefore);

            replacementsToShow.Add(
                leftAfter);

            replacementsToShow.Add(
                rightAfter);
        }

        // ============================================================
        // 距离增大
        //
        // 原来：
        //
        // left  -= 0.13m
        // right += 0.13m
        //
        // 现在：
        //
        // 1. 每边最多移动 0.32m；
        // 2. 自动获取物体实际 Renderer Bounds；
        // 3. 自动获取桌面的 Bounds；
        // 4. 保证物体最外侧仍位于桌面内部；
        // 5. 与桌边至少保留 8cm；
        // 6. 大模型会自动减少移动距离。
        // ============================================================
        else if (
            job.changeType ==
            DatasetChangeTypes.DistanceIncrease)
        {
            DeskPropSlot leftAfterSlot =
                leftSlot;

            DeskPropSlot rightAfterSlot =
                rightSlot;


            Bounds tableTopBounds;

            if (
                !TryFindTableTopBounds(
                    room.TableCenter,
                    out tableTopBounds))
            {
                // 正常情况下应该自动找到桌面 Renderer。
                //
                // 如果场景里的桌面由特殊 Shader / 非 Renderer
                // 方式生成，则使用一个相对保守的备用范围。
                tableTopBounds =
                    new Bounds(
                        new Vector3(
                            room.TableCenter.x,
                            RoomEnvironment.TableTopSurfaceY -
                            0.04f,
                            room.TableCenter.z),
                        new Vector3(
                            1.60f,
                            0.08f,
                            0.90f));

                Debug.LogWarning(
                    "Could not detect tabletop bounds automatically. " +
                    "Using fallback tabletop bounds: " +
                    tableTopBounds);
            }


            leftAfterSlot.localPosition =
                ResolveDistanceIncreasePosition(
                    leftBefore,
                    propRoot,
                    leftSlot.localPosition,
                    tableTopBounds,
                    true);


            rightAfterSlot.localPosition =
                ResolveDistanceIncreasePosition(
                    rightBefore,
                    propRoot,
                    rightSlot.localPosition,
                    tableTopBounds,
                    false);


            repositionTargets =
                new[]
                {
                    leftBefore.transform,
                    rightBefore.transform
                };


            afterLocalPositions =
                new[]
                {
                    leftAfterSlot.localPosition,
                    rightAfterSlot.localPosition
                };


            afterLocalRotations =
                new[]
                {
                    Quaternion.Euler(
                        0f,
                        leftAfterSlot.yaw,
                        0f),

                    Quaternion.Euler(
                        0f,
                        rightAfterSlot.yaw,
                        0f)
                };


            float beforeDistance =
                Vector3.Distance(
                    leftSlot.localPosition,
                    rightSlot.localPosition);


            float afterDistance =
                Vector3.Distance(
                    leftAfterSlot.localPosition,
                    rightAfterSlot.localPosition);


            Debug.Log(
                "DistanceIncrease: " +
                "left x " +
                leftSlot.localPosition.x.ToString("F3") +
                " -> " +
                leftAfterSlot.localPosition.x.ToString("F3") +
                ", right x " +
                rightSlot.localPosition.x.ToString("F3") +
                " -> " +
                rightAfterSlot.localPosition.x.ToString("F3") +
                ", distance " +
                beforeDistance.ToString("F3") +
                " -> " +
                afterDistance.ToString("F3"));
        }

        else if (
            job.changeType ==
            DatasetChangeTypes.SwapPositions)
        {
            repositionTargets =
                new[]
                {
                    leftBefore.transform,
                    rightBefore.transform
                };

            afterLocalPositions =
                new[]
                {
                    rightSlot.localPosition,
                    leftSlot.localPosition
                };

            afterLocalRotations =
                new[]
                {
                    Quaternion.Euler(
                        0f,
                        rightSlot.yaw,
                        0f),

                    Quaternion.Euler(
                        0f,
                        leftSlot.yaw,
                        0f)
                };
        }


        Camera camera =
            BuildCamera(
                room.TableCenter);


        FrameSequenceCapture capture =
            camera.gameObject
                .AddComponent<FrameSequenceCapture>();

        capture.Configure(
            options,
            job);


        ChangeBlindnessSequence sequence =
            gameObject
                .AddComponent<ChangeBlindnessSequence>();

        sequence.Initialize(
            camera.transform,
            originalsToHide.ToArray(),
            replacementsToShow.ToArray(),
            job.HasVisualChange,
            options.Loop,
            capture,
            repositionTargets,
            afterLocalPositions,
            afterLocalRotations,
            room.TableCenter,
            job.seed);


        Debug.Log(
            "Dataset schema version: " +
            DatasetBuildInfo.SchemaVersion);


        Debug.Log(
            "Dataset batch " +
            job.id +
            ": before=[" +
            job.leftBefore.Description +
            ", " +
            job.rightBefore.Description +
            "]" +
            ", after=[" +
            job.leftAfter.Description +
            ", " +
            job.rightAfter.Description +
            "]" +
            ", change=" +
            job.changeType +
            ", slot=" +
            job.changedSlot);
    }


    private static GameObject CreateProp(
        HybridPropFactory factory,
        DatasetObjectState state,
        Transform parent,
        DeskPropSlot slot,
        string prefix)
    {
        GameObject prop =
            factory.Create(
                state.propClass,
                parent,
                slot.localPosition,
                slot.yaw);

        prop.name =
            prefix +
            "_" +
            state.propClass;

        if (
            state.supportsColor &&
            !string.IsNullOrWhiteSpace(
                state.color))
        {
            PropColorApplicator.Apply(
                prop,
                state.color);
        }

        return prop;
    }


    private static void ResolveSlots(
        string stableName,
        string originalName,
        out DeskPropSlot stableSlot,
        out DeskPropSlot originalSlot)
    {
        DeskPropCategory stableCategory =
            Categorize(
                stableName);

        DeskPropCategory originalCategory =
            Categorize(
                originalName);


        if (
            (stableCategory ==
                 DeskPropCategory.Keyboard &&
             originalCategory ==
                 DeskPropCategory.Mouse) ||
            (stableCategory ==
                 DeskPropCategory.Mouse &&
             originalCategory ==
                 DeskPropCategory.Keyboard))
        {
            DeskPropSlot keyboard =
                new DeskPropSlot(
                    new Vector3(
                        0.24f,
                        0f,
                        0.08f),
                    -7f);

            DeskPropSlot mouse =
                new DeskPropSlot(
                    new Vector3(
                        -0.24f,
                        0f,
                        -0.02f),
                    18f);


            if (
                stableCategory ==
                DeskPropCategory.Keyboard)
            {
                stableSlot =
                    keyboard;

                originalSlot =
                    mouse;
            }
            else
            {
                stableSlot =
                    mouse;

                originalSlot =
                    keyboard;
            }

            return;
        }


        if (
            (stableCategory ==
                 DeskPropCategory.Laptop &&
             originalCategory ==
                 DeskPropCategory.Mouse) ||
            (stableCategory ==
                 DeskPropCategory.Mouse &&
             originalCategory ==
                 DeskPropCategory.Laptop))
        {
            DeskPropSlot laptop =
                new DeskPropSlot(
                    new Vector3(
                        0.00f,
                        0f,
                        0.10f),
                    -4f);

            DeskPropSlot mouse =
                new DeskPropSlot(
                    new Vector3(
                        0.42f,
                        0f,
                        -0.02f),
                    15f);


            if (
                stableCategory ==
                DeskPropCategory.Laptop)
            {
                stableSlot =
                    laptop;

                originalSlot =
                    mouse;
            }
            else
            {
                stableSlot =
                    mouse;

                originalSlot =
                    laptop;
            }

            return;
        }


        stableSlot =
            DefaultSlotFor(
                stableCategory,
                true);

        originalSlot =
            DefaultSlotFor(
                originalCategory,
                false);


        if (
            stableCategory ==
                DeskPropCategory.Flat ||
            stableCategory ==
                DeskPropCategory.Keyboard)
        {
            stableSlot.localPosition.z +=
                0.05f;
        }


        if (
            originalCategory ==
                DeskPropCategory.Flat ||
            originalCategory ==
                DeskPropCategory.Keyboard)
        {
            originalSlot.localPosition.z +=
                0.05f;
        }


        if (
            stableCategory ==
            DeskPropCategory.Tall)
        {
            stableSlot.localPosition.z +=
                0.03f;
        }


        if (
            originalCategory ==
            DeskPropCategory.Tall)
        {
            originalSlot.localPosition.z +=
                0.03f;
        }
    }


    private static DeskPropSlot DefaultSlotFor(
        DeskPropCategory category,
        bool isLeft)
    {
        if (
            category ==
            DeskPropCategory.Mouse)
        {
            return isLeft
                ? new DeskPropSlot(
                    new Vector3(
                        -0.26f,
                        0f,
                        -0.02f),
                    16f)
                : new DeskPropSlot(
                    new Vector3(
                        0.30f,
                        0f,
                        -0.01f),
                    -12f);
        }


        if (
            category ==
            DeskPropCategory.Keyboard)
        {
            return isLeft
                ? new DeskPropSlot(
                    new Vector3(
                        -0.16f,
                        0f,
                        0.10f),
                    -6f)
                : new DeskPropSlot(
                    new Vector3(
                        0.20f,
                        0f,
                        0.09f),
                    -8f);
        }


        if (
            category ==
            DeskPropCategory.Laptop)
        {
            return isLeft
                ? new DeskPropSlot(
                    new Vector3(
                        -0.18f,
                        0f,
                        0.08f),
                    -5f)
                : new DeskPropSlot(
                    new Vector3(
                        0.18f,
                        0f,
                        0.08f),
                    -7f);
        }


        if (
            category ==
            DeskPropCategory.Flat)
        {
            return isLeft
                ? new DeskPropSlot(
                    new Vector3(
                        -0.28f,
                        0f,
                        0.08f),
                    -10f)
                : new DeskPropSlot(
                    new Vector3(
                        0.28f,
                        0f,
                        0.07f),
                    6f);
        }


        if (
            category ==
            DeskPropCategory.Tall)
        {
            return isLeft
                ? new DeskPropSlot(
                    new Vector3(
                        -0.28f,
                        0f,
                        0.03f),
                    -8f)
                : new DeskPropSlot(
                    new Vector3(
                        0.28f,
                        0f,
                        0.03f),
                    10f);
        }


        return isLeft
            ? new DeskPropSlot(
                new Vector3(
                    -0.28f,
                    0f,
                    0.01f),
                -10f)
            : new DeskPropSlot(
                new Vector3(
                    0.30f,
                    0f,
                    0.04f),
                8f);
    }


    // ================================================================
    // 距离增大位置计算
    // ================================================================

    private static Vector3 ResolveDistanceIncreasePosition(
        GameObject prop,
        Transform propRoot,
        Vector3 initialLocalPosition,
        Bounds tableTopBounds,
        bool moveLeft)
    {
        Bounds propBounds;

        bool hasPropBounds =
            TryGetRendererBounds(
                prop,
                out propBounds);


        float currentWorldX =
            propRoot
                .TransformPoint(
                    initialLocalPosition)
                .x;


        float objectMinX;
        float objectMaxX;


        if (hasPropBounds)
        {
            objectMinX =
                propBounds.min.x;

            objectMaxX =
                propBounds.max.x;
        }
        else
        {
            objectMinX =
                currentWorldX -
                DistanceIncreaseFallbackHalfExtentX;

            objectMaxX =
                currentWorldX +
                DistanceIncreaseFallbackHalfExtentX;

            Debug.LogWarning(
                "Could not get Renderer bounds for " +
                prop.name +
                ". Using fallback object width.");
        }


        float desiredDelta =
            moveLeft
                ? -DistanceIncreaseDesiredMove
                : DistanceIncreaseDesiredMove;


        float actualDelta;


        if (moveLeft)
        {
            // 如果物体向左移动：
            //
            // objectMinX + delta
            //
            // 必须 >=
            //
            // table.min.x + margin

            float minimumAllowedDelta =
                tableTopBounds.min.x +
                DistanceIncreaseEdgeMargin -
                objectMinX;


            actualDelta =
                Mathf.Max(
                    desiredDelta,
                    minimumAllowedDelta);


            // 边界计算异常时，也绝不能让左侧物体向右移动。
            actualDelta =
                Mathf.Min(
                    0f,
                    actualDelta);
        }
        else
        {
            // 如果物体向右移动：
            //
            // objectMaxX + delta
            //
            // 必须 <=
            //
            // table.max.x - margin

            float maximumAllowedDelta =
                tableTopBounds.max.x -
                DistanceIncreaseEdgeMargin -
                objectMaxX;


            actualDelta =
                Mathf.Min(
                    desiredDelta,
                    maximumAllowedDelta);


            // 边界计算异常时，也绝不能让右侧物体向左移动。
            actualDelta =
                Mathf.Max(
                    0f,
                    actualDelta);
        }


        Vector3 initialWorldPosition =
            propRoot.TransformPoint(
                initialLocalPosition);


        Vector3 finalWorldPosition =
            initialWorldPosition +
            new Vector3(
                actualDelta,
                0f,
                0f);


        Vector3 finalLocalPosition =
            propRoot.InverseTransformPoint(
                finalWorldPosition);


        Debug.Log(
            "DistanceIncrease object=" +
            prop.name +
            ", direction=" +
            (moveLeft ? "left" : "right") +
            ", requested=" +
            desiredDelta.ToString("F3") +
            ", actual=" +
            actualDelta.ToString("F3") +
            ", objectBoundsX=[" +
            objectMinX.ToString("F3") +
            ", " +
            objectMaxX.ToString("F3") +
            "], tableBoundsX=[" +
            tableTopBounds.min.x.ToString("F3") +
            ", " +
            tableTopBounds.max.x.ToString("F3") +
            "]");


        return finalLocalPosition;
    }


    // ================================================================
    // 获取物体的真实 Renderer Bounds
    // ================================================================

    private static bool TryGetRendererBounds(
        GameObject root,
        out Bounds result)
    {
        Renderer[] renderers =
            root.GetComponentsInChildren<Renderer>(
                true);


        bool found =
            false;

        result =
            new Bounds();


        for (
            int i = 0;
            i < renderers.Length;
            i++)
        {
            Renderer renderer =
                renderers[i];


            if (renderer == null)
            {
                continue;
            }


            if (!found)
            {
                result =
                    renderer.bounds;

                found =
                    true;
            }
            else
            {
                result.Encapsulate(
                    renderer.bounds);
            }
        }


        return found;
    }


    // ================================================================
    // 自动寻找桌面实际 Bounds
    //
    // 策略：
    //
    // 1. 顶部高度接近 TableTopSurfaceY；
    // 2. X/Z 尺寸足够大，避免识别成桌上物体；
    // 3. 中心靠近 room.TableCenter；
    // 4. 多个候选时选择水平面积最大的。
    // ================================================================

    private static bool TryFindTableTopBounds(
        Vector3 tableCenter,
        out Bounds result)
    {
        Renderer[] renderers =
            UnityEngine.Object
                .FindObjectsOfType<Renderer>();


        bool found =
            false;

        float bestArea =
            -1f;

        result =
            new Bounds();


        for (
            int i = 0;
            i < renderers.Length;
            i++)
        {
            Renderer renderer =
                renderers[i];


            if (
                renderer == null ||
                !renderer.gameObject.activeInHierarchy)
            {
                continue;
            }


            Bounds bounds =
                renderer.bounds;


            // 桌面 Renderer 的最高点应该接近桌面表面高度。
            float topDifference =
                Mathf.Abs(
                    bounds.max.y -
                    RoomEnvironment.TableTopSurfaceY);


            if (
                topDifference >
                0.12f)
            {
                continue;
            }


            // 排除杯子、鼠标、书等小物体，
            // 以及细小桌腿。
            if (
                bounds.size.x <
                    0.90f ||
                bounds.size.z <
                    0.45f)
            {
                continue;
            }


            float centerXDifference =
                Mathf.Abs(
                    bounds.center.x -
                    tableCenter.x);


            float centerZDifference =
                Mathf.Abs(
                    bounds.center.z -
                    tableCenter.z);


            if (
                centerXDifference >
                    0.40f ||
                centerZDifference >
                    0.40f)
            {
                continue;
            }


            float area =
                bounds.size.x *
                bounds.size.z;


            if (
                !found ||
                area > bestArea)
            {
                result =
                    bounds;

                bestArea =
                    area;

                found =
                    true;
            }
        }


        if (found)
        {
            Debug.Log(
                "Detected tabletop bounds: " +
                "center=" +
                result.center.ToString("F3") +
                ", size=" +
                result.size.ToString("F3"));
        }


        return found;
    }


    private static DeskPropCategory Categorize(
        string propName)
    {
        string key =
            (propName ?? string.Empty)
                .ToLowerInvariant();


        if (
            key.Contains("mouse") ||
            key.Contains("mice"))
        {
            return DeskPropCategory.Mouse;
        }


        if (
            key.Contains("keyboard") ||
            key.Contains("keypad"))
        {
            return DeskPropCategory.Keyboard;
        }


        if (
            key.Contains("laptop") ||
            key.Contains("notebook"))
        {
            return DeskPropCategory.Laptop;
        }


        if (
            key.Contains("tablet") ||
            key.Contains("phone") ||
            key.Contains("book") ||
            key.Contains("pad"))
        {
            return DeskPropCategory.Flat;
        }


        if (
            key.Contains("bottle") ||
            key.Contains("mug") ||
            key.Contains("cup") ||
            key.Contains("vase") ||
            key.Contains("plant") ||
            key.Contains("lamp") ||
            key.Contains("column") ||
            key.Contains("tower") ||
            key.Contains("pyramid"))
        {
            return DeskPropCategory.Tall;
        }


        return DeskPropCategory.Generic;
    }


    private static Camera BuildCamera(
        Vector3 target)
    {
        GameObject cameraObject =
            new GameObject(
                "Main Camera");

        cameraObject.tag =
            "MainCamera";


        cameraObject.transform.position =
            new Vector3(
                0f,
                2.18f,
                -3.02f);


        cameraObject.transform.rotation =
            Quaternion.LookRotation(
                (
                    target +
                    new Vector3(
                        0f,
                        0.08f,
                        -0.02f) -
                    cameraObject.transform.position
                ).normalized,
                Vector3.up);


        Camera camera =
            cameraObject
                .AddComponent<Camera>();


        camera.clearFlags =
            CameraClearFlags.Skybox;

        camera.fieldOfView =
            37f;

        camera.nearClipPlane =
            0.03f;

        camera.farClipPlane =
            50f;

        camera.allowHDR =
            true;

        camera.allowMSAA =
            true;

        camera.depthTextureMode =
            DepthTextureMode.Depth;

        camera.usePhysicalProperties =
            true;

        camera.focalLength =
            48f;

        camera.sensorSize =
            new Vector2(
                36f,
                24f);

        camera.gateFit =
            Camera.GateFitMode.Horizontal;


        cameraObject
            .AddComponent<AudioListener>();

        cameraObject
            .AddComponent<FlareLayer>();

        cameraObject
            .AddComponent<CinematicPostFX>();


        return camera;
    }
}