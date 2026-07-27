using System;
using System.Collections.Generic;

[Serializable]
public sealed class DatasetVideoMetadata
{
    public string change_type;
    public bool change_exists;
    public int view_a_object_count;
    public int view_b_object_count;

    public List<string> view_a_position_a = new List<string>();
    public List<string> view_a_position_b = new List<string>();
    public List<string> view_b_position_a = new List<string>();
    public List<string> view_b_position_b = new List<string>();

    public List<string> view_a_color_a = new List<string>();
    public List<string> view_a_color_b = new List<string>();
    public List<string> view_b_color_a = new List<string>();
    public List<string> view_b_color_b = new List<string>();

    public List<string> changed_positions = new List<string>();
    public bool object_replaced;
    public bool object_added;
    public bool object_removed;
    public bool color_changed;
    public bool position_changed;
    public bool distance_changed;
    public string distance_change = "none";
}

[Serializable]
public sealed class DatasetVideoQaRecord
{
    public string video_id;
    public string video;
    public string video_path;
    public string scene_type = "tabletop";
    public DatasetVideoMetadata metadata = new DatasetVideoMetadata();
    public List<DatasetQaPair> questions = new List<DatasetQaPair>();
}
