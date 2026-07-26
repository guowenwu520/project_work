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
            new System.Random(unchecked(job.seed ^ 0x5F3759DF));

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

        Put(values, "object_a", Description(leftBefore));
        Put(values, "object_b", Description(rightBefore));

        Put(
            values,
            "final_object_list",
            "The " +
            Description(rightAfter) +
            " and the " +
            Description(leftAfter));

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

            Put(values, "old_object", Description(before));
            Put(values, "new_object", Description(after));
            Put(
                values,
                "initial_position",
                changedLeft ? LeftFirstView() : RightFirstView());
            Put(
                values,
                "final_position",
                changedLeft ? RightSecondView() : LeftSecondView());
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

            Put(values, "initial_position_1", LeftFirstView());
            Put(values, "final_position_1", RightSecondView());
            Put(values, "initial_position_2", RightFirstView());
            Put(values, "final_position_2", LeftSecondView());
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

            Put(values, "object", Label(before));
            Put(values, "original_color", before.color);
            Put(values, "new_color", after.color);
            Put(
                values,
                "initial_position",
                changedLeft ? LeftFirstView() : RightFirstView());
            Put(
                values,
                "final_position",
                changedLeft ? RightSecondView() : LeftSecondView());
        }
        else if (string.Equals(
            job.changeType,
            DatasetChangeTypes.DistanceIncrease,
            StringComparison.OrdinalIgnoreCase))
        {
            Put(values, "initial_position_a", LeftFirstView());
            Put(values, "initial_position_b", RightFirstView());
            Put(values, "final_position_a", RightSecondView());
            Put(values, "final_position_b", LeftSecondView());
        }
        else if (string.Equals(
            job.changeType,
            DatasetChangeTypes.SwapPositions,
            StringComparison.OrdinalIgnoreCase))
        {
            Put(values, "object_a_initial_position", LeftFirstView());
            Put(values, "object_a_final_position", LeftSecondView());
            Put(values, "object_b_initial_position", RightFirstView());
            Put(values, "object_b_final_position", RightSecondView());
        }
        else
        {
            bool selectLeft = random.Next(2) == 0;
            DatasetObjectState selected =
                selectLeft ? leftBefore : rightBefore;

            Put(values, "selected_object", Description(selected));
            Put(
                values,
                "initial_selected_position",
                selectLeft ? LeftFirstView() : RightFirstView());
            Put(
                values,
                "final_selected_position",
                selectLeft ? RightSecondView() : LeftSecondView());

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

    private static string LeftFirstView()
    {
        return "the left side of the table in the first view";
    }

    private static string RightFirstView()
    {
        return "the right side of the table in the first view";
    }

    private static string LeftSecondView()
    {
        return "the left side of the table in the second view";
    }

    private static string RightSecondView()
    {
        return "the right side of the table in the second view";
    }
}
