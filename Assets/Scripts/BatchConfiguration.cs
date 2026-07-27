using System;
using System.Collections.Generic;
using UnityEngine;

public static class BatchConfiguration
{
    private static readonly string[] BuiltInPropNames = BuiltInPropCatalog.Names;

    public static BatchJob ResolveJob(int requestedIndex)
    {
        return ResolveJob(requestedIndex, int.MinValue, string.Empty);
    }

    public static BatchJob ResolveJob(int requestedIndex, int seedOverride)
    {
        return ResolveJob(requestedIndex, seedOverride, string.Empty);
    }

    public static BatchJob ResolveJob(int requestedIndex, int seedOverride, string forcedChangeType)
    {
        return CreateDatasetJob(requestedIndex, seedOverride, forcedChangeType);
    }

    public static BatchJob CreateDeterministicJob(int batchIndex)
    {
        return CreateDatasetJob(batchIndex, int.MinValue, string.Empty);
    }

    public static BatchJob CreateDatasetJob(int batchIndex, int seedOverride)
    {
        return CreateDatasetJob(batchIndex, seedOverride, string.Empty);
    }

    public static BatchJob CreateDatasetJob(int batchIndex, int seedOverride, string forcedChangeType)
    {
        int safeIndex = batchIndex == int.MinValue ? int.MaxValue : Mathf.Abs(batchIndex);
        DatasetGenerationConfig config = DatasetConfiguration.Load();
        string[] propNames = GetActivePropNames();
        if (propNames == null || propNames.Length == 0)
        {
            propNames = BuiltInPropNames;
        }

        int baseSeed = seedOverride == int.MinValue ? config.baseSeed : seedOverride;
        int resolvedSeed = unchecked(baseSeed + safeIndex * 7919 + 104729);
        System.Random random = new System.Random(resolvedSeed);
        string normalizedForcedType = NormalizeChangeType(forcedChangeType);

        // Color-change tests must contain at least one recolorable built-in object.
        string leftProp;
        string rightProp;
        if (normalizedForcedType == DatasetChangeTypes.ColorChange)
        {
            leftProp = BuiltInPropNames[random.Next(BuiltInPropNames.Length)];
            rightProp = PickDistinctProp(propNames, random, leftProp);
        }
        else
        {
            // Imported models and built-in simple props are selected from the same pool.
            leftProp = propNames[random.Next(propNames.Length)];
            rightProp = PickDistinctProp(propNames, random, leftProp);
        }

        BatchJob job = new BatchJob
        {
            id = batchIndex,
            seed = resolvedSeed,
            leftBefore = CreateState("left", leftProp, config, random, null),
            rightBefore = CreateState("right", rightProp, config, random, null)
        };

        if (job.leftBefore.supportsColor && job.rightBefore.supportsColor &&
            string.Equals(job.leftBefore.propClass, job.rightBefore.propClass, StringComparison.OrdinalIgnoreCase) &&
            string.Equals(job.leftBefore.color, job.rightBefore.color, StringComparison.OrdinalIgnoreCase) &&
            config.colors.Count > 1)
        {
            job.rightBefore.color = PickColorName(config, random, job.leftBefore.color, null);
        }

        job.leftAfter = job.leftBefore.Clone();
        job.rightAfter = job.rightBefore.Clone();

        bool hasColorEligibleObject = job.leftBefore.supportsColor || job.rightBefore.supportsColor;
        job.changeType = string.IsNullOrEmpty(normalizedForcedType)
            ? PickChangeType(config.changeProbabilities, random, hasColorEligibleObject)
            : normalizedForcedType;
        job.changedSlot = random.Next(2) == 0 ? "left" : "right";

        switch (job.changeType)
        {
            case DatasetChangeTypes.OneObjectReplacement:
            {
                DatasetObjectState before = job.GetBefore(job.changedSlot);
                DatasetObjectState after = job.GetAfter(job.changedSlot);
                after.propClass = PickDistinctProp(
                    propNames,
                    random,
                    job.leftBefore.propClass,
                    job.rightBefore.propClass);
                UpdateStateAfterReplacement(after, config, random, before.supportsColor ? before.color : null);
                break;
            }
            case DatasetChangeTypes.TwoObjectsReplacement:
            {
                string newLeft = PickDistinctProp(
                    propNames,
                    random,
                    job.leftBefore.propClass,
                    job.rightBefore.propClass);
                string newRight = PickDistinctProp(
                    propNames,
                    random,
                    job.leftBefore.propClass,
                    job.rightBefore.propClass,
                    newLeft);
                job.leftAfter = CreateState("left", newLeft, config, random, null);
                job.rightAfter = CreateState("right", newRight, config, random, null);
                job.changedSlot = "both";
                break;
            }
            case DatasetChangeTypes.ColorChange:
            {
                if (!hasColorEligibleObject)
                {
                    // Safety fallback for manually forced color change.
                    job.leftBefore = CreateState(
                        "left",
                        BuiltInPropNames[random.Next(BuiltInPropNames.Length)],
                        config,
                        random,
                        null);
                    job.leftAfter = job.leftBefore.Clone();
                    hasColorEligibleObject = true;
                }

                if (job.leftBefore.supportsColor && job.rightBefore.supportsColor)
                {
                    job.changedSlot = random.Next(2) == 0 ? "left" : "right";
                }
                else
                {
                    job.changedSlot = job.leftBefore.supportsColor ? "left" : "right";
                }

                DatasetObjectState before = job.GetBefore(job.changedSlot);
                DatasetObjectState after = job.GetAfter(job.changedSlot);
                after.color = PickColorName(config, random, before.color, null);
                break;
            }
            case DatasetChangeTypes.DistanceIncrease:
                job.changedSlot = "both";
                break;
            case DatasetChangeTypes.SwapPositions:
            {
                DatasetObjectState originalLeft = job.leftBefore.Clone();
                DatasetObjectState originalRight = job.rightBefore.Clone();
                job.leftAfter = originalRight;
                job.leftAfter.slot = "left";
                job.rightAfter = originalLeft;
                job.rightAfter.slot = "right";
                job.changedSlot = "both";
                break;
            }
            case DatasetChangeTypes.NoChange:
            default:
                job.changeType = DatasetChangeTypes.NoChange;
                job.changedSlot = "none";
                break;
        }

        PopulateLegacyFields(job);
        return job;
    }

    public static string[] GetActivePropNames()
    {
        List<string> names = new List<string>(BuiltInPropNames);
        string[] imported = ImportedPropLibrary.GetAvailablePropNames();
        if (imported != null)
        {
            for (int i = 0; i < imported.Length; i++)
            {
                string item = imported[i];
                if (!string.IsNullOrWhiteSpace(item) && !ContainsIgnoreCase(names, item))
                {
                    names.Add(item);
                }
            }
        }
        return names.ToArray();
    }

    private static DatasetObjectState CreateState(
        string slot,
        string propClass,
        DatasetGenerationConfig config,
        System.Random random,
        string excludedColor)
    {
        bool supportsColor = PropSupportsColor(propClass);
        return new DatasetObjectState
        {
            slot = slot,
            propClass = propClass,
            label = ResolveQaLabel(propClass),
            color = supportsColor ? PickColorName(config, random, excludedColor, null) : string.Empty,
            supportsColor = supportsColor
        };
    }

    private static void UpdateStateAfterReplacement(
        DatasetObjectState state,
        DatasetGenerationConfig config,
        System.Random random,
        string excludedColor)
    {
        state.label = ResolveQaLabel(state.propClass);
        state.supportsColor = PropSupportsColor(state.propClass);
        state.color = state.supportsColor ? PickColorName(config, random, excludedColor, null) : string.Empty;
    }

    private static bool PropSupportsColor(string propClass)
    {
        // Imported prefabs keep their authored materials; only built-in shapes are recolored.
        return !ImportedPropLibrary.HasPrefab(propClass);
    }

    private static string ResolveQaLabel(string propClass)
    {
        // Imported models keep `name` as the stable runtime/model lookup key, while
        // `displayName` in ModelBundles/prop_manifest.json is the human-readable
        // name used by generated QA.
        if (ImportedPropLibrary.HasPrefab(propClass))
        {
            string displayName = ImportedPropLibrary.GetDisplayName(propClass);
            if (!string.IsNullOrWhiteSpace(displayName))
            {
                return displayName.Trim();
            }
        }

        // Built-in procedural props do not come from the imported-model manifest,
        // so keep the existing human-readable name conversion for them.
        return DatasetConfiguration.ModelNameToQaLabel(propClass);
    }

    private static string PickColorName(
        DatasetGenerationConfig config,
        System.Random random,
        string excludedName,
        string secondExcludedName)
    {
        if (config.colors == null || config.colors.Count == 0)
        {
            return string.Empty;
        }
        if (config.colors.Count == 1)
        {
            return config.colors[0].name;
        }

        string selected = config.colors[random.Next(config.colors.Count)].name;
        int guard = 0;
        while ((Matches(selected, excludedName) || Matches(selected, secondExcludedName)) && guard++ < 100)
        {
            selected = config.colors[random.Next(config.colors.Count)].name;
        }
        return selected;
    }

    private static string PickDistinctProp(
        string[] propNames,
        System.Random random,
        params string[] excludedClasses)
    {
        List<string> candidates = new List<string>();
        for (int i = 0; i < propNames.Length; i++)
        {
            string candidate = propNames[i];
            if (string.IsNullOrWhiteSpace(candidate) ||
                ContainsIgnoreCase(candidates, candidate))
            {
                continue;
            }

            bool excluded = false;
            if (excludedClasses != null)
            {
                for (int j = 0; j < excludedClasses.Length; j++)
                {
                    if (Matches(candidate, excludedClasses[j]))
                    {
                        excluded = true;
                        break;
                    }
                }
            }

            if (!excluded)
            {
                candidates.Add(candidate);
            }
        }

        if (candidates.Count == 0)
        {
            throw new InvalidOperationException(
                "The active prop pool does not contain enough unique objects " +
                "for the requested scene.");
        }

        return candidates[random.Next(candidates.Count)];
    }

    private static bool Matches(string a, string b)
    {
        return !string.IsNullOrEmpty(a) && !string.IsNullOrEmpty(b) &&
               string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
    }

    private static bool ContainsIgnoreCase(List<string> values, string item)
    {
        for (int i = 0; i < values.Count; i++)
        {
            if (Matches(values[i], item))
            {
                return true;
            }
        }
        return false;
    }

    private static string NormalizeChangeType(string value)
    {
        string key = (value ?? string.Empty).Trim().ToLowerInvariant().Replace('-', '_').Replace(' ', '_');
        switch (key)
        {
            case "one":
            case "one_replace":
            case "one_object_replacement":
                return DatasetChangeTypes.OneObjectReplacement;
            case "two":
            case "two_replace":
            case "two_objects_replacement":
                return DatasetChangeTypes.TwoObjectsReplacement;
            case "color":
            case "color_change":
            case "same_object_color_change":
                return DatasetChangeTypes.ColorChange;
            case "distance":
            case "distance_increase":
                return DatasetChangeTypes.DistanceIncrease;
            case "swap":
            case "swap_positions":
                return DatasetChangeTypes.SwapPositions;
            case "none":
            case "no_change":
                return DatasetChangeTypes.NoChange;
            default:
                return string.Empty;
        }
    }

    private static string PickChangeType(
        DatasetChangeProbabilities probabilities,
        System.Random random,
        bool allowColorChange)
    {
        float oneReplace = Mathf.Max(0f, probabilities.oneObjectReplacement);
        float twoReplace = Mathf.Max(0f, probabilities.twoObjectsReplacement);
        float colorChange = allowColorChange ? Mathf.Max(0f, probabilities.colorChange) : 0f;
        float distance = Mathf.Max(0f, probabilities.distanceIncrease);
        float swap = Mathf.Max(0f, probabilities.swapPositions);
        float noChange = Mathf.Max(0f, probabilities.noChange);
        float total = oneReplace + twoReplace + colorChange + distance + swap + noChange;
        if (total <= 0.0001f)
        {
            return DatasetChangeTypes.OneObjectReplacement;
        }

        double value = random.NextDouble() * total;
        if (value < oneReplace) return DatasetChangeTypes.OneObjectReplacement;
        value -= oneReplace;
        if (value < twoReplace) return DatasetChangeTypes.TwoObjectsReplacement;
        value -= twoReplace;
        if (value < colorChange) return DatasetChangeTypes.ColorChange;
        value -= colorChange;
        if (value < distance) return DatasetChangeTypes.DistanceIncrease;
        value -= distance;
        if (value < swap) return DatasetChangeTypes.SwapPositions;
        return DatasetChangeTypes.NoChange;
    }

    private static void PopulateLegacyFields(BatchJob job)
    {
        job.stableProp = job.leftBefore != null ? job.leftBefore.propClass : string.Empty;
        job.originalProp = job.rightBefore != null ? job.rightBefore.propClass : string.Empty;
        job.replacementProp = job.rightAfter != null ? job.rightAfter.propClass : job.originalProp;
    }
}
