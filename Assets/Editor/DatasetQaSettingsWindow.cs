#if UNITY_EDITOR
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

public sealed class DatasetQaSettingsWindow : EditorWindow
{
    private DatasetGenerationConfig config;
    private int previewIndex;
    private Vector2 scroll;
    private List<DatasetQaPair> previewQa;
    private BatchJob previewJob;

    [MenuItem("Tools/Change Blindness/Dataset QA Settings")]
    public static void Open()
    {
        GetWindow<DatasetQaSettingsWindow>("Dataset QA");
    }

    private void OnEnable()
    {
        LoadConfig();
    }

    private void OnGUI()
    {
        if (config == null)
        {
            LoadConfig();
        }

        scroll = EditorGUILayout.BeginScrollView(scroll);
        EditorGUILayout.LabelField("Video + QA Dataset Settings", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "All imported and built-in props are sampled from the same pool. Built-in simple props support six change types, including color change. Imported props keep their original materials and use only the other five change types.",
            MessageType.Info);

        config.baseSeed = EditorGUILayout.IntField("Base Seed", config.baseSeed);
        config.sameClassPairProbability = EditorGUILayout.Slider("Same-class Pair Probability", config.sameClassPairProbability, 0f, 1f);
        config.videoPathPrefix = EditorGUILayout.TextField("Video Path Prefix", config.videoPathPrefix);

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("Change Probabilities", EditorStyles.boldLabel);
        DatasetChangeProbabilities p = config.changeProbabilities;
        p.oneObjectReplacement = EditorGUILayout.Slider("One Object Replaced", p.oneObjectReplacement, 0f, 1f);
        p.twoObjectsReplacement = EditorGUILayout.Slider("Two Objects Replaced", p.twoObjectsReplacement, 0f, 1f);
        p.colorChange = EditorGUILayout.Slider("Built-in Object Color Change", p.colorChange, 0f, 1f);
        p.distanceIncrease = EditorGUILayout.Slider("Distance Becomes Larger", p.distanceIncrease, 0f, 1f);
        p.swapPositions = EditorGUILayout.Slider("Swap Positions", p.swapPositions, 0f, 1f);
        p.noChange = EditorGUILayout.Slider("No Change", p.noChange, 0f, 1f);

        float sum = p.oneObjectReplacement + p.twoObjectsReplacement + p.colorChange + p.distanceIncrease + p.swapPositions + p.noChange;
        EditorGUILayout.LabelField("Probability Sum", sum.ToString("0.000"));
        EditorGUILayout.HelpBox("Values are normalized automatically, so the sum does not have to equal 1.", MessageType.None);

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("Built-in Prop Colors (Imported props ignore this)", EditorStyles.boldLabel);
        if (config.colors == null)
        {
            config.colors = new List<DatasetColorDefinition>();
        }
        for (int i = 0; i < config.colors.Count; i++)
        {
            using (new EditorGUILayout.HorizontalScope())
            {
                config.colors[i].name = EditorGUILayout.TextField(config.colors[i].name, GUILayout.Width(110f));
                Color color = config.colors[i].ToUnityColor();
                Color updated = EditorGUILayout.ColorField(color);
                config.colors[i].hex = "#" + ColorUtility.ToHtmlStringRGB(updated);
                if (GUILayout.Button("-", GUILayout.Width(25f)))
                {
                    config.colors.RemoveAt(i);
                    i--;
                }
            }
        }
        if (GUILayout.Button("Add Color"))
        {
            config.colors.Add(new DatasetColorDefinition { name = "new color", hex = "#808080" });
        }

        EditorGUILayout.Space();
        if (GUILayout.Button("Save dataset_config.json", GUILayout.Height(32f)))
        {
            SaveConfig();
        }

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("Preview One Dataset Item", EditorStyles.boldLabel);
        previewIndex = EditorGUILayout.IntField("Batch Index", previewIndex);
        if (GUILayout.Button("Generate Preview Metadata + QA", GUILayout.Height(28f)))
        {
            SaveConfig();
            previewJob = BatchConfiguration.ResolveJob(previewIndex, config.baseSeed);
            previewQa = DatasetQAGenerator.Generate(previewJob);
        }

        if (previewJob != null)
        {
            EditorGUILayout.HelpBox(
                "Before: " + previewJob.leftBefore.Description + " | " + previewJob.rightBefore.Description + "\n" +
                "After:  " + previewJob.leftAfter.Description + " | " + previewJob.rightAfter.Description + "\n" +
                "Change: " + previewJob.changeType + " (" + previewJob.changedSlot + ")",
                MessageType.None);

            if (previewQa != null)
            {
                for (int i = 0; i < previewQa.Count; i++)
                {
                    EditorGUILayout.LabelField("Q" + (i + 1) + ": " + previewQa[i].question, EditorStyles.wordWrappedLabel);
                    EditorGUILayout.LabelField("A" + (i + 1) + ": " + previewQa[i].answer, EditorStyles.wordWrappedLabel);
                    EditorGUILayout.Space(4f);
                }
            }
        }

        EditorGUILayout.EndScrollView();
    }

    private void LoadConfig()
    {
        string path = GetConfigPath();
        if (File.Exists(path))
        {
            config = JsonUtility.FromJson<DatasetGenerationConfig>(File.ReadAllText(path));
        }
        if (config == null)
        {
            config = DatasetConfiguration.CreateDefault();
        }
        if (config.changeProbabilities == null)
        {
            config.changeProbabilities = new DatasetChangeProbabilities();
        }
    }

    private void SaveConfig()
    {
        string path = GetConfigPath();
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        File.WriteAllText(path, JsonUtility.ToJson(config, true));
        DatasetConfiguration.ClearCache();
        AssetDatabase.Refresh();
        ShowNotification(new GUIContent("Dataset settings saved"));
    }

    private static string GetConfigPath()
    {
        return Path.Combine(Application.dataPath, "StreamingAssets/dataset_config.json");
    }
}
#endif
