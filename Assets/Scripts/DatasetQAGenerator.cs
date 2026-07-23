using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

public static class DatasetQAGenerator
{
    private static readonly Regex PlaceholderRegex =
        new Regex(@"\{([A-Za-z0-9_]+)\}", RegexOptions.Compiled);

    public static List<DatasetQaPair> Generate(BatchJob job)
    {
        if (job == null)
        {
            throw new ArgumentNullException("job");
        }

        System.Random random =
            new System.Random(unchecked(
                job.seed ^
                0x5F3759DF ^
                DatasetQATemplateLibrary.SamplingSalt));

        Dictionary<string, string> context = BuildContext(job, random);
        List<TabletopQaTemplate> sourceTemplates =
            new List<TabletopQaTemplate>(
                DatasetQATemplateLibrary.GetTemplates(job.changeType));

        Shuffle(sourceTemplates, random);

        int targetCount = DatasetQATemplateLibrary.QuestionsPerScene;
        List<DatasetQaPair> result = new List<DatasetQaPair>(targetCount);
        HashSet<string> usedQuestions =
            new HashSet<string>(StringComparer.Ordinal);

        for (int i = 0;
             i < sourceTemplates.Count && result.Count < targetCount;
             i++)
        {
            TabletopQaTemplate template = sourceTemplates[i];
            if (template == null)
            {
                continue;
            }

            string question;
            string answer;
            if (!TryRender(
                template.question,
                template.answer,
                context,
                out question,
                out answer))
            {
                continue;
            }

            if (!usedQuestions.Add(question))
            {
                continue;
            }

            result.Add(new DatasetQaPair(question, answer));
        }

        if (result.Count != targetCount)
        {
            throw new InvalidOperationException(
                "Change type '" +
                job.changeType +
                "' produced only " +
                result.Count +
                " valid unique QA pairs, but " +
                targetCount +
                " are required. Check " +
                DatasetQATemplateLibrary.FileName +
                " for missing or unsupported placeholders.");
        }

        return result;
    }

    private static Dictionary<string, string> BuildContext(
        BatchJob job,
        System.Random random)
    {
        Dictionary<string, string> values =
            new Dictionary<string, string>(StringComparer.Ordinal);

        Put(values, "initial_count", "2");
        Put(values, "final_count", "2");

        DatasetObjectState leftBefore = job.leftBefore;
        DatasetObjectState rightBefore = job.rightBefore;
        DatasetObjectState leftAfter = job.leftAfter;
        DatasetObjectState rightAfter = job.rightAfter;

        Put(values, "position_a", LeftPosition());
        Put(values, "position_b", RightPosition());
        Put(values, "position_1", LeftPosition());
        Put(values, "position_2", RightPosition());

        Put(values, "object_a", Description(leftBefore));
        Put(values, "object_b", Description(rightBefore));

        Put(
            values,
            "final_object_list",
            "The " +
            Description(leftAfter) +
            " and the " +
            Description(rightAfter));

        if (string.Equals(
            job.changeType,
            DatasetChangeTypes.OneObjectReplacement,
            StringComparison.OrdinalIgnoreCase))
        {
            bool changedLeft = IsLeft(job.changedSlot);
            DatasetObjectState before =
                changedLeft ? leftBefore : rightBefore;
            DatasetObjectState after =
                changedLeft ? leftAfter : rightAfter;
            DatasetObjectState reference =
                changedLeft ? rightAfter : leftAfter;

            Put(values, "old_object", Description(before));
            Put(values, "new_object", Description(after));
            Put(
                values,
                "original_position",
                changedLeft ? LeftPosition() : RightPosition());
            Put(
                values,
                "relative_position",
                changedLeft ? "to the left of" : "to the right of");
            Put(values, "reference_object", Description(reference));
        }
        else if (string.Equals(
            job.changeType,
            DatasetChangeTypes.TwoObjectsReplacement,
            StringComparison.OrdinalIgnoreCase))
        {
            Put(values, "old_object_1", Description(leftBefore));
            Put(values, "new_object_1", Description(leftAfter));
            Put(values, "old_object_2", Description(rightBefore));
            Put(values, "new_object_2", Description(rightAfter));

        }
        else if (string.Equals(
            job.changeType,
            DatasetChangeTypes.ColorChange,
            StringComparison.OrdinalIgnoreCase))
        {
            bool changedLeft = IsLeft(job.changedSlot);
            DatasetObjectState before =
                changedLeft ? leftBefore : rightBefore;
            DatasetObjectState after =
                changedLeft ? leftAfter : rightAfter;
            DatasetObjectState reference =
                changedLeft ? rightBefore : leftBefore;

            Put(values, "object", Label(before));
            Put(values, "original_color", before.color);
            Put(values, "new_color", after.color);
            Put(
                values,
                "original_position",
                changedLeft ? LeftPosition() : RightPosition());
            Put(
                values,
                "relative_position",
                changedLeft ? "to the left of" : "to the right of");
            Put(values, "reference_object", Description(reference));
        }
        else if (string.Equals(
            job.changeType,
            DatasetChangeTypes.DistanceIncrease,
            StringComparison.OrdinalIgnoreCase))
        {
            Put(values, "initial_position_a", LeftPosition());
            Put(values, "initial_position_b", RightPosition());
            Put(values, "final_position_a", "farther toward the left edge of the table");
            Put(values, "final_position_b", "farther toward the right edge of the table");
        }
        else if (string.Equals(
            job.changeType,
            DatasetChangeTypes.SwapPositions,
            StringComparison.OrdinalIgnoreCase))
        {
        }
        else
        {
            bool selectLeft = random.Next(2) == 0;
            DatasetObjectState selected =
                selectLeft ? leftBefore : rightBefore;
            DatasetObjectState reference =
                selectLeft ? rightBefore : leftBefore;

            Put(values, "selected_object", Description(selected));
            Put(
                values,
                "selected_position",
                selectLeft ? LeftPosition() : RightPosition());
            Put(
                values,
                "relative_position",
                selectLeft ? "to the left of" : "to the right of");
            Put(values, "reference_object", Description(reference));

            if (selected.supportsColor &&
                !string.IsNullOrWhiteSpace(selected.color))
            {
                Put(values, "selected_color", selected.color);
            }
        }

        return values;
    }

    private static bool TryRender(
        string questionTemplate,
        string answerTemplate,
        Dictionary<string, string> context,
        out string question,
        out string answer)
    {
        bool missingQuestionValue = false;
        bool missingAnswerValue = false;

        question = Render(
            questionTemplate,
            context,
            ref missingQuestionValue);
        answer = Render(
            answerTemplate,
            context,
            ref missingAnswerValue);

        if (missingQuestionValue ||
            missingAnswerValue ||
            string.IsNullOrWhiteSpace(question) ||
            string.IsNullOrWhiteSpace(answer))
        {
            question = string.Empty;
            answer = string.Empty;
            return false;
        }

        question = question.Trim();
        answer = answer.Trim();
        return true;
    }

    private static string Render(
        string template,
        Dictionary<string, string> context,
        ref bool missingValue)
    {
        if (string.IsNullOrWhiteSpace(template))
        {
            missingValue = true;
            return string.Empty;
        }

        // C# does not allow a ref/out/in parameter to be captured by an
        // anonymous method. Record the state in a normal local variable,
        // then merge it back into missingValue after replacement.
        bool localMissingValue = false;

        string rendered = PlaceholderRegex.Replace(
            template,
            delegate(Match match)
            {
                string key = match.Groups[1].Value;
                string value;
                if (!context.TryGetValue(key, out value) ||
                    string.IsNullOrWhiteSpace(value))
                {
                    localMissingValue = true;
                    return match.Value;
                }

                return value.Trim();
            });

        if (localMissingValue)
        {
            missingValue = true;
        }

        return rendered;
    }

    private static void Shuffle<T>(
        List<T> values,
        System.Random random)
    {
        for (int i = values.Count - 1; i > 0; i--)
        {
            int j = random.Next(i + 1);
            T temp = values[i];
            values[i] = values[j];
            values[j] = temp;
        }
    }

    private static void Put(
        Dictionary<string, string> values,
        string key,
        string value)
    {
        if (!string.IsNullOrWhiteSpace(key) &&
            !string.IsNullOrWhiteSpace(value))
        {
            values[key] = value.Trim();
        }
    }

    private static bool IsLeft(string slot)
    {
        return string.Equals(
            slot,
            "left",
            StringComparison.OrdinalIgnoreCase);
    }

    private static string Description(DatasetObjectState state)
    {
        if (state == null ||
            string.IsNullOrWhiteSpace(state.Description))
        {
            return "item";
        }

        return state.Description.Trim();
    }

    private static string Label(DatasetObjectState state)
    {
        if (state == null ||
            string.IsNullOrWhiteSpace(state.label))
        {
            return "item";
        }

        return state.label.Trim();
    }

    private static string LeftPosition()
    {
        return "the left side of the table";
    }

    private static string RightPosition()
    {
        return "the right side of the table";
    }
}
