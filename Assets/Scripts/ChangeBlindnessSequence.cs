using System;
using UnityEngine;

[Serializable]
public sealed class ChangeBlindnessTimingData
{
    public float initialHold;
    public float moveAway;
    public float hiddenChange;
    public float returnToTable;
    public float finalHold;
    public float swapAt;
    public float totalDuration;
}

public sealed class ChangeBlindnessTiming
{
    public const float MinimumObservationDuration = 2f;
    public const float MaximumObservationDuration = 10f;
    public const float MinimumMovementDuration = 2f;
    public const float MaximumMovementDuration = 7f;

    public float InitialHoldDuration { get; private set; }
    public float MoveAwayDuration { get; private set; }
    public float HiddenChangeDuration { get; private set; }
    public float ReturnDuration { get; private set; }
    public float FinalHoldDuration { get; private set; }

    public float TotalDuration
    {
        get
        {
            return
                InitialHoldDuration +
                MoveAwayDuration +
                HiddenChangeDuration +
                ReturnDuration +
                FinalHoldDuration;
        }
    }

    public float ChangeTime
    {
        get
        {
            return
                InitialHoldDuration +
                MoveAwayDuration +
                HiddenChangeDuration * 0.5f;
        }
    }

    private ChangeBlindnessTiming(
        float initialHoldDuration,
        float moveAwayDuration,
        float hiddenChangeDuration,
        float returnDuration,
        float finalHoldDuration)
    {
        InitialHoldDuration = initialHoldDuration;
        MoveAwayDuration = moveAwayDuration;
        HiddenChangeDuration = hiddenChangeDuration;
        ReturnDuration = returnDuration;
        FinalHoldDuration = finalHoldDuration;
    }

    public static ChangeBlindnessTiming Create(
        int seed,
        string profile)
    {
        string normalized =
            (profile ?? string.Empty)
                .Trim()
                .ToLowerInvariant();

        if (normalized == "fastest")
        {
            return new ChangeBlindnessTiming(
                MinimumObservationDuration,
                MinimumMovementDuration,
                MinimumMovementDuration,
                MinimumMovementDuration,
                MinimumObservationDuration);
        }

        if (normalized == "slowest")
        {
            return new ChangeBlindnessTiming(
                MaximumObservationDuration,
                MaximumMovementDuration,
                MaximumMovementDuration,
                MaximumMovementDuration,
                MaximumObservationDuration);
        }

        var random = new System.Random(
            unchecked(seed * 1103515245 + 12345));

        return new ChangeBlindnessTiming(
            SampleDuration(
                random,
                MinimumObservationDuration,
                MaximumObservationDuration),
            SampleDuration(
                random,
                MinimumMovementDuration,
                MaximumMovementDuration),
            SampleDuration(
                random,
                MinimumMovementDuration,
                MaximumMovementDuration),
            SampleDuration(
                random,
                MinimumMovementDuration,
                MaximumMovementDuration),
            SampleDuration(
                random,
                MinimumObservationDuration,
                MaximumObservationDuration));
    }

    public ChangeBlindnessTimingData ToData()
    {
        return new ChangeBlindnessTimingData
        {
            initialHold = InitialHoldDuration,
            moveAway = MoveAwayDuration,
            hiddenChange = HiddenChangeDuration,
            returnToTable = ReturnDuration,
            finalHold = FinalHoldDuration,
            swapAt = ChangeTime,
            totalDuration = TotalDuration
        };
    }

    private static float SampleDuration(
        System.Random random,
        float minimum,
        float maximum)
    {
        int minimumMilliseconds =
            Mathf.RoundToInt(minimum * 1000f);
        int maximumMilliseconds =
            Mathf.RoundToInt(maximum * 1000f);

        return random.Next(
                   minimumMilliseconds,
                   maximumMilliseconds + 1) /
               1000f;
    }
}

[Serializable]
public sealed class ChangeBlindnessCameraRouteData
{
    public string routeId;
    public string profile;
    public int finalViewAngleDegrees;
    public float signedViewAngleDegrees;
    public int routeVariant;
    public string direction;
    public float pathLengthMeters;
    public Vector3 startPosition;
    public Vector3 controlPoint1;
    public Vector3 controlPoint2;
    public Vector3 endPosition;
}

public sealed class ChangeBlindnessCameraRoute
{
    private static readonly int[] SupportedAngles =
    {
        45,
        90,
        135,
        180
    };

    private readonly Vector3 point0;
    private readonly Vector3 point3;
    private readonly Vector3 routeCenter;
    private readonly Vector3 startOutward;
    private readonly float startRadius;
    private readonly float endInset;
    private readonly float detourInset;
    private readonly int detourWaveCount;

    public string RouteId { get; private set; }
    public string Profile { get; private set; }
    public int FinalViewAngleDegrees { get; private set; }
    public float SignedViewAngleDegrees { get; private set; }
    public int RouteVariant { get; private set; }
    public float DirectionSign { get; private set; }
    public float PathLengthMeters { get; private set; }
    public Vector3 StartPosition { get { return point0; } }
    public Vector3 EndPosition { get { return point3; } }

    private ChangeBlindnessCameraRoute(
        string profile,
        int finalViewAngleDegrees,
        int routeVariant,
        float directionSign,
        Vector3 start,
        Vector3 end,
        Vector3 center,
        Vector3 initialOutward,
        float initialRadius,
        float finalInset,
        float routeDetourInset,
        int routeDetourWaveCount)
    {
        Profile = profile;
        FinalViewAngleDegrees = finalViewAngleDegrees;
        RouteVariant = routeVariant;
        DirectionSign = directionSign;
        SignedViewAngleDegrees =
            finalViewAngleDegrees *
            directionSign;

        point0 = start;
        point3 = end;
        routeCenter = center;
        startOutward = initialOutward;
        startRadius = initialRadius;
        endInset = finalInset;
        detourInset = routeDetourInset;
        detourWaveCount = routeDetourWaveCount;

        RouteId =
            "angle_" +
            finalViewAngleDegrees.ToString("D3") +
            "_route_" +
            routeVariant;

        PathLengthMeters = EstimateLength(160);
    }

    public static ChangeBlindnessCameraRoute Create(
        Vector3 startPosition,
        Quaternion startRotation,
        Vector3 focusPoint,
        int seed,
        int forcedAngleDegrees,
        int forcedRouteVariant,
        string requestedProfile)
    {
        string profile =
            NormalizeProfile(requestedProfile);

        int angle;
        int variant;

        if (profile == "shortest")
        {
            angle = 45;
            variant = 1;
        }
        else if (profile == "longest")
        {
            angle = 180;
            variant = 2;
        }
        else
        {
            var random = new System.Random(
                unchecked(seed * 1664525 + 1013904223));

            angle = IsSupportedAngle(forcedAngleDegrees)
                ? forcedAngleDegrees
                : SupportedAngles[random.Next(SupportedAngles.Length)];

            variant =
                forcedRouteVariant == 1 ||
                forcedRouteVariant == 2
                    ? forcedRouteVariant
                    : random.Next(1, 3);
        }

        float directionSign =
            (unchecked(seed * 31 + angle * 7 + variant) & 1) == 0
                ? 1f
                : -1f;

        Vector3 eyeLevelCenter = new Vector3(
            focusPoint.x,
            startPosition.y,
            focusPoint.z);

        Vector3 startRadial =
            startPosition -
            eyeLevelCenter;

        startRadial.y = 0f;

        if (startRadial.sqrMagnitude < 0.25f)
        {
            startRadial =
                -Vector3.ProjectOnPlane(
                    startRotation * Vector3.forward,
                    Vector3.up);

            if (startRadial.sqrMagnitude < 0.0001f)
            {
                startRadial = Vector3.back;
            }

            startRadial.Normalize();
            startRadial *= 3f;
        }

        float radius = startRadial.magnitude;
        Vector3 startOutward = startRadial.normalized;
        Vector3 endOutward =
            Quaternion.AngleAxis(
                angle * directionSign,
                Vector3.up) *
            startOutward;

        // Pull every destination slightly toward the table. This leaves a
        // stable clearance from the room walls without changing the chosen
        // 45/90/135/180-degree final viewing angle.
        float finalInset =
            Mathf.Clamp(
                radius * 0.12f,
                0.30f,
                0.50f);

        Vector3 endPosition =
            eyeLevelCenter +
            endOutward * (radius - finalInset);

        endPosition.y = startPosition.y;

        float routeDetourInset;
        int routeDetourWaveCount;

        if (variant == 1)
        {
            // Short route: one shallow inward bend.
            routeDetourInset =
                Mathf.Clamp(
                    radius * 0.05f,
                    0.12f,
                    0.25f);

            routeDetourWaveCount = 1;
        }
        else
        {
            // Long route: one wide, shallow inward sweep. Staying near the
            // original safe orbit keeps it longer than route 1, while the
            // single bend avoids repeated left/right heading oscillations.
            routeDetourInset =
                Mathf.Clamp(
                    radius * 0.02f,
                    0.05f,
                    0.10f);

            routeDetourWaveCount = 1;
        }

        return new ChangeBlindnessCameraRoute(
            profile,
            angle,
            variant,
            directionSign,
            startPosition,
            endPosition,
            eyeLevelCenter,
            startOutward,
            radius,
            finalInset,
            routeDetourInset,
            routeDetourWaveCount);
    }

    public Vector3 EvaluatePosition(float progress)
    {
        float t = Mathf.Clamp01(progress);

        // The route is planned in polar coordinates around the tabletop.
        // Both radial offsets are non-negative, so no sampled point can be
        // farther from the table than the original camera position.
        float settledProgress =
            t * t * t *
            (t * (t * 6f - 15f) + 10f);

        float detourPhase =
            t *
            detourWaveCount *
            Mathf.PI;

        float detourEnvelope =
            Mathf.Sin(detourPhase);

        detourEnvelope *= detourEnvelope;

        float radius =
            startRadius -
            endInset * settledProgress -
            detourInset * detourEnvelope;

        Vector3 outward =
            Quaternion.AngleAxis(
                SignedViewAngleDegrees * t,
                Vector3.up) *
            startOutward;

        Vector3 position =
            routeCenter +
            outward * radius;

        position.y = point0.y;
        return position;
    }

    public Vector3 EvaluateTangent(float progress)
    {
        float t = Mathf.Clamp01(progress);
        const float tangentSampleDistance = 0.001f;

        float before =
            Mathf.Max(
                0f,
                t - tangentSampleDistance);

        float after =
            Mathf.Min(
                1f,
                t + tangentSampleDistance);

        Vector3 derivative =
            EvaluatePosition(after) -
            EvaluatePosition(before);

        derivative.y = 0f;

        if (derivative.sqrMagnitude < 0.0001f)
        {
            derivative = point3 - point0;
            derivative.y = 0f;
        }

        if (derivative.sqrMagnitude < 0.0001f)
        {
            derivative = Vector3.forward;
        }

        return derivative.normalized;
    }

    public ChangeBlindnessCameraRouteData ToData()
    {
        return new ChangeBlindnessCameraRouteData
        {
            routeId = RouteId,
            profile = Profile,
            finalViewAngleDegrees = FinalViewAngleDegrees,
            signedViewAngleDegrees = SignedViewAngleDegrees,
            routeVariant = RouteVariant,
            direction = DirectionSign > 0f ? "counterclockwise" : "clockwise",
            pathLengthMeters = PathLengthMeters,
            startPosition = point0,
            // Preserve the existing annotation schema. These fields now
            // contain safe route guide points rather than Bezier handles.
            controlPoint1 = EvaluatePosition(1f / 3f),
            controlPoint2 = EvaluatePosition(2f / 3f),
            endPosition = point3
        };
    }

    private float EstimateLength(int samples)
    {
        float length = 0f;
        Vector3 previous = point0;

        for (int i = 1; i <= samples; i++)
        {
            Vector3 current =
                EvaluatePosition(
                    i / (float)samples);

            length +=
                Vector3.Distance(
                    previous,
                    current);

            previous = current;
        }

        return length;
    }

    private static bool IsSupportedAngle(int angle)
    {
        for (int i = 0; i < SupportedAngles.Length; i++)
        {
            if (SupportedAngles[i] == angle)
            {
                return true;
            }
        }

        return false;
    }

    private static string NormalizeProfile(string value)
    {
        string normalized =
            (value ?? string.Empty)
                .Trim()
                .ToLowerInvariant();

        if (normalized == "shortest" ||
            normalized == "longest")
        {
            return normalized;
        }

        return "random";
    }
}

public sealed class ChangeBlindnessSequence : MonoBehaviour
{
    private const float InwardLookWeight = 0.22f;
    private const float InitialTurnMaximumDegrees = 30f;
    private const float WalkingHeadingBlendFraction = 0.32f;
    private const float ReturnTurnLeadStart = 0.82f;
    private const float ReturnTurnLeadShare = 0.15f;

    private Transform cameraTransform;
    private Vector3 focusPoint;
    private Vector3 startPosition;
    private Quaternion startRotation;
    private Quaternion routeStartRotation;
    private Quaternion initialTurnRotation;
    private Quaternion routeArrivalRotation;
    private Quaternion endLookRotation;
    private Vector3 tableTurnStartHorizontal;
    private float tableTurnSignedYawDegrees;
    private float tableTurnStartPitchDegrees;
    private float tableTurnEndPitchDegrees;
    private float cameraNoiseSeed;

    private GameObject[] originalsToHide;
    private GameObject[] replacementsToShow;
    private Transform[] repositionTargets;
    private Vector3[] beforeLocalPositions;
    private Quaternion[] beforeLocalRotations;
    private Vector3[] afterLocalPositions;
    private Quaternion[] afterLocalRotations;

    private FrameSequenceCapture capture;
    private ChangeBlindnessTiming timing;
    private ChangeBlindnessCameraRoute route;

    private bool shouldApplyChange;
    private bool loop;
    private bool changed;
    private bool completed;
    private float elapsed;

    public void Initialize(
        Transform targetCamera,
        GameObject[] originals,
        GameObject[] replacements,
        bool enableChange,
        bool shouldLoop,
        FrameSequenceCapture captureController,
        ChangeBlindnessTiming timingConfig,
        ChangeBlindnessCameraRoute routeConfig,
        Transform[] transformsToReposition = null,
        Vector3[] targetLocalPositions = null,
        Quaternion[] targetLocalRotations = null,
        Vector3? sceneFocusPoint = null,
        int movementSeed = 0)
    {
        cameraTransform = targetCamera;
        originalsToHide = originals;
        replacementsToShow = replacements;
        shouldApplyChange = enableChange;
        loop = shouldLoop;
        capture = captureController;

        timing =
            timingConfig ??
            ChangeBlindnessTiming.Create(
                movementSeed,
                "random");

        repositionTargets = transformsToReposition;
        afterLocalPositions = targetLocalPositions;
        afterLocalRotations = targetLocalRotations;

        CacheBeforeTransforms();

        startPosition = cameraTransform.position;
        startRotation = cameraTransform.rotation;
        focusPoint =
            sceneFocusPoint ??
            EstimateFocusPoint(
                startPosition,
                startRotation);

        route =
            routeConfig ??
            ChangeBlindnessCameraRoute.Create(
                startPosition,
                startRotation,
                focusPoint,
                movementSeed,
                0,
                0,
                "random");

        // The route derivative defines the walking direction. The arrival
        // direction then anchors one continuous signed turn toward the table.
        routeStartRotation =
            BuildRouteRotation(
                route.EvaluatePosition(0f),
                route.EvaluateTangent(0f));

        float initialTurnAngle =
            Quaternion.Angle(
                startRotation,
                routeStartRotation);

        float initialTurnProgress =
            initialTurnAngle <= 0.001f
                ? 1f
                : Mathf.Min(
                    1f,
                    InitialTurnMaximumDegrees /
                    initialTurnAngle);

        // Only make a gentle turn while standing still. The rest of the
        // heading change is completed continuously during early walking.
        initialTurnRotation =
            Quaternion.SlerpUnclamped(
                startRotation,
                routeStartRotation,
                initialTurnProgress);

        routeArrivalRotation =
            BuildRouteRotation(
                route.EvaluatePosition(1f),
                route.EvaluateTangent(1f));

        endLookRotation =
            LookAtFocusFrom(
                route.EndPosition);

        ConfigureTableTurn(
            routeArrivalRotation,
            endLookRotation);

        // The final part of the walking route already begins the same turn
        // that continues during returnToTable. Both phases therefore share
        // one planned rotation curve and one explicit turn direction.
        cameraNoiseSeed =
            Mathf.Abs(movementSeed % 100000) * 0.0137f +
            Mathf.Abs(startPosition.GetHashCode() % 1000) * 0.01f;

        SetObjectsActive(
            replacementsToShow,
            false);

        Debug.Log(
            "Smooth planned camera route configured: " +
            route.RouteId +
            ", signed angle=" +
            route.SignedViewAngleDegrees.ToString("F0") +
            " degrees, path=" +
            route.PathLengthMeters.ToString("F3") +
            "m, total=" +
            timing.TotalDuration.ToString("F3") +
            "s. Phase boundaries share the same position and rotation.");
    }

    private void Update()
    {
        if (cameraTransform == null || completed)
        {
            return;
        }

        elapsed += Time.deltaTime;

        float timelineTime =
            Mathf.Min(
                elapsed,
                timing.TotalDuration);

        ApplyTimeline(timelineTime);

        if (shouldApplyChange &&
            !changed &&
            timelineTime >= timing.ChangeTime)
        {
            ApplyChange();
        }

        if (elapsed >= timing.TotalDuration)
        {
            if (loop)
            {
                ResetLoop();
            }
            else
            {
                completed = true;

                // Do not assign endLookRotation again here. ApplyTimeline
                // already produced the final pose, including continuous
                // natural eye motion. Reassigning used to cause a one-frame
                // completion snap.
                capture?.MarkSequenceComplete();
            }
        }
    }

    private void ApplyTimeline(float t)
    {
        float moveAwayEnd =
            timing.InitialHoldDuration +
            timing.MoveAwayDuration;

        float routeEnd =
            moveAwayEnd +
            timing.HiddenChangeDuration;

        float returnEnd =
            routeEnd +
            timing.ReturnDuration;

        Vector3 basePosition;
        Quaternion baseRotation;
        float walkingWeight = 0f;
        float routeProgress = 0f;

        if (t < timing.InitialHoldDuration)
        {
            basePosition = startPosition;
            baseRotation = startRotation;
        }
        else if (t < moveAwayEnd)
        {
            float normalized =
                SafeProgress(
                    t - timing.InitialHoldDuration,
                    timing.MoveAwayDuration);

            float eased =
                SmootherStep(normalized);

            basePosition = startPosition;
            baseRotation =
                Quaternion.SlerpUnclamped(
                    startRotation,
                    initialTurnRotation,
                    eased);
        }
        else if (t < routeEnd)
        {
            routeProgress =
                SafeProgress(
                    t - moveAwayEnd,
                    timing.HiddenChangeDuration);

            float travelProgress =
                SmootherStep(routeProgress);

            basePosition =
                route.EvaluatePosition(
                    travelProgress);

            Quaternion walkingRotation =
                BuildRouteRotation(
                    basePosition,
                    route.EvaluateTangent(
                        travelProgress));

            float walkingHeadingBlend =
                SafeProgress(
                    routeProgress,
                    WalkingHeadingBlendFraction);

            walkingHeadingBlend =
                SmootherStep(
                    walkingHeadingBlend);

            // The first walking frame starts at exactly the final stationary
            // turn rotation. During the first 32% of the route it converges
            // smoothly to the actual route tangent instead of snapping to it.
            Quaternion routeRotation =
                Quaternion.SlerpUnclamped(
                    initialTurnRotation,
                    walkingRotation,
                    walkingHeadingBlend);

            float turnLead =
                SafeProgress(
                    routeProgress -
                    ReturnTurnLeadStart,
                    1f -
                    ReturnTurnLeadStart);

            turnLead =
                SmootherStep(turnLead);

            if (turnLead > 0f)
            {
                Quaternion plannedTurnRotation =
                    EvaluateTableTurnRotation(
                        ReturnTurnLeadShare *
                        turnLead);

                baseRotation =
                    Quaternion.SlerpUnclamped(
                        routeRotation,
                        plannedTurnRotation,
                        turnLead);
            }
            else
            {
                baseRotation = routeRotation;
            }

            walkingWeight =
                Mathf.Sin(
                    routeProgress *
                    Mathf.PI);

            walkingWeight *= walkingWeight;

            AddWalkingBodyMotion(
                routeProgress,
                route.EvaluateTangent(
                    travelProgress),
                ref basePosition);
        }
        else if (t < returnEnd)
        {
            float normalized =
                SafeProgress(
                    t - routeEnd,
                    timing.ReturnDuration);

            float eased =
                SmootherStep(normalized);

            basePosition = route.EndPosition;

            float tableTurnProgress =
                Mathf.Lerp(
                    ReturnTurnLeadShare,
                    1f,
                    eased);

            // Continue the exact same signed yaw/pitch curve that began near
            // the end of the walking route. No new transition time is used.
            baseRotation =
                EvaluateTableTurnRotation(
                    tableTurnProgress);
        }
        else
        {
            basePosition = route.EndPosition;
            baseRotation = endLookRotation;
        }

        ApplyNaturalEyeMotion(
            t,
            walkingWeight,
            ref basePosition,
            ref baseRotation);

        cameraTransform.position = basePosition;
        cameraTransform.rotation = baseRotation;
    }

    private Quaternion BuildRouteRotation(
        Vector3 position,
        Vector3 routeTangent)
    {
        Vector3 tangent =
            Vector3.ProjectOnPlane(
                routeTangent,
                Vector3.up);

        if (tangent.sqrMagnitude < 0.0001f)
        {
            tangent =
                Vector3.ProjectOnPlane(
                    startRotation * Vector3.forward,
                    Vector3.up);
        }

        tangent.Normalize();

        Vector3 towardTable =
            Vector3.ProjectOnPlane(
                focusPoint - position,
                Vector3.up);

        if (towardTable.sqrMagnitude > 0.0001f)
        {
            towardTable.Normalize();
        }

        Vector3 horizontalForward =
            tangent +
            towardTable * InwardLookWeight;

        if (horizontalForward.sqrMagnitude < 0.0001f)
        {
            horizontalForward = tangent;
        }

        horizontalForward.Normalize();

        float originalVertical =
            (startRotation * Vector3.forward).y;

        float vertical =
            Mathf.Clamp(
                originalVertical * 0.30f,
                -0.08f,
                0.03f);

        Vector3 forward =
            horizontalForward +
            Vector3.up * vertical;

        return Quaternion.LookRotation(
            forward.normalized,
            Vector3.up);
    }

    private void AddWalkingBodyMotion(
        float routeProgress,
        Vector3 travelTangent,
        ref Vector3 position)
    {
        float gaitEnvelope =
            Mathf.Sin(
                routeProgress *
                Mathf.PI);

        gaitEnvelope *= gaitEnvelope;

        float stepPhase =
            routeProgress *
            timing.HiddenChangeDuration *
            1.65f *
            Mathf.PI *
            2f;

        float verticalBob =
            Mathf.Abs(
                Mathf.Sin(stepPhase)) *
            0.016f *
            gaitEnvelope;

        Vector3 side =
            Vector3.Cross(
                travelTangent,
                Vector3.up);

        if (side.sqrMagnitude < 0.0001f)
        {
            side = Vector3.right;
        }

        side.Normalize();

        float lateralSway =
            Mathf.Sin(
                stepPhase * 0.5f) *
            0.012f *
            gaitEnvelope;

        position +=
            Vector3.up * verticalBob +
            side * lateralSway;
    }

    private Quaternion LookAtFocusFrom(
        Vector3 position)
    {
        Vector3 target =
            focusPoint +
            new Vector3(
                0f,
                0.08f,
                -0.02f);

        Vector3 direction =
            target - position;

        if (direction.sqrMagnitude < 0.0001f)
        {
            direction = Vector3.forward;
        }

        return Quaternion.LookRotation(
            direction.normalized,
            Vector3.up);
    }

    private void ConfigureTableTurn(
        Quaternion start,
        Quaternion end)
    {
        Vector3 startForward =
            (start * Vector3.forward).normalized;

        Vector3 endForward =
            (end * Vector3.forward).normalized;

        tableTurnStartHorizontal =
            Vector3.ProjectOnPlane(
                startForward,
                Vector3.up);

        Vector3 endHorizontal =
            Vector3.ProjectOnPlane(
                endForward,
                Vector3.up);

        if (tableTurnStartHorizontal.sqrMagnitude < 0.0001f)
        {
            tableTurnStartHorizontal = Vector3.forward;
        }

        if (endHorizontal.sqrMagnitude < 0.0001f)
        {
            endHorizontal = tableTurnStartHorizontal;
        }

        tableTurnStartHorizontal.Normalize();
        endHorizontal.Normalize();

        tableTurnSignedYawDegrees =
            Vector3.SignedAngle(
                tableTurnStartHorizontal,
                endHorizontal,
                Vector3.up);

        // Continue turning in the same direction as the route. This avoids
        // Quaternion.Slerp choosing the opposite side near an ambiguous yaw.
        if (Mathf.Abs(tableTurnSignedYawDegrees) > 0.001f &&
            Mathf.Sign(tableTurnSignedYawDegrees) !=
            Mathf.Sign(route.DirectionSign))
        {
            tableTurnSignedYawDegrees +=
                360f *
                Mathf.Sign(route.DirectionSign);
        }

        tableTurnStartPitchDegrees =
            Mathf.Asin(
                Mathf.Clamp(
                    startForward.y,
                    -1f,
                    1f)) *
            Mathf.Rad2Deg;

        tableTurnEndPitchDegrees =
            Mathf.Asin(
                Mathf.Clamp(
                    endForward.y,
                    -1f,
                    1f)) *
            Mathf.Rad2Deg;
    }

    private Quaternion EvaluateTableTurnRotation(
        float progress)
    {
        float t = Mathf.Clamp01(progress);

        Vector3 horizontalForward =
            Quaternion.AngleAxis(
                tableTurnSignedYawDegrees * t,
                Vector3.up) *
            tableTurnStartHorizontal;

        float pitchDegrees =
            Mathf.Lerp(
                tableTurnStartPitchDegrees,
                tableTurnEndPitchDegrees,
                t);

        float pitchRadians =
            pitchDegrees *
            Mathf.Deg2Rad;

        Vector3 forward =
            horizontalForward.normalized *
            Mathf.Cos(pitchRadians) +
            Vector3.up *
            Mathf.Sin(pitchRadians);

        if (forward.sqrMagnitude < 0.0001f)
        {
            return routeArrivalRotation;
        }

        return Quaternion.LookRotation(
            forward.normalized,
            Vector3.up);
    }

    private void ApplyNaturalEyeMotion(
        float t,
        float walkingWeight,
        ref Vector3 position,
        ref Quaternion rotation)
    {
        walkingWeight =
            Mathf.Clamp01(walkingWeight);

        float positionAmount =
            Mathf.Lerp(
                0.18f,
                0.08f,
                walkingWeight);

        float nx =
            (Mathf.PerlinNoise(
                cameraNoiseSeed,
                t * 0.15f) - 0.5f) *
            0.004f *
            positionAmount;

        float ny =
            (Mathf.PerlinNoise(
                cameraNoiseSeed + 11f,
                t * 0.13f) - 0.5f) *
            0.003f *
            positionAmount;

        position +=
            rotation *
            new Vector3(
                nx,
                ny,
                0f);

        float headMotionAmount =
            1f - walkingWeight;

        float yaw =
            (Mathf.PerlinNoise(
                cameraNoiseSeed + 23f,
                t * 0.10f) - 0.5f) *
            0.10f *
            headMotionAmount;

        float roll =
            (Mathf.PerlinNoise(
                cameraNoiseSeed + 37f,
                t * 0.11f) - 0.5f) *
            0.10f *
            headMotionAmount;

        rotation *=
            Quaternion.Euler(
                0f,
                yaw,
                roll);
    }

    private static float SafeProgress(
        float elapsedTime,
        float duration)
    {
        if (duration <= 0.0001f)
        {
            return 1f;
        }

        return Mathf.Clamp01(
            elapsedTime / duration);
    }

    private static Vector3 EstimateFocusPoint(
        Vector3 cameraPosition,
        Quaternion cameraRotation)
    {
        Vector3 horizontalForward =
            Vector3.ProjectOnPlane(
                cameraRotation * Vector3.forward,
                Vector3.up);

        if (horizontalForward.sqrMagnitude < 0.0001f)
        {
            horizontalForward = Vector3.forward;
        }

        horizontalForward.Normalize();

        Vector3 estimated =
            cameraPosition +
            horizontalForward * 3f;

        estimated.y =
            cameraPosition.y - 1.2f;

        return estimated;
    }

    private void ApplyChange()
    {
        changed = true;

        SetObjectsActive(
            originalsToHide,
            false);

        SetObjectsActive(
            replacementsToShow,
            true);

        ApplyRepositionTargets(
            afterLocalPositions,
            afterLocalRotations);
    }

    private void ResetLoop()
    {
        elapsed = 0f;
        changed = false;
        completed = false;

        SetObjectsActive(
            originalsToHide,
            true);

        SetObjectsActive(
            replacementsToShow,
            false);

        ApplyRepositionTargets(
            beforeLocalPositions,
            beforeLocalRotations);

        cameraTransform.position = startPosition;
        cameraTransform.rotation = startRotation;
    }

    private void CacheBeforeTransforms()
    {
        if (repositionTargets == null ||
            repositionTargets.Length == 0)
        {
            beforeLocalPositions = null;
            beforeLocalRotations = null;
            return;
        }

        beforeLocalPositions =
            new Vector3[
                repositionTargets.Length];

        beforeLocalRotations =
            new Quaternion[
                repositionTargets.Length];

        for (int i = 0;
             i < repositionTargets.Length;
             i++)
        {
            if (repositionTargets[i] == null)
            {
                continue;
            }

            beforeLocalPositions[i] =
                repositionTargets[i]
                    .localPosition;

            beforeLocalRotations[i] =
                repositionTargets[i]
                    .localRotation;
        }
    }

    private void ApplyRepositionTargets(
        Vector3[] positions,
        Quaternion[] rotations)
    {
        if (repositionTargets == null ||
            positions == null)
        {
            return;
        }

        for (int i = 0;
             i < repositionTargets.Length &&
             i < positions.Length;
             i++)
        {
            if (repositionTargets[i] == null)
            {
                continue;
            }

            repositionTargets[i]
                .localPosition =
                positions[i];

            if (rotations != null &&
                i < rotations.Length)
            {
                repositionTargets[i]
                    .localRotation =
                    rotations[i];
            }
        }
    }

    private static void SetObjectsActive(
        GameObject[] objects,
        bool active)
    {
        if (objects == null)
        {
            return;
        }

        for (int i = 0;
             i < objects.Length;
             i++)
        {
            if (objects[i] != null)
            {
                objects[i]
                    .SetActive(active);
            }
        }
    }

    private static float SmootherStep(float value)
    {
        value = Mathf.Clamp01(value);

        return
            value *
            value *
            value *
            (value *
             (value * 6f - 15f) +
             10f);
    }
}
