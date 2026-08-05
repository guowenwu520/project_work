using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;

public static class DatasetAnnotationWriter
{
    public static void WriteAll(
        string jobDirectory,
        string videoPath,
        BatchJob job,
        ChangeBlindnessTiming timing,
        ChangeBlindnessCameraRoute cameraRoute)
    {
        if (string.IsNullOrWhiteSpace(jobDirectory) || job == null)
        {
            return;
        }

        Directory.CreateDirectory(jobDirectory);

        string normalizedVideoPath = NormalizePath(videoPath);
        DatasetVideoMetadata metadata =
            BuildMetadata(job);

        DatasetAnnotation annotation = new DatasetAnnotation
        {
            schemaVersion = DatasetBuildInfo.SchemaVersion,
            batchId = job.id,
            seed = job.seed,
            videoPath = normalizedVideoPath,
            changeType = job.changeType,
            changedSlot = job.changedSlot,
            initialObjectCount = job.InitialObjectCount,
            finalObjectCount = job.FinalObjectCount,
            leftBefore = job.leftBefore,
            rightBefore = job.rightBefore,
            leftAfter = job.leftAfter,
            rightAfter = job.rightAfter,
            metadata = metadata,
            timeline = timing == null
                ? null
                : timing.ToData(),
            cameraRoute = cameraRoute == null
                ? null
                : cameraRoute.ToData()
        };

        File.WriteAllText(
            Path.Combine(jobDirectory, "annotation.json"),
            JsonUtility.ToJson(annotation, true),
            new UTF8Encoding(false));
    }

    private static string NormalizePath(string value)
    {
        return (value ?? string.Empty)
            .Replace('\\', '/')
            .TrimStart('/');
    }

    private static DatasetVideoMetadata BuildMetadata(BatchJob job)
    {
        DatasetVideoMetadata metadata =
            new DatasetVideoMetadata
            {
                view_a_object_count = job.InitialObjectCount,
                view_b_object_count = job.FinalObjectCount,
                object_replaced = Matches(
                    job.changeType,
                    DatasetChangeTypes.OneObjectReplacement),
                object_added = Matches(
                    job.changeType,
                    DatasetChangeTypes.ObjectAdding),
                object_removed = Matches(
                    job.changeType,
                    DatasetChangeTypes.ObjectDeleting),
                color_changed = Matches(
                    job.changeType,
                    DatasetChangeTypes.ColorChange),
                position_changed =
                    Matches(
                        job.changeType,
                        DatasetChangeTypes.DistanceIncrease) ||
                    Matches(
                        job.changeType,
                        DatasetChangeTypes.DistanceDecrease) ||
                    Matches(
                        job.changeType,
                        DatasetChangeTypes.SwapPositions),
                distance_changed =
                    Matches(
                        job.changeType,
                        DatasetChangeTypes.DistanceIncrease) ||
                    Matches(
                        job.changeType,
                        DatasetChangeTypes.DistanceDecrease),
                distance_change =
                    Matches(
                        job.changeType,
                        DatasetChangeTypes.DistanceIncrease)
                        ? "increased"
                        : Matches(
                            job.changeType,
                            DatasetChangeTypes.DistanceDecrease)
                            ? "decreased"
                            : "none",
                no_change = Matches(
                    job.changeType,
                    DatasetChangeTypes.NoChange)
            };

        AddState(
            job.leftBefore,
            metadata.view_a_position_a,
            metadata.view_a_color_a);
        AddState(
            job.rightBefore,
            metadata.view_a_position_b,
            metadata.view_a_color_b);
        AddState(
            job.leftAfter,
            metadata.view_b_position_a,
            metadata.view_b_color_a);
        AddState(
            job.rightAfter,
            metadata.view_b_position_b,
            metadata.view_b_color_b);

        if (Matches(
            job.changedSlot,
            "left"))
        {
            metadata.changed_positions.Add("position_a");
        }
        else if (Matches(
            job.changedSlot,
            "right"))
        {
            metadata.changed_positions.Add("position_b");
        }
        else if (Matches(
            job.changedSlot,
            "both"))
        {
            metadata.changed_positions.Add("position_a");
            metadata.changed_positions.Add("position_b");
        }

        return metadata;
    }

    private static void AddState(
        DatasetObjectState state,
        List<string> objects,
        List<string> colors)
    {
        if (state == null || !state.present)
        {
            return;
        }

        string objectName =
            !string.IsNullOrWhiteSpace(state.label)
                ? state.label.Trim()
                : (state.propClass ?? string.Empty).Trim();
        if (!string.IsNullOrWhiteSpace(objectName))
        {
            objects.Add(objectName);
        }

        if (!string.IsNullOrWhiteSpace(state.color))
        {
            colors.Add(state.color.Trim());
        }
        else
        {
            colors.Add("Null");
        }
    }

    private static bool Matches(string first, string second)
    {
        return string.Equals(
            first,
            second,
            StringComparison.OrdinalIgnoreCase);
    }
}
