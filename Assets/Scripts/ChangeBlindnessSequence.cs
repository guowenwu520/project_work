using UnityEngine;

public sealed class ChangeBlindnessSequence : MonoBehaviour
{
    public const float InitialHoldDuration = 8f;
    public const float MoveAwayDuration = 5f;
    public const float AwayHoldDuration = 5f;
    public const float ReturnDuration = 5f;
    public const float FinalHoldDuration = 8f;
    public const float TotalDuration = InitialHoldDuration + MoveAwayDuration + AwayHoldDuration + ReturnDuration + FinalHoldDuration;

    private Transform cameraTransform;
    private Vector3 startPosition;
    private Quaternion startRotation;
    private Vector3 awayPosition;
    private Quaternion awayRotation;
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
    private float cameraNoiseSeed;

    public void Initialize(
        Transform targetCamera,
        GameObject[] originals,
        GameObject[] replacements,
        bool enableChange,
        bool shouldLoop,
        FrameSequenceCapture captureController,
        Transform[] transformsToReposition = null,
        Vector3[] targetLocalPositions = null,
        Quaternion[] targetLocalRotations = null)
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

        Vector3 horizontalRight = Vector3.ProjectOnPlane(startRotation * Vector3.right, Vector3.up).normalized;
        if (horizontalRight.sqrMagnitude < 0.0001f)
        {
            horizontalRight = Vector3.right;
        }

        const float horizontalMoveDistance = 1.0f;
        awayPosition = startPosition + horizontalRight * horizontalMoveDistance;
        awayPosition.y = startPosition.y;

        Vector3 startEuler = startRotation.eulerAngles;
        awayRotation = Quaternion.Euler(startEuler.x, startEuler.y + 60f, startEuler.z);
        cameraNoiseSeed = Mathf.Abs(startPosition.GetHashCode() % 1000) * 0.01f;

        SetObjectsActive(replacementsToShow, false);
    }

    private void Update()
    {
        if (cameraTransform == null || completed)
        {
            return;
        }

        elapsed += Time.deltaTime;
        float timelineTime = Mathf.Min(elapsed, TotalDuration);
        ApplyTimeline(timelineTime);

        float changeTime = InitialHoldDuration + MoveAwayDuration + AwayHoldDuration * 0.5f;
        if (shouldApplyChange && !changed && timelineTime >= changeTime)
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
                cameraTransform.position = startPosition;
                cameraTransform.rotation = startRotation;
                capture?.MarkSequenceComplete();
            }
        }
    }

    private void ApplyTimeline(float t)
    {
        Vector3 basePosition;
        Quaternion baseRotation;

        float phaseA = InitialHoldDuration;
        float phaseB = phaseA + MoveAwayDuration;
        float phaseC = phaseB + AwayHoldDuration;
        float phaseD = phaseC + ReturnDuration;

        if (t < phaseA)
        {
            basePosition = startPosition;
            baseRotation = startRotation;
        }
        else if (t < phaseB)
        {
            float normalized = (t - phaseA) / MoveAwayDuration;
            float eased = SmootherStep(normalized);
            basePosition = Vector3.LerpUnclamped(startPosition, awayPosition, eased);
            baseRotation = Quaternion.SlerpUnclamped(startRotation, awayRotation, eased);
        }
        else if (t < phaseC)
        {
            basePosition = awayPosition;
            baseRotation = awayRotation;
        }
        else if (t < phaseD)
        {
            float normalized = (t - phaseC) / ReturnDuration;
            float eased = SmootherStep(normalized);
            basePosition = Vector3.LerpUnclamped(awayPosition, startPosition, eased);
            baseRotation = Quaternion.SlerpUnclamped(awayRotation, startRotation, eased);
        }
        else
        {
            basePosition = startPosition;
            baseRotation = startRotation;
        }

        float noiseStrength = (t < InitialHoldDuration || t > TotalDuration - FinalHoldDuration) ? 0.16f : 0.85f;
        float nx = (Mathf.PerlinNoise(cameraNoiseSeed, Time.time * 0.15f) - 0.5f) * 0.006f * noiseStrength;
        float roll = (Mathf.PerlinNoise(cameraNoiseSeed + 29f, Time.time * 0.11f) - 0.5f) * 0.12f * noiseStrength;

        cameraTransform.position = basePosition + baseRotation * new Vector3(nx, 0f, 0f);
        cameraTransform.position = new Vector3(cameraTransform.position.x, startPosition.y, cameraTransform.position.z);
        cameraTransform.rotation = baseRotation * Quaternion.Euler(0f, 0f, roll);
    }

    private void ApplyChange()
    {
        changed = true;
        SetObjectsActive(originalsToHide, false);
        SetObjectsActive(replacementsToShow, true);
        ApplyRepositionTargets(afterLocalPositions, afterLocalRotations);
    }

    private void ResetLoop()
    {
        elapsed = 0f;
        changed = false;
        completed = false;
        SetObjectsActive(originalsToHide, true);
        SetObjectsActive(replacementsToShow, false);
        ApplyRepositionTargets(beforeLocalPositions, beforeLocalRotations);
        cameraTransform.position = startPosition;
        cameraTransform.rotation = startRotation;
    }

    private void CacheBeforeTransforms()
    {
        if (repositionTargets == null || repositionTargets.Length == 0)
        {
            beforeLocalPositions = null;
            beforeLocalRotations = null;
            return;
        }

        beforeLocalPositions = new Vector3[repositionTargets.Length];
        beforeLocalRotations = new Quaternion[repositionTargets.Length];
        for (int i = 0; i < repositionTargets.Length; i++)
        {
            if (repositionTargets[i] == null)
            {
                continue;
            }
            beforeLocalPositions[i] = repositionTargets[i].localPosition;
            beforeLocalRotations[i] = repositionTargets[i].localRotation;
        }
    }

    private void ApplyRepositionTargets(Vector3[] positions, Quaternion[] rotations)
    {
        if (repositionTargets == null || positions == null)
        {
            return;
        }

        for (int i = 0; i < repositionTargets.Length && i < positions.Length; i++)
        {
            if (repositionTargets[i] == null)
            {
                continue;
            }

            repositionTargets[i].localPosition = positions[i];
            if (rotations != null && i < rotations.Length)
            {
                repositionTargets[i].localRotation = rotations[i];
            }
        }
    }

    private static void SetObjectsActive(GameObject[] objects, bool active)
    {
        if (objects == null)
        {
            return;
        }

        for (int i = 0; i < objects.Length; i++)
        {
            if (objects[i] != null)
            {
                objects[i].SetActive(active);
            }
        }
    }

    private static float SmootherStep(float value)
    {
        value = Mathf.Clamp01(value);
        return value * value * value * (value * (value * 6f - 15f) + 10f);
    }
}
