Shader "ChangeBlindness/PropRecolorLit"
{
    Properties
    {
        _Color ("Dataset Color", Color) = (1,1,1,1)
        _MainTex ("Source Detail", 2D) = "white" {}
        _Metallic ("Metallic", Range(0,1)) = 0
        _Glossiness ("Smoothness", Range(0,1)) = 0.35
    }

    SubShader
    {
        Tags { "RenderType"="Opaque" "Queue"="Geometry" }
        LOD 300

        CGPROGRAM
        #pragma surface surf Standard fullforwardshadows addshadow
        #pragma target 3.0

        sampler2D _MainTex;
        fixed4 _Color;
        half _Metallic;
        half _Glossiness;

        struct Input
        {
            float2 uv_MainTex;
        };

        void surf(Input IN, inout SurfaceOutputStandard output)
        {
            fixed4 source = tex2D(_MainTex, IN.uv_MainTex);
            half luminance = dot(source.rgb, half3(0.299h, 0.587h, 0.114h));

            // Preserve texture shading/details but replace its hue with the dataset color.
            half detail = lerp(0.48h, 1.30h, saturate(luminance));
            output.Albedo = saturate(_Color.rgb * detail);
            output.Metallic = _Metallic;
            output.Smoothness = _Glossiness;
            output.Alpha = 1.0h;
        }
        ENDCG
    }

    FallBack "Diffuse"
}
