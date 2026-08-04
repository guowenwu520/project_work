using UnityEngine;

public sealed class ChangeBlindnessSequence : MonoBehaviour
{
    // Keep the original 31-second capture duration.
    public const float InitialHoldDuration = 8f;
    public const float TurnAwayDuration = 3f;
    public const float WalkAroundDuration = 9f;
    public const float TurnBackDuration = 3f;
    public const float FinalHoldDuration = 8f;

    public const float TotalDuration =
        InitialHoldDuration +
        TurnAwayDuration +
        WalkAroundDuration +
        TurnBackDuration +
        FinalHoldDuration;

    // During walking, the gaze primarily follows the path tangent.
    // A small inward component makes the first turn gentler while keeping
    // the tabletop outside the normal camera field of view.
    private const float InwardLookWeight = 0.22f;

    // The first visible head turn is explicitly limited to 30 degrees.
    // Further rotation happens gradually while the person follows the arc.
    private const float InitialTurnDegrees = 30f;

    // Fraction of the walking stage used to align the body from the initial
    // 30-degree heading to the curved-path walking direction.
    private const float WalkingHeadingBlendFraction = 0.32f;

    // Pull only the middle of the route toward the table. Start and end
    // positions remain unchanged.
    private const float RouteInsetRatio = 0.18f;
    private const float MinimumRouteInset = 0.40f;
    private const float MaximumRouteInset = 0.78f;

    private Transform cameraTransform;

    private Vector3 focusPoint;
    private Vector3 eyeLevelCenter;

    private Vector3 startPosition;
    private Quaternion startRotation;

    private Vector3 startRadial;
    private Vector3 endPosition;
    private Quaternion initialTurnRotation;
    private Quaternion startWalkRotation;
    private Quaternion endWalkRotation;
    private Quaternion endLookRotation;

    private float walkRadius;
    private float routeInset;
    private float arcDirection;
    private float cameraNoiseSeed;

    private GameObject[] originalsToHide;
    private GameObject[] replacementsToShow;

    private Transform[] repositionTargets;
    private Vector3[] beforeLocalPositions;
    private Quaternion[] beforeLocalRotations;
    private Vector3[] afterLocalPositions;
    private Quaternion[] afterLocalRotations;

    private FrameSequenceCapture capture;

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

        ConfigureOppositeSidePath(movementSeed);

        cameraNoiseSeed =
            Mathf.Abs(movementSeed % 100000) * 0.0137f +
            Mathf.Abs(startPosition.GetHashCode() % 1000) * 0.01f;

        SetObjectsActive(
            replacementsToShow,
            false);

        Debug.Log(
            "Forward-walking arc camera configured: start=" +
            startPosition.ToString("F3") +
            ", opposite=" +
            endPosition.ToString("F3") +
            ", direction=" +
            (arcDirection > 0f ? "left arc" : "right arc") +
            ", middle inset=" +
            routeInset.ToString("F2") +
            "m. Initial head turn is limited to " +
            InitialTurnDegrees.ToString("F0") +
            " degrees; body heading then aligns gradually while walking.");
    }

    private void ConfigureOppositeSidePath(int movementSeed)
    {
        eyeLevelCenter = new Vector3(
            focusPoint.x,
            startPosition.y,
            focusPoint.z);

        Vector3 radial =
            startPosition - eyeLevelCenter;

        radial.y = 0f;

        if (radial.sqrMagnitude < 0.25f)
        {
            radial =
                -Vector3.ProjectOnPlane(
                    startRotation * Vector3.forward,
                    Vector3.up);

            if (radial.sqrMagnitude < 0.0001f)
            {
                radial = Vector3.back;
            }

            radial.Normalize();
            radial *= 3f;
        }

        walkRadius = radial.magnitude;
        startRadial = radial.normalized;

        routeInset = Mathf.Clamp(
            walkRadius * RouteInsetRatio,
            MinimumRouteInset,
            MaximumRouteInset);

        routeInset = Mathf.Min(
            routeInset,
            Mathf.Max(0f, walkRadius - 1.35f));

        // Stable left/right route for the same scene seed.
        arcDirection =
            (movementSeed & 1) == 0
                ? 1f
                : -1f;

        Vector3 endRadial = -startRadial;

        endPosition =
            eyeLevelCenter +
            endRadial * walkRadius;

        endPosition.y = startPosition.y;

        startWalkRotation =
            BuildWalkingRotation(startRadial);

        Vector3 startHorizontalForward =
            Vector3.ProjectOnPlane(
                startRotation * Vector3.forward,
                Vector3.up);

        if (startHorizontalForward.sqrMagnitude < 0.0001f)
        {
            startHorizontalForward = -startRadial;
        }

        startHorizontalForward.Normalize();

        Vector3 initialTravelTangent =
            GetTravelTangent(startRadial);

        float fullRouteTurn =
            Vector3.SignedAngle(
                startHorizontalForward,
                initialTravelTangent,
                Vector3.up);

        float limitedInitialTurn =
            Mathf.Clamp(
                fullRouteTurn,
                -InitialTurnDegrees,
                InitialTurnDegrees);

        initialTurnRotation =
            Quaternion.AngleAxis(
                limitedInitialTurn,
                Vector3.up) *
            startRotation;

        endWalkRotation =
            BuildWalkingRotation(endRadial);

        endLookRotation =
            LookAtFocusFrom(endPosition);
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
                TotalDuration);

        ApplyTimeline(timelineTime);

        float changeTime =
            InitialHoldDuration +
            TurnAwayDuration +
            WalkAroundDuration * 0.5f;

        if (shouldApplyChange &&
            !changed &&
            timelineTime >= changeTime)
        {
            ApplyChange();
        }

        if (elapsed >= TotalDuration)
        {
            if (loop)
            {
                ResetLoop();
            }
            else
            {
                completed = true;

                cameraTransform.position =
                    endPosition;

                cameraTransform.rotation =
                    endLookRotation;

                capture?.MarkSequenceComplete();
            }
        }
    }

    private void ApplyTimeline(float t)
    {
        float turnAwayEnd =
            InitialHoldDuration +
            TurnAwayDuration;

        float walkEnd =
            turnAwayEnd +
            WalkAroundDuration;

        float turnBackEnd =
            walkEnd +
            TurnBackDuration;

        Vector3 basePosition;
        Quaternion baseRotation;

        bool walking = false;
        float walkProgress = 0f;

        if (t < InitialHoldDuration)
        {
            basePosition = startPosition;
            baseRotation = startRotation;
        }
        else if (t < turnAwayEnd)
        {
            float normalized =
                (t - InitialHoldDuration) /
                TurnAwayDuration;

            float eased =
                SmootherStep(normalized);

            Vector3 firstTravelTangent =
                GetTravelTangent(startRadial);

            basePosition =
                startPosition +
                firstTravelTangent *
                Mathf.Sin(eased * Mathf.PI) *
                0.025f;

            // Only make a gentle 30-degree turn before walking.
            baseRotation =
                Quaternion.SlerpUnclamped(
                    startRotation,
                    initialTurnRotation,
                    eased);
        }
        else if (t < walkEnd)
        {
            walking = true;

            walkProgress =
                (t - turnAwayEnd) /
                WalkAroundDuration;

            float travelProgress =
                SmootherStep(walkProgress);

            float arcAngle =
                arcDirection *
                180f *
                travelProgress;

            Vector3 radial =
                Quaternion.AngleAxis(
                    arcAngle,
                    Vector3.up) *
                startRadial;

            Vector3 tangent =
                GetTravelTangent(radial);

            // Keep the exact start and opposite-side end positions, but
            // pull the middle of the route inward so it stays away from the
            // side wall. Squared sine gives zero lateral slope at both ends.
            float insetEnvelope =
                Mathf.Sin(
                    travelProgress *
                    Mathf.PI);

            insetEnvelope *=
                insetEnvelope;

            float currentRadius =
                walkRadius -
                routeInset *
                insetEnvelope;

            basePosition =
                eyeLevelCenter +
                radial * currentRadius;

            // Important:
            // The camera follows the walking direction, so optical flow
            // always looks like forward motion. There is no independent
            // head pan; this rotation is only the body's natural turn
            // while following the curved path.
            Quaternion curvedPathRotation =
                BuildWalkingRotation(radial);

            float headingBlend =
                SmootherStep(
                    Mathf.Clamp01(
                        walkProgress /
                        WalkingHeadingBlendFraction));

            baseRotation =
                Quaternion.SlerpUnclamped(
                    initialTurnRotation,
                    curvedPathRotation,
                    headingBlend);

            AddWalkingBodyMotion(
                walkProgress,
                tangent,
                ref basePosition);
        }
        else if (t < turnBackEnd)
        {
            float normalized =
                (t - walkEnd) /
                TurnBackDuration;

            float eased =
                SmootherStep(normalized);

            Vector3 finalTravelTangent =
                GetTravelTangent(-startRadial);

            basePosition =
                endPosition +
                finalTravelTangent *
                Mathf.Sin(eased * Mathf.PI) *
                0.025f;

            // Only after reaching the opposite side does the viewer
            // turn from the walking direction back toward the tabletop.
            baseRotation =
                Quaternion.SlerpUnclamped(
                    endWalkRotation,
                    endLookRotation,
                    eased);
        }
        else
        {
            basePosition = endPosition;
            baseRotation = endLookRotation;
        }

        ApplyNaturalEyeMotion(
            t,
            walking,
            ref basePosition,
            ref baseRotation);

        cameraTransform.position =
            basePosition;

        cameraTransform.rotation =
            baseRotation;
    }

    private Quaternion BuildWalkingRotation(
        Vector3 radial)
    {
        Vector3 outward =
            Vector3.ProjectOnPlane(
                radial,
                Vector3.up);

        if (outward.sqrMagnitude < 0.0001f)
        {
            outward = Vector3.forward;
        }

        outward.Normalize();

        Vector3 tangent =
            GetTravelTangent(outward);

        // Mostly look in the direction of travel. A small component
        // toward the table reduces the first turn. The tabletop center
        // remains about 78 degrees off-axis and stays outside the view.
        Vector3 towardTable =
            -outward;

        Vector3 horizontalForward =
            tangent +
            towardTable * InwardLookWeight;

        horizontalForward.Normalize();

        float originalVertical =
            (startRotation *
             Vector3.forward).y;

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

    private Vector3 GetTravelTangent(
        Vector3 radial)
    {
        Vector3 tangent =
            Vector3.Cross(
                Vector3.up,
                radial.normalized) *
            arcDirection;

        if (tangent.sqrMagnitude < 0.0001f)
        {
            tangent = Vector3.right;
        }

        return tangent.normalized;
    }

    private void AddWalkingBodyMotion(
        float walkProgress,
        Vector3 travelTangent,
        ref Vector3 position)
    {
        float gaitEnvelope =
            Mathf.Sin(
                walkProgress *
                Mathf.PI);

        float stepPhase =
            walkProgress *
            WalkAroundDuration *
            1.65f *
            Mathf.PI *
            2f;

        float verticalBob =
            Mathf.Abs(
                Mathf.Sin(stepPhase)) *
            0.016f *
            gaitEnvelope;

        Vector3 radialSide =
            Vector3.Cross(
                travelTangent,
                Vector3.up);

        if (radialSide.sqrMagnitude < 0.0001f)
        {
            radialSide = Vector3.right;
        }

        radialSide.Normalize();

        float lateralSway =
            Mathf.Sin(
                stepPhase * 0.5f) *
            0.012f *
            gaitEnvelope;

        position +=
            Vector3.up * verticalBob +
            radialSide * lateralSway;
    }

    private Quaternion LookAtFocusFrom(
        Vector3 position)
    {
        Vector3 finalTarget =
            focusPoint +
            new Vector3(
                0f,
                0.08f,
                -0.02f);

        Vector3 direction =
            finalTarget -
            position;

        if (direction.sqrMagnitude < 0.0001f)
        {
            direction =
                Vector3.forward;
        }

        return Quaternion.LookRotation(
            direction.normalized,
            Vector3.up);
    }

    private void ApplyNaturalEyeMotion(
        float t,
        bool walking,
        ref Vector3 position,
        ref Quaternion rotation)
    {
        float positionAmount =
            walking
                ? 0.08f
                : 0.18f;

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

        if (walking)
        {
            // No independent head yaw/roll during walking.
            return;
        }

        float yaw =
            (Mathf.PerlinNoise(
                cameraNoiseSeed + 23f,
                t * 0.10f) - 0.5f) *
            0.10f;

        float roll =
            (Mathf.PerlinNoise(
                cameraNoiseSeed + 37f,
                t * 0.11f) - 0.5f) *
            0.10f;

        rotation *=
            Quaternion.Euler(
                0f,
                yaw,
                roll);
    }

    private static Vector3 EstimateFocusPoint(
        Vector3 cameraPosition,
        Quaternion cameraRotation)
    {
        Vector3 horizontalForward =
            Vector3.ProjectOnPlane(
                cameraRotation *
                Vector3.forward,
                Vector3.up);

        if (horizontalForward.sqrMagnitude < 0.0001f)
        {
            horizontalForward =
                Vector3.forward;
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

        cameraTransform.position =
            startPosition;

        cameraTransform.rotation =
            startRotation;
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

    private static float SmootherStep(
        float value)
    {
        value =
            Mathf.Clamp01(value);

        return
            value *
            value *
            value *
            (value *
             (value * 6f - 15f) +
             10f);
    }
}
