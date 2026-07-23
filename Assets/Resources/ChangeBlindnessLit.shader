Shader "ChangeBlindness/Lit"
{
    Properties
    {
        _Color ("Color", Color) = (1,1,1,1)
        _MainTex ("Albedo", 2D) = "white" {}
        _Metallic ("Metallic", Range(0,1)) = 0
        _Glossiness ("Smoothness", Range(0,1)) = 0.35
        _EmissionColor ("Emission", Color) = (0,0,0,0)
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
        fixed4 _EmissionColor;

        struct Input
        {
            float2 uv_MainTex;
        };

        void surf(Input IN, inout SurfaceOutputStandard output)
        {
            fixed4 albedo = tex2D(_MainTex, IN.uv_MainTex) * _Color;
            output.Albedo = albedo.rgb;
            output.Metallic = _Metallic;
            output.Smoothness = _Glossiness;
            output.Emission = _EmissionColor.rgb;
            output.Alpha = albedo.a;
        }
        ENDCG
    }

    FallBack "Diffuse"
}
