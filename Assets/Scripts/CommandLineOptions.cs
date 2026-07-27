using System;
using System.Globalization;

public sealed class CommandLineOptions
{
    public int BatchIndex { get; private set; }
    public int SeedOverride { get; private set; } = int.MinValue;
    public string ForcedChangeType { get; private set; } = string.Empty;
    public string ForcedChangedSlot { get; private set; } = string.Empty;
    public string ModelBundleDirectory { get; private set; } = string.Empty;
    public bool Capture { get; private set; }
    public bool AutoQuit { get; private set; }
    public bool Loop { get; private set; }
    public int Width { get; private set; } = 1920;
    public int Height { get; private set; } = 1080;
    public int Fps { get; private set; } = 30;
    public string OutputRoot { get; private set; } = "Output";

    public static CommandLineOptions Parse(string[] args)
    {
        var options = new CommandLineOptions();
        for (int i = 0; i < args.Length; i++)
        {
            string arg = args[i];
            switch (arg)
            {
                case "--batch-index":
                    options.BatchIndex = ParseInt(Next(args, ref i), 0);
                    break;
                case "--seed":
                    options.SeedOverride = ParseInt(Next(args, ref i), int.MinValue);
                    break;
                case "--change-type":
                    options.ForcedChangeType = (Next(args, ref i) ?? string.Empty).Trim();
                    break;
                case "--changed-slot":
                    options.ForcedChangedSlot = (Next(args, ref i) ?? string.Empty).Trim();
                    break;
                case "--model-bundle-dir":
                    options.ModelBundleDirectory = (Next(args, ref i) ?? string.Empty).Trim();
                    break;
                case "--capture":
                    options.Capture = true;
                    break;
                case "--auto-quit":
                    options.AutoQuit = true;
                    break;
                case "--loop":
                    options.Loop = true;
                    break;
                case "--width":
                    options.Width = Math.Max(320, ParseInt(Next(args, ref i), 1920));
                    break;
                case "--height":
                    options.Height = Math.Max(180, ParseInt(Next(args, ref i), 1080));
                    break;
                case "--fps":
                    options.Fps = Math.Max(1, ParseInt(Next(args, ref i), 30));
                    break;
                case "--output":
                    options.OutputRoot = Next(args, ref i) ?? "Output";
                    break;
            }
        }

        return options;
    }

    private static string Next(string[] args, ref int index)
    {
        int nextIndex = index + 1;
        if (nextIndex >= args.Length)
        {
            return null;
        }

        index = nextIndex;
        return args[nextIndex];
    }

    private static int ParseInt(string value, int fallback)
    {
        if (int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out int parsed))
        {
            return parsed;
        }

        return fallback;
    }
}
