using System;
using System.Collections.Generic;

[Serializable]
public sealed class DatasetVideoQaRecord
{
    public string video_id;
    public string video_path;
    public string scene_type = "tabletop";
    public List<DatasetQaPair> questions = new List<DatasetQaPair>();
}
