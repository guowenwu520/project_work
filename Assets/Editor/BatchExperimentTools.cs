#if UNITY_EDITOR
using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

public sealed class BatchExperimentWindow : EditorWindow
{
    [MenuItem("Tools/Change Blindness/Build & Dataset Tools")]
    public static void Open()
    {
        GetWindow<BatchExperimentWindow>("Dataset Tools");
    }

    private void OnGUI()
    {
        EditorGUILayout.LabelField("Change Blindness Video + QA Dataset", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "Dataset items are generated from dataset_config.json and the batch index. The old batch_jobs.json file is no longer required.",
            MessageType.Info);

        if (GUILayout.Button("Open Dataset QA Settings", GUILayout.Height(32f)))
        {
            DatasetQaSettingsWindow.Open();
        }

        if (GUILayout.Button("Adjust Imported Prop Placement", GUILayout.Height(30f)))
        {
            PropPlacementWindow.OpenWindow();
        }

        EditorGUILayout.Space();
        if (GUILayout.Button("Build Linux Recorder", GUILayout.Height(30f)))
        {
            BatchExperimentBuild.BuildLinux();
        }

        if (GUILayout.Button("Build Windows Recorder", GUILayout.Height(30f)))
        {
            BatchExperimentBuild.BuildWindows();
        }
    }
}

public static class BatchExperimentBuild
{
    [MenuItem("Tools/Change Blindness/Build/Linux Recorder")]
    public static void BuildLinux()
    {
        string output = Path.GetFullPath(Path.Combine(Application.dataPath, "../Build/Linux/ChangeBlindnessRoom.x86_64"));
        Directory.CreateDirectory(Path.GetDirectoryName(output));
        Build(output, BuildTarget.StandaloneLinux64);
    }

    [MenuItem("Tools/Change Blindness/Build/Windows Recorder")]
    public static void BuildWindows()
    {
        string output = Path.GetFullPath(Path.Combine(Application.dataPath, "../Build/Windows/ChangeBlindnessRoom.exe"));
        Directory.CreateDirectory(Path.GetDirectoryName(output));
        Build(output, BuildTarget.StandaloneWindows64);
    }

    private static void Build(string output, BuildTarget target)
    {
        var options = new BuildPlayerOptions
        {
            scenes = new[] { "Assets/Scenes/Main.unity" },
            locationPathName = output,
            target = target,
            options = BuildOptions.None
        };

        BuildReport report = BuildPipeline.BuildPlayer(options);
        if (report.summary.result == BuildResult.Succeeded)
        {
            Debug.Log($"Build succeeded: {output} ({report.summary.totalSize} bytes)");
            if (!Application.isBatchMode)
            {
                EditorUtility.RevealInFinder(output);
            }
        }
        else
        {
            throw new Exception($"Build failed: {report.summary.result}, errors={report.summary.totalErrors}");
        }
    }
}

[InitializeOnLoad]
public static class ChangeBlindnessProjectSetup
{
    static ChangeBlindnessProjectSetup()
    {
        EditorApplication.delayCall += ApplySettings;
    }

    private static void ApplySettings()
    {
        bool changed = false;
        if (PlayerSettings.companyName != "ChangeBlindness")
        {
            PlayerSettings.companyName = "ChangeBlindness";
            changed = true;
        }

        if (PlayerSettings.productName != "ChangeBlindnessRoom")
        {
            PlayerSettings.productName = "ChangeBlindnessRoom";
            changed = true;
        }

        if (PlayerSettings.colorSpace != ColorSpace.Linear)
        {
            PlayerSettings.colorSpace = ColorSpace.Linear;
            changed = true;
        }

        if (!PlayerSettings.runInBackground)
        {
            PlayerSettings.runInBackground = true;
            changed = true;
        }

        if (PlayerSettings.defaultScreenWidth != 1920 || PlayerSettings.defaultScreenHeight != 1080)
        {
            PlayerSettings.defaultScreenWidth = 1920;
            PlayerSettings.defaultScreenHeight = 1080;
            changed = true;
        }

        if (PlayerSettings.fullScreenMode != FullScreenMode.Windowed)
        {
            PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
            changed = true;
        }

        if (changed)
        {
            AssetDatabase.SaveAssets();
            Debug.Log("Applied linear color space and capture-friendly player settings.");
        }
    }
}
#endif
