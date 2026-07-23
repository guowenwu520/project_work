using System.Collections;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEngine;

[RequireComponent(typeof(Camera))]
public sealed class FrameSequenceCapture : MonoBehaviour
{
    private Camera captureCamera;
    private bool enabledForCapture;
    private bool sequenceComplete;
    private bool finalFrameWritten;
    private bool autoQuit;
    private int width;
    private int height;
    private int fps;
    private int frameIndex;
    private string framesDirectory;
    private string jobDirectory;
    private string videoRelativePath;
    private RenderTexture renderTexture;
    private Texture2D readbackTexture;
    private BatchJob activeJob;

    public void Configure(CommandLineOptions options, BatchJob job)
    {
        activeJob = job;
        enabledForCapture = options.Capture;
        autoQuit = options.AutoQuit || options.Capture;
        width = options.Width;
        height = options.Height;
        fps = options.Fps;

        if (!enabledForCapture)
        {
            return;
        }

        string root = Path.GetFullPath(options.OutputRoot);
        string folderName = BuildJobFolderName(job);
        jobDirectory = Path.Combine(root, folderName);
        framesDirectory = Path.Combine(jobDirectory, "frames");
        Directory.CreateDirectory(framesDirectory);

        DatasetGenerationConfig config = DatasetConfiguration.Load();
        string videoName = "video_" + job.id.ToString("D6", CultureInfo.InvariantCulture) + ".mp4";
        videoRelativePath = CombineRelative(config.videoPathPrefix, videoName);

        Time.captureFramerate = fps;
        Application.targetFrameRate = -1;
        DatasetAnnotationWriter.WriteAll(jobDirectory, videoRelativePath, activeJob);
        WriteManifest(false);
    }

    private void Awake()
    {
        captureCamera = GetComponent<Camera>();
    }

    private void LateUpdate()
    {
        if (!enabledForCapture || finalFrameWritten)
        {
            return;
        }

        CaptureFrame();
        if (sequenceComplete)
        {
            finalFrameWritten = true;
            WriteManifest(true);
            StartCoroutine(FinishAndQuit());
        }
    }

    public void MarkSequenceComplete()
    {
        sequenceComplete = true;
    }

    private void CaptureFrame()
    {
        EnsureBuffers();

        RenderTexture previousActive = RenderTexture.active;
        RenderTexture previousTarget = captureCamera.targetTexture;
        captureCamera.targetTexture = renderTexture;
        captureCamera.Render();

        RenderTexture.active = renderTexture;
        readbackTexture.ReadPixels(new Rect(0, 0, width, height), 0, 0, false);
        readbackTexture.Apply(false, false);

        byte[] bytes = readbackTexture.EncodeToPNG();
        string path = Path.Combine(framesDirectory, "frame_" + frameIndex.ToString("D6") + ".png");
        File.WriteAllBytes(path, bytes);
        frameIndex++;

        captureCamera.targetTexture = previousTarget;
        RenderTexture.active = previousActive;
    }

    private void EnsureBuffers()
    {
        if (renderTexture == null)
        {
            renderTexture = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32)
            {
                name = "CaptureRenderTexture",
                antiAliasing = 1,
                useMipMap = false
            };
            renderTexture.Create();
        }

        if (readbackTexture == null)
        {
            readbackTexture = new Texture2D(width, height, TextureFormat.RGB24, false, false)
            {
                name = "CaptureReadbackTexture"
            };
        }
    }

    private IEnumerator FinishAndQuit()
    {
        yield return null;
        yield return null;

        if (!autoQuit)
        {
            yield break;
        }

#if UNITY_EDITOR
        UnityEditor.EditorApplication.isPlaying = false;
#else
        Application.Quit(0);
#endif
    }

    private void OnDestroy()
    {
        if (renderTexture != null)
        {
            renderTexture.Release();
            Destroy(renderTexture);
        }

        if (readbackTexture != null)
        {
            Destroy(readbackTexture);
        }

        if (enabledForCapture)
        {
            Time.captureFramerate = 0;
        }
    }

    private void WriteManifest(bool completed)
    {
        if (string.IsNullOrEmpty(jobDirectory) || activeJob == null)
        {
            return;
        }

        string jobJson = Indent(JsonUtility.ToJson(activeJob, true), 2);
        string json = "{\n" +
                      "  \"schemaVersion\": \"" + Escape(DatasetBuildInfo.SchemaVersion) + "\",\n" +
                      "  \"completed\": " + (completed ? "true" : "false") + ",\n" +
                      "  \"width\": " + width + ",\n" +
                      "  \"height\": " + height + ",\n" +
                      "  \"fps\": " + fps + ",\n" +
                      "  \"frameCount\": " + frameIndex + ",\n" +
                      "  \"durationSeconds\": " + ChangeBlindnessSequence.TotalDuration.ToString("0.###", CultureInfo.InvariantCulture) + ",\n" +
                      "  \"videoPath\": \"" + Escape(videoRelativePath) + "\",\n" +
                      "  \"timeline\": {\n" +
                      "    \"initialHold\": 8,\n" +
                      "    \"moveAway\": 5,\n" +
                      "    \"awayHold\": 5,\n" +
                      "    \"return\": 5,\n" +
                      "    \"finalHold\": 8,\n" +
                      "    \"swapAt\": 15.5\n" +
                      "  },\n" +
                      "  \"job\": " + jobJson.TrimStart() + "\n" +
                      "}\n";
        File.WriteAllText(Path.Combine(jobDirectory, "manifest.json"), json, new UTF8Encoding(false));
    }

    private static string BuildJobFolderName(BatchJob job)
    {
        return "Batch_" + job.id.ToString("D6", CultureInfo.InvariantCulture) + "_" + Sanitize(job.changeType);
    }

    private static string CombineRelative(string prefix, string fileName)
    {
        string safePrefix = (prefix ?? string.Empty).Replace('\\', '/').Trim('/');
        return string.IsNullOrEmpty(safePrefix) ? fileName : safePrefix + "/" + fileName;
    }

    private static string Sanitize(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "unknown";
        }

        foreach (char invalid in Path.GetInvalidFileNameChars())
        {
            value = value.Replace(invalid, '_');
        }

        return value.Replace(' ', '_');
    }

    private static string Escape(string value)
    {
        return (value ?? string.Empty).Replace("\\", "\\\\").Replace("\"", "\\\"");
    }

    private static string Indent(string value, int spaces)
    {
        string indent = new string(' ', spaces);
        return indent + (value ?? string.Empty).Replace("\n", "\n" + indent);
    }
}
