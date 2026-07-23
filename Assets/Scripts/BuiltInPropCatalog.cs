using System;
using System.Collections.Generic;

public static class BuiltInPropCatalog
{
    public static readonly string[] Names =
    {
        "CubeBlock",
        "RectangularBlock",
        "TallBlock",
        "FlatTile",
        "CylinderColumn",
        "ShortCylinder",
        "WideDrum",
        "ConeTower",
        "TruncatedCone",
        "Pyramid",
        "SphereOrb",
        "SmallSphere",
        "CapsulePill",
        "HorizontalCapsule",
        "DonutRing",
        "FlatRing",
        "HexPrism",
        "OctPrism",
        "TriPrism",
        "Diamond",
        "DoubleCone",
        "Hourglass",
        "Mushroom",
        "Dumbbell",
        "CrossBlock",
        "TBlock",
        "LBlock",
        "StepBlock",
        "Arch",
        "UFrame",
        "TripodStand",
        "Snowman",
        "StackCylinders",
        "StackCubes",
        "StarColumn",
        "GearWheel",
        "Bowl",
        "Vase",
        "Bottle",
        "Goblet"
    };

    private static readonly HashSet<string> Lookup =
        new HashSet<string>(Names, StringComparer.OrdinalIgnoreCase);

    public static bool IsBuiltIn(string propName)
    {
        return !string.IsNullOrWhiteSpace(propName) && Lookup.Contains(propName.Trim());
    }

    public static string ResourcePath(string propName)
    {
        return "BuiltInProps/Generated/" + (propName ?? string.Empty).Trim();
    }
}
