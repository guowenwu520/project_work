using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;
using UnityEngine;

public static class DatasetConfiguration
{
    private static DatasetGenerationConfig cachedConfig;

    public static DatasetGenerationConfig Load()
    {
        if (cachedConfig != null)
        {
            return cachedConfig;
        }

        string path = Path.Combine(Application.streamingAssetsPath, "dataset_config.json");
        try
        {
            if (File.Exists(path))
            {
                cachedConfig = JsonUtility.FromJson<DatasetGenerationConfig>(File.ReadAllText(path));
            }
        }
        catch (Exception exception)
        {
            Debug.LogWarning("Failed to load dataset_config.json: " + exception.Message);
        }

        if (cachedConfig == null)
        {
            cachedConfig = CreateDefault();
        }

        EnsureValid(cachedConfig);
        return cachedConfig;
    }

    public static DatasetGenerationConfig CreateDefault()
    {
        DatasetGenerationConfig config = new DatasetGenerationConfig();
        config.colors = new List<DatasetColorDefinition>
        {
            NewColor("red", "#D94A4A"),
            NewColor("blue", "#3F73D8"),
            NewColor("green", "#4C9A55"),
            NewColor("yellow", "#E1B93F"),
            NewColor("orange", "#D97B35"),
            NewColor("purple", "#8B63C7"),
            NewColor("pink", "#D97FA5"),
            NewColor("cyan", "#43A7B5"),
            NewColor("white", "#D9D9D2"),
            NewColor("gray", "#777D83")
        };
        return config;
    }

    public static DatasetColorDefinition FindColor(string name)
    {
        DatasetGenerationConfig config = Load();
        foreach (DatasetColorDefinition color in config.colors)
        {
            if (color != null && string.Equals(color.name, name, StringComparison.OrdinalIgnoreCase))
            {
                return color;
            }
        }

        return config.colors.Count > 0 ? config.colors[0] : NewColor("white", "#FFFFFF");
    }

    public static string HumanizePropName(string propName)
    {
        return ModelNameToQaLabel(propName);
    }

    public static string ModelNameToQaLabel(string modelName)
    {
        if (string.IsNullOrWhiteSpace(modelName))
        {
            return "item";
        }

        string value = modelName.Trim();
        int hierarchySeparator = value.LastIndexOf("__", StringComparison.Ordinal);
        if (hierarchySeparator >= 0 && hierarchySeparator + 2 < value.Length)
        {
            value = value.Substring(hierarchySeparator + 2);
        }

        value = value.Replace("_Visual", string.Empty).Replace("(Clone)", string.Empty);
        value = Regex.Replace(value, "([a-z0-9])([A-Z])", "$1 $2");
        value = value.Replace('_', ' ').Replace('-', ' ');
        value = Regex.Replace(value, @"\s+", " ").Trim().ToLowerInvariant();
        return string.IsNullOrWhiteSpace(value) ? "item" : value;
    }

    public static void ClearCache()
    {
        cachedConfig = null;
    }

    private static DatasetColorDefinition NewColor(string name, string hex)
    {
        return new DatasetColorDefinition { name = name, hex = hex };
    }

    private static void EnsureValid(DatasetGenerationConfig config)
    {
        if (config.changeProbabilities == null)
        {
            config.changeProbabilities = new DatasetChangeProbabilities();
        }

        if (config.colors == null || config.colors.Count < 2)
        {
            config.colors = CreateDefault().colors;
        }

        config.sameClassPairProbability = Mathf.Clamp01(config.sameClassPairProbability);
        if (string.IsNullOrWhiteSpace(config.videoPathPrefix))
        {
            config.videoPathPrefix = "data";
        }
    }
}
