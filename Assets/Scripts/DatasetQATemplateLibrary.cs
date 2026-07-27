using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

[Serializable]
public sealed class TabletopQaTemplateLibraryData
{
    public string schema_version;
    public string scene_type = "tabletop";
    public int questions_per_scene = 8;
    public int sampling_salt = 0;
    public List<TabletopQaTemplateGroup> change_types = new List<TabletopQaTemplateGroup>();
}

[Serializable]
public sealed class TabletopQaTemplateGroup
{
    public string change_type;
    public string name_cn;
    public string description;
    public List<TabletopQaTemplate> templates = new List<TabletopQaTemplate>();
}

[Serializable]
public sealed class TabletopQaTemplate
{
    public string template_id;
    public string question;
    public string answer;
    public string answer_style;
    public List<string> required_variables = new List<string>();
}

/// <summary>
/// Loads the generated tabletop QA library from StreamingAssets.
///
/// To revise questions, edit QAs_v5_d.xlsx. Existing project scripts
/// synchronize sheets 01-08 and Variables into this generated JSON.
/// </summary>
public static class DatasetQATemplateLibrary
{
    public const string FileName = "tabletop_qa_templates.json";

    private static TabletopQaTemplateLibraryData cached;

    public static string SceneType
    {
        get
        {
            TabletopQaTemplateLibraryData data = Load();
            return string.IsNullOrWhiteSpace(data.scene_type)
                ? "tabletop"
                : data.scene_type.Trim();
        }
    }

    public static int QuestionsPerScene
    {
        get
        {
            return Math.Max(1, Load().questions_per_scene);
        }
    }

    public static int SamplingSalt
    {
        get
        {
            return Load().sampling_salt;
        }
    }

    public static List<TabletopQaTemplate> GetTemplates(string changeType)
    {
        TabletopQaTemplateLibraryData data = Load();
        string normalized = NormalizeChangeType(changeType);

        for (int i = 0; i < data.change_types.Count; i++)
        {
            TabletopQaTemplateGroup group = data.change_types[i];
            if (group == null)
            {
                continue;
            }

            if (string.Equals(
                NormalizeChangeType(group.change_type),
                normalized,
                StringComparison.OrdinalIgnoreCase))
            {
                return group.templates ?? new List<TabletopQaTemplate>();
            }
        }

        throw new InvalidOperationException(
            "No QA template group exists for change type '" +
            changeType +
            "' in " +
            FileName +
            ".");
    }

    public static void ClearCache()
    {
        cached = null;
    }

    private static TabletopQaTemplateLibraryData Load()
    {
        if (cached != null)
        {
            return cached;
        }

        string path = Path.Combine(Application.streamingAssetsPath, FileName);
        if (!File.Exists(path))
        {
            throw new FileNotFoundException(
                "Missing tabletop QA library. Expected: " + path,
                path);
        }

        string json = File.ReadAllText(path);
        cached = JsonUtility.FromJson<TabletopQaTemplateLibraryData>(json);

        if (cached == null ||
            cached.change_types == null ||
            cached.change_types.Count == 0)
        {
            cached = null;
            throw new InvalidDataException(
                "The tabletop QA library is empty or invalid: " + path);
        }

        return cached;
    }

    private static string NormalizeChangeType(string value)
    {
        string key = (value ?? string.Empty)
            .Trim()
            .ToLowerInvariant()
            .Replace('-', '_')
            .Replace(' ', '_');

        switch (key)
        {
            case "single_object_replacement":
            case "one_object_replacement":
                return DatasetChangeTypes.OneObjectReplacement;

            case "color_change":
            case "same_object_color_change":
                return DatasetChangeTypes.ColorChange;

            case "distance_increase":
                return DatasetChangeTypes.DistanceIncrease;

            case "distance_decrease":
                return DatasetChangeTypes.DistanceDecrease;

            case "position_swap":
            case "swap_position":
            case "swap_positions":
                return DatasetChangeTypes.SwapPositions;

            case "none":
            case "no_change":
                return DatasetChangeTypes.NoChange;

            case "object_addition":
            case "object_adding":
                return DatasetChangeTypes.ObjectAdding;

            case "object_removal":
            case "object_deleting":
                return DatasetChangeTypes.ObjectDeleting;

            default:
                return key;
        }
    }
}
