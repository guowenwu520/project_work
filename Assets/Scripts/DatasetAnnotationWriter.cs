using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEngine;

public static class DatasetAnnotationWriter
{
    public static void WriteAll(
        string jobDirectory,
        string videoPath,
        BatchJob job)
    {
        if (string.IsNullOrWhiteSpace(jobDirectory) || job == null)
        {
            return;
        }

        Directory.CreateDirectory(jobDirectory);

        List<DatasetQaPair> qa = DatasetQAGenerator.Generate(job);
        string normalizedVideoPath = NormalizePath(videoPath);
        string videoId =
            "scene_" +
            job.id.ToString("D6", CultureInfo.InvariantCulture);

        DatasetAnnotation annotation = new DatasetAnnotation
        {
            schemaVersion = DatasetBuildInfo.SchemaVersion,
            batchId = job.id,
            seed = job.seed,
            videoPath = normalizedVideoPath,
            changeType = job.changeType,
            changedSlot = job.changedSlot,
            leftBefore = job.leftBefore,
            rightBefore = job.rightBefore,
            leftAfter = job.leftAfter,
            rightAfter = job.rightAfter,
            qa = qa
        };

        File.WriteAllText(
            Path.Combine(jobDirectory, "annotation.json"),
            JsonUtility.ToJson(annotation, true),
            new UTF8Encoding(false));

        DatasetVideoQaRecord record = new DatasetVideoQaRecord
        {
            video_id = videoId,
            video_path = normalizedVideoPath,
            scene_type = DatasetQATemplateLibrary.SceneType,
            questions = qa
        };

        File.WriteAllText(
            Path.Combine(jobDirectory, "qa_entries.json"),
            JsonUtility.ToJson(record, true) + "\n",
            new UTF8Encoding(false));

        WriteText(jobDirectory, videoId, job, qa);
    }

    private static void WriteText(
        string jobDirectory,
        string videoId,
        BatchJob job,
        List<DatasetQaPair> qa)
    {
        StringBuilder builder = new StringBuilder();
        builder.AppendLine("Video ID: " + videoId);
        builder.AppendLine("Scene type: " + DatasetQATemplateLibrary.SceneType);
        builder.AppendLine("Change type: " + job.changeType);
        builder.AppendLine("Changed slot: " + job.changedSlot);
        builder.AppendLine(
            "Before: left=" +
            job.leftBefore.Description +
            ", right=" +
            job.rightBefore.Description);
        builder.AppendLine(
            "After:  left=" +
            job.leftAfter.Description +
            ", right=" +
            job.rightAfter.Description);
        builder.AppendLine();

        for (int i = 0; i < qa.Count; i++)
        {
            builder.AppendLine(
                "Q" +
                (i + 1) +
                ": " +
                qa[i].question);
            builder.AppendLine(
                "A" +
                (i + 1) +
                ": " +
                qa[i].answer);
            builder.AppendLine();
        }

        File.WriteAllText(
            Path.Combine(jobDirectory, "qa.txt"),
            builder.ToString(),
            new UTF8Encoding(false));
    }

    private static string NormalizePath(string value)
    {
        return (value ?? string.Empty)
            .Replace('\\', '/')
            .TrimStart('/');
    }
}
