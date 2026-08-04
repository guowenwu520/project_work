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

        ValidateCanonicalChangedSlot(job);

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

            result.Add(
                new DatasetQaPair(
                    question,
                    answer,
                    NormalizeQuestionType(
                        template.answer_style)));
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

    private static void ValidateCanonicalChangedSlot(BatchJob job)
    {
        string expected =
            BatchConfiguration.GetCanonicalChangedSlot(job.changeType);
        string actual = (job.changedSlot ?? string.Empty).Trim();
        if (!string.Equals(
                actual,
                expected,
                StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(
                "Change type '" +
                job.changeType +
                "' must use changedSlot='" +
                expected +
                "', but the job uses '" +
                actual +
                "'. The fixed QA wording would not match the rendered " +
                "first-view/second-view positions.");
        }
    }

    private static string NormalizeQuestionType(string value)
    {
        string key = (value ?? string.Empty)
            .Trim()
            .ToLowerInvariant()
            .Replace('-', '_')
            .Replace(' ', '_');

        return key == "yes_no" ||
               key == "yes_or_no"
            ? "yes_or_no"
            : "descriptive";
    }

    private static Dictionary<string, string> BuildContext(
        BatchJob job,
        System.Random random)
    {
        Dictionary<string, string> values =
            new Dictionary<string, string>(StringComparer.Ordinal);

        Put(
            values,
            "view_a_count",
            job.InitialObjectCount.ToString());
        Put(
            values,
            "view_b_count",
            job.FinalObjectCount.ToString());

        DatasetObjectState leftBefore = job.leftBefore;
        DatasetObjectState rightBefore = job.rightBefore;
        DatasetObjectState leftAfter = job.leftAfter;
        DatasetObjectState rightAfter = job.rightAfter;

        // The a/b suffix always names the physical tabletop slot, never the
        // changed object or an object's identity across views:
        //   A = first-view left  / second-view right
        //   B = first-view right / second-view left
        // leftBefore/leftAfter are physical slot A; rightBefore/rightAfter
        // are physical slot B. Canonical-slot validation above guarantees
        // that fixed-wording changes happen in the slot required by the XLSX.

        if (string.Equals(
            job.changeType,
            DatasetChangeTypes.OneObjectReplacement,
            StringComparison.OrdinalIgnoreCase))
        {
            Put(values, "view_a_object_a", Description(leftBefore));
            Put(values, "view_b_object_a", Description(leftAfter));
            Put(values, "view_a_object_b", Description(rightBefore));
            Put(values, "view_b_object_b", Description(rightAfter));
            Put(values, "view_a_position_a", ViewAPositionA());
            Put(values, "view_b_position_a", ViewBPositionA());
        }
        else if (string.Equals(
            job.changeType,
            DatasetChangeTypes.ColorChange,
            StringComparison.OrdinalIgnoreCase))
        {
            Put(values, "view_a_object_a", Label(leftBefore));
            Put(values, "view_b_object_a", Label(leftAfter));
            Put(values, "view_a_object_b", Description(rightBefore));
            Put(values, "view_b_object_b", Description(rightAfter));
            Put(values, "view_a_color_a", ColorValue(leftBefore));
            Put(values, "view_b_color_a", ColorValue(leftAfter));
            Put(values, "view_a_position_a", ViewAPositionA());
            Put(values, "view_b_position_a", ViewBPositionA());
        }
        else if (
            string.Equals(
                job.changeType,
                DatasetChangeTypes.DistanceIncrease,
                StringComparison.OrdinalIgnoreCase) ||
            string.Equals(
                job.changeType,
                DatasetChangeTypes.DistanceDecrease,
                StringComparison.OrdinalIgnoreCase))
        {
            Put(values, "view_a_object_a", Description(leftBefore));
            Put(values, "view_a_object_b", Description(rightBefore));
            Put(values, "view_b_object_a", Description(leftAfter));
            Put(values, "view_b_object_b", Description(rightAfter));
            Put(values, "view_a_position_a", ViewAPositionA());
            Put(values, "view_a_position_b", ViewAPositionB());
            Put(values, "view_b_position_a", ViewBPositionA());
            Put(values, "view_b_position_b", ViewBPositionB());
        }
        else if (string.Equals(
            job.changeType,
            DatasetChangeTypes.SwapPositions,
            StringComparison.OrdinalIgnoreCase))
        {
            Put(values, "view_a_object_a", Description(leftBefore));
            Put(values, "view_a_object_b", Description(rightBefore));
            Put(values, "view_b_object_a", Description(leftAfter));
            Put(values, "view_b_object_b", Description(rightAfter));
            Put(values, "view_a_position_a", ViewAPositionA());
            Put(values, "view_a_position_b", ViewAPositionB());
            Put(values, "view_b_position_a", ViewBPositionA());
            Put(values, "view_b_position_b", ViewBPositionB());
        }
        else if (string.Equals(
            job.changeType,
            DatasetChangeTypes.ObjectAdding,
            StringComparison.OrdinalIgnoreCase))
        {
            Put(values, "view_a_object_a", Description(leftBefore));
            Put(values, "view_b_object_a", Description(leftAfter));
            Put(values, "view_b_object_b", Description(rightAfter));
            Put(values, "view_a_position_a", ViewAPositionA());
            Put(values, "view_b_position_a", ViewBPositionA());
            Put(values, "view_b_position_b", ViewBPositionB());
        }
        else if (string.Equals(
            job.changeType,
            DatasetChangeTypes.ObjectDeleting,
            StringComparison.OrdinalIgnoreCase))
        {
            Put(values, "view_a_object_a", Description(leftBefore));
            Put(values, "view_a_object_b", Description(rightBefore));
            Put(values, "view_a_position_a", ViewAPositionA());
            Put(values, "view_a_position_b", ViewAPositionB());
            Put(values, "view_b_object_a", Description(leftAfter));
            Put(values, "view_b_position_a", ViewBPositionA());
        }
        else
        {
            Put(values, "view_a_object_a", Description(leftBefore));
            Put(values, "view_a_object_b", Description(rightBefore));
            Put(values, "view_b_object_a", Description(leftAfter));
            Put(values, "view_b_object_b", Description(rightAfter));
            Put(values, "view_a_position_a", ViewAPositionA());
            Put(values, "view_a_position_b", ViewAPositionB());
            Put(values, "view_b_position_a", ViewBPositionA());
            Put(values, "view_b_position_b", ViewBPositionB());
            Put(values, "view_a_color_a", ColorValue(leftBefore));
            Put(values, "view_b_color_a", ColorValue(leftAfter));

            Put(
                values,
                "view_b_object_list",
                "The " +
                Description(leftAfter) +
                " and the " +
                Description(rightAfter));
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

    private static string ColorValue(DatasetObjectState state)
    {
        if (state == null ||
            string.IsNullOrWhiteSpace(state.color))
        {
            return "Null";
        }

        return state.color.Trim();
    }

    private static string ViewAPositionA()
    {
        return "the left side (1st view) of the table";
    }

    private static string ViewAPositionB()
    {
        return "the right side (1st view) of the table";
    }

    private static string ViewBPositionA()
    {
        return "the right side (2nd view) of the table";
    }

    private static string ViewBPositionB()
    {
        return "the left side (2nd view) of the table";
    }
}
