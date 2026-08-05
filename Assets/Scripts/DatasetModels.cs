using System;
using System.Collections.Generic;
using UnityEngine;

public static class DatasetChangeTypes
{
    public const string OneObjectReplacement = "one_object_replacement";
    public const string ColorChange = "same_object_color_change";
    public const string DistanceIncrease = "distance_increase";
    public const string DistanceDecrease = "distance_decrease";
    public const string SwapPositions = "swap_positions";
    public const string NoChange = "no_change";
    public const string ObjectAdding = "object_adding";
    public const string ObjectDeleting = "object_deleting";
}

[Serializable]
public sealed class DatasetColorDefinition
{
    public string name;
    public string hex;

    public Color ToUnityColor()
    {
        if (!string.IsNullOrWhiteSpace(hex) && ColorUtility.TryParseHtmlString(hex, out Color parsed))
        {
            parsed.a = 1f;
            return parsed;
        }
        return Color.white;
    }
}

[Serializable]
public sealed class DatasetChangeProbabilities
{
    public float oneObjectReplacement = 0.25f;
    public float colorChange = 0.17f;
    public float distanceIncrease = 0.075f;
    public float distanceDecrease = 0.075f;
    public float swapPositions = 0.12f;
    public float noChange = 0.13f;
    public float objectAdding = 0.09f;
    public float objectDeleting = 0.09f;
}

[Serializable]
public sealed class DatasetGenerationConfig
{
    public int baseSeed = 20260714;
    public string videoPathPrefix = "data";
    public DatasetChangeProbabilities changeProbabilities = new DatasetChangeProbabilities();
    public List<DatasetColorDefinition> colors = new List<DatasetColorDefinition>();
}

[Serializable]
public sealed class DatasetObjectState
{
    public string slot;
    public string propClass;
    public string label;
    public string color;
    public bool supportsColor;
    public bool present = true;

    public string Description
    {
        get
        {
            if (!present)
            {
                return "no object";
            }
            if (supportsColor && !string.IsNullOrWhiteSpace(color))
            {
                return (color + " " + label).Trim();
            }
            return label;
        }
    }

    public DatasetObjectState Clone()
    {
        return new DatasetObjectState
        {
            slot = slot,
            propClass = propClass,
            label = label,
            color = color,
            supportsColor = supportsColor,
            present = present
        };
    }
}

[Serializable]
public sealed class BatchJob
{
    public int id;
    public int seed = 1;
    public string changeType;
    public string changedSlot;
    public DatasetObjectState leftBefore;
    public DatasetObjectState rightBefore;
    public DatasetObjectState leftAfter;
    public DatasetObjectState rightAfter;

    // Legacy fields retained for build/log compatibility.
    public string stableProp;
    public string originalProp;
    public string replacementProp;

    public bool HasVisualChange
    {
        get { return !string.Equals(changeType, DatasetChangeTypes.NoChange, StringComparison.OrdinalIgnoreCase); }
    }

    public DatasetObjectState GetBefore(string slot)
    {
        return string.Equals(slot, "left", StringComparison.OrdinalIgnoreCase) ? leftBefore : rightBefore;
    }

    public DatasetObjectState GetAfter(string slot)
    {
        return string.Equals(slot, "left", StringComparison.OrdinalIgnoreCase) ? leftAfter : rightAfter;
    }

    public DatasetObjectState GetUnchangedBefore()
    {
        return string.Equals(changedSlot, "left", StringComparison.OrdinalIgnoreCase) ? rightBefore : leftBefore;
    }

    public int InitialObjectCount
    {
        get { return CountPresent(leftBefore, rightBefore); }
    }

    public int FinalObjectCount
    {
        get { return CountPresent(leftAfter, rightAfter); }
    }

    private static int CountPresent(
        DatasetObjectState first,
        DatasetObjectState second)
    {
        int count = 0;
        if (first != null && first.present)
        {
            count++;
        }
        if (second != null && second.present)
        {
            count++;
        }
        return count;
    }
}

[Serializable]
public sealed class BatchJobCollection
{
    public List<BatchJob> jobs = new List<BatchJob>();
}

[Serializable]
public sealed class DatasetConversationTurn
{
    public string from;
    public string value;

    public DatasetConversationTurn(string speaker, string text)
    {
        from = speaker;
        value = text;
    }
}

[Serializable]
public sealed class DatasetVideoConversation
{
    public List<string> video = new List<string>();
    public List<DatasetConversationTurn> conversations = new List<DatasetConversationTurn>();
}

[Serializable]
public sealed class DatasetQaPair
{
    public string question;
    public string answer;
    public string question_type;

    public DatasetQaPair(
        string q,
        string a,
        string questionType)
    {
        question = q;
        answer = a;
        question_type = questionType;
    }
}

[Serializable]
public sealed class DatasetAnnotation
{
    public string schemaVersion;
    public int batchId;
    public int seed;
    public string videoPath;
    public string changeType;
    public string changedSlot;
    public int initialObjectCount;
    public int finalObjectCount;
    public DatasetObjectState leftBefore;
    public DatasetObjectState rightBefore;
    public DatasetObjectState leftAfter;
    public DatasetObjectState rightAfter;
    public DatasetVideoMetadata metadata;
    public ChangeBlindnessTimingData timeline;
    public ChangeBlindnessCameraRouteData cameraRoute;
    public List<DatasetQaPair> qa = new List<DatasetQaPair>();
    public List<DatasetConversationTurn> conversations = new List<DatasetConversationTurn>();
}
